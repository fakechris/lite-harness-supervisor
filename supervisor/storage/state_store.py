from __future__ import annotations
import hashlib
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from supervisor.domain.enums import TopState
from supervisor.domain.models import Session, SupervisorState, WorkflowSpec


class StateStore:
    def __init__(self, runtime_dir: str = "runtime", *, runtime_root: str | None = None):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        if runtime_root is not None:
            self.runtime_root = Path(runtime_root)
        elif self.runtime_dir.parent.name == "runs":
            self.runtime_root = self.runtime_dir.parent.parent
        else:
            self.runtime_root = self.runtime_dir
        self.state_path = self.runtime_dir / "state.json"
        self.event_log_path = self.runtime_dir / "event_log.jsonl"
        self.decision_log_path = self.runtime_dir / "decision_log.jsonl"
        self.session_log_path = self.runtime_dir / "session_log.jsonl"
        self._session_seq = 0
        self._seq_lock = threading.Lock()

    def load_or_init(
        self, spec: WorkflowSpec, *,
        spec_path: str = "",
        pane_target: str = "",
        surface_type: str = "tmux",
        workspace_root: str = "",
        controller_mode: str = "",
    ) -> SupervisorState:
        spec_hash = self._hash_spec(spec_path) if spec_path else ""

        if self.state_path.exists():
            try:
                state = SupervisorState.from_dict(json.loads(self.state_path.read_text()))
            except (json.JSONDecodeError, KeyError, ValueError):
                # Corrupt state file — archive and start fresh
                self._archive_state("corrupt")
                state = None
            else:
                # Resume validation: check consistency
                if state.spec_id != spec.id or (spec_hash and state.spec_hash and state.spec_hash != spec_hash):
                    self._archive_state(state.run_id)
                elif pane_target and state.pane_target and state.pane_target != pane_target:
                    self._archive_state(state.run_id)
                elif surface_type and state.surface_type and state.surface_type != surface_type:
                    self._archive_state(state.run_id)
                else:
                    dirty = False
                    if controller_mode and getattr(state, "controller_mode", "") != controller_mode:
                        state.controller_mode = controller_mode
                        dirty = True
                    # Legacy runs (pre-session_id) get backfilled so the
                    # rest of the system can rely on the invariant that
                    # every run carries a session_id.
                    if not state.session_id:
                        state.session_id = self._resolve_session_id(
                            workspace_root=state.workspace_root or workspace_root or os.getcwd(),
                            spec_id=state.spec_id,
                        )
                        dirty = True
                    if dirty:
                        self.save(state)
                    self._session_seq = self._read_last_seq()
                    return state

        resolved_workspace_root = workspace_root or os.getcwd()
        session_id = self._resolve_session_id(
            workspace_root=resolved_workspace_root, spec_id=spec.id
        )

        state = SupervisorState(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            spec_id=spec.id,
            mode=spec.kind,
            top_state=TopState.READY,
            current_node_id=spec.first_node_id(),
            spec_path=spec_path,
            spec_hash=spec_hash,
            pane_target=pane_target,
            surface_type=surface_type,
            workspace_root=resolved_workspace_root,
            controller_mode=controller_mode or "daemon",
            session_id=session_id,
        )
        state.retry_budget.per_node = spec.policy.max_retries_per_node
        state.retry_budget.global_limit = spec.policy.max_retries_global
        self.save(state)
        return state

    def _resolve_session_id(self, *, workspace_root: str, spec_id: str) -> str:
        """Adopt an existing active session for (workspace_root, spec_id),
        else create and record a new one."""
        existing = self.find_session_by_attachment(
            workspace_root=workspace_root, spec_id=spec_id
        )
        if existing is not None:
            return existing.session_id
        session = Session(workspace_root=workspace_root, spec_id=spec_id)
        self.save_session(session)
        return session.session_id

    def save(self, state: SupervisorState) -> None:
        """Atomic write: write to temp file then rename."""
        data = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.runtime_dir), suffix=".tmp", prefix="state."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp_path, str(self.state_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def append_event(self, event: dict) -> None:
        with self.event_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_decision(self, decision: dict) -> None:
        with self.decision_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision, ensure_ascii=False) + "\n")

    def append_session_event(self, run_id: str, event_type: str, payload: dict) -> None:
        """Append to the durable session log (append-only)."""
        with self._seq_lock:
            self._session_seq += 1
            seq = self._session_seq
        record = {
            "run_id": run_id,
            "seq": seq,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        with self.session_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def next_checkpoint_seq(self) -> int:
        with self._seq_lock:
            self._session_seq += 1
            return self._session_seq

    def _archive_state(self, label: str) -> None:
        if self.state_path.exists():
            archive = self.runtime_dir / f"state.{label}.json"
            self.state_path.rename(archive)

    def _read_last_seq(self) -> int:
        if not self.session_log_path.exists():
            return 0
        try:
            lines = self._tail_lines(self.session_log_path, max_lines=256)
        except OSError:
            return 0
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            seq = record.get("seq", 0)
            if isinstance(seq, int):
                return seq
        return 0

    # ------------------------------------------------------------------
    # Session (cross-run logical entity) — stored under shared/sessions.jsonl
    # ------------------------------------------------------------------

    @property
    def sessions_path(self) -> Path:
        return self.runtime_root / "shared" / "sessions.jsonl"

    def save_session(self, session: Session) -> None:
        """Append a session record. Latest line wins for a given session_id.

        Append-only preserves an audit trail of status transitions; queries
        fold the log to derive the current state per session_id.
        """
        session.updated_at = datetime.now(timezone.utc).isoformat()
        path = self.sessions_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(session.to_dict(), ensure_ascii=False) + "\n")

    def load_session(self, session_id: str) -> Session | None:
        path = self.sessions_path
        if not path.exists():
            return None
        latest: dict | None = None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("session_id") == session_id:
                    latest = record
        except OSError:
            return None
        return Session.from_dict(latest) if latest else None

    def list_sessions(self, *, status: str = "") -> list[Session]:
        """Return one Session per session_id (latest record wins).

        Optional *status* filter (e.g. "active"). Records with missing or
        malformed session_id are skipped.
        """
        path = self.sessions_path
        if not path.exists():
            return []
        by_id: dict[str, dict] = {}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = record.get("session_id")
                if not sid:
                    continue
                by_id[sid] = record
        except OSError:
            return []
        sessions = [Session.from_dict(r) for r in by_id.values()]
        if status:
            sessions = [s for s in sessions if s.status == status]
        return sessions

    def find_session_by_attachment(
        self, *, workspace_root: str, spec_id: str
    ) -> Session | None:
        """Find an active session matching (workspace_root, spec_id).

        Used at run registration (Task 1b) to decide adoption vs. new session.
        Returns None if no open session matches.
        """
        for session in self.list_sessions(status="active"):
            if session.workspace_root == workspace_root and session.spec_id == spec_id:
                return session
        return None

    def load_raw(self) -> dict | None:
        """Load state as a raw dict without constructing SupervisorState."""
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def read_recent_session_events(self, count: int = 5) -> list[dict]:
        """Read the last N session events."""
        if not self.session_log_path.exists():
            return []
        try:
            lines = self._tail_lines(self.session_log_path, max_lines=count)
            return [json.loads(line) for line in lines if line.strip()]
        except (OSError, json.JSONDecodeError):
            return []

    def session_event_count(self) -> int:
        """Count total session events (approximate for large files)."""
        if not self.session_log_path.exists():
            return 0
        try:
            return sum(1 for _ in self.session_log_path.open("r", encoding="utf-8"))
        except OSError:
            return 0

    @staticmethod
    def _hash_spec(path: str) -> str:
        try:
            content = Path(path).read_bytes()
            return hashlib.sha256(content).hexdigest()[:16]
        except (OSError, FileNotFoundError):
            return ""

    @staticmethod
    def _tail_lines(path: Path, *, max_lines: int = 256, chunk_size: int = 4096) -> list[str]:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            buffer = b""
            pos = size
            while pos > 0 and buffer.count(b"\n") <= max_lines:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                buffer = f.read(read_size) + buffer
            return buffer.decode("utf-8", errors="replace").splitlines()[-max_lines:]
