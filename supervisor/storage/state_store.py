from __future__ import annotations
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from supervisor.domain.enums import TopState
from supervisor.domain.models import SupervisorState, WorkflowSpec


class StateStore:
    def __init__(self, runtime_dir: str = "runtime"):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.event_log_path = self.runtime_dir / "event_log.jsonl"
        self.decision_log_path = self.runtime_dir / "decision_log.jsonl"
        self.session_log_path = self.runtime_dir / "session_log.jsonl"
        self._session_seq = 0

    def load_or_init(
        self, spec: WorkflowSpec, *,
        spec_path: str = "",
        pane_target: str = "",
        workspace_root: str = "",
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
                else:
                    self._session_seq = self._read_last_seq()
                    return state

        state = SupervisorState(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            spec_id=spec.id,
            mode=spec.kind,
            top_state=TopState.READY,
            current_node_id=spec.first_node_id(),
            spec_path=spec_path,
            spec_hash=spec_hash,
            pane_target=pane_target,
            workspace_root=workspace_root or os.getcwd(),
        )
        state.retry_budget.per_node = spec.policy.max_retries_per_node
        state.retry_budget.global_limit = spec.policy.max_retries_global
        self.save(state)
        return state

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
        self._session_seq += 1
        record = {
            "run_id": run_id,
            "seq": self._session_seq,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        with self.session_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def next_checkpoint_seq(self) -> int:
        self._session_seq += 1
        return self._session_seq

    def _archive_state(self, label: str) -> None:
        if self.state_path.exists():
            archive = self.runtime_dir / f"state.{label}.json"
            self.state_path.rename(archive)

    def _read_last_seq(self) -> int:
        if not self.session_log_path.exists():
            return 0
        last_seq = 0
        try:
            for line in self.session_log_path.read_text().strip().splitlines():
                try:
                    record = json.loads(line)
                    last_seq = max(last_seq, record.get("seq", 0))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return last_seq

    @staticmethod
    def _hash_spec(path: str) -> str:
        try:
            content = Path(path).read_bytes()
            return hashlib.sha256(content).hexdigest()[:16]
        except (OSError, FileNotFoundError):
            return ""
