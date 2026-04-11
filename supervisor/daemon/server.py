"""Single daemon that manages multiple concurrent supervisor runs.

Listens on a Unix domain socket for register/stop/status requests.
Each run executes in its own thread via run_sidecar().
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import uuid
from pathlib import Path

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.adapters.surface_factory import create_surface
from supervisor.config import RuntimeConfig
from supervisor.global_registry import (
    acquire_pane_lock,
    register_daemon,
    release_pane_lock,
    unregister_daemon,
    update_daemon,
)

logger = logging.getLogger(__name__)

# Default paths — overridden by config in production
DEFAULT_SOCK_PATH = ".supervisor/daemon.sock"
DEFAULT_PID_PATH = ".supervisor/daemon.pid"
DEFAULT_RUNS_DIR = ".supervisor/runtime/runs"

MAX_REQUEST_SIZE = 64 * 1024  # 64KB max request


class RunEntry:
    """Registry entry for one active run."""

    def __init__(self, run_id: str, spec_path: str, pane_target: str,
                 workspace_root: str, thread: threading.Thread, store: StateStore):
        self.run_id = run_id
        self.spec_path = spec_path
        self.pane_target = pane_target
        self.workspace_root = workspace_root
        self.thread = thread
        self.store = store
        self.stop_event = threading.Event()

    def to_dict(self) -> dict:
        state = self._read_state()
        return {
            "run_id": self.run_id,
            "spec_path": self.spec_path,
            "pane_target": self.pane_target,
            "alive": self.thread.is_alive() if self.thread else False,
            "top_state": state.get("top_state", "UNKNOWN") if state else "UNKNOWN",
            "current_node": state.get("current_node_id", "") if state else "",
        }

    def _read_state(self) -> dict | None:
        try:
            return json.loads(self.store.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None


class DaemonServer:
    """Single-process daemon managing multiple supervisor runs."""

    def __init__(self, config: RuntimeConfig | None = None, *,
                 sock_path: str = "", pid_path: str = "", runs_dir: str = ""):
        self.config = config or RuntimeConfig()
        self.sock_path = sock_path or DEFAULT_SOCK_PATH
        self.pid_path = pid_path or DEFAULT_PID_PATH
        self.runs_dir = runs_dir or DEFAULT_RUNS_DIR
        self._runs: dict[str, RunEntry] = {}
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._sock: socket.socket | None = None

    def start(self) -> None:
        """Start the daemon: bind socket, write PID, accept connections."""
        Path(self.runs_dir).mkdir(parents=True, exist_ok=True)

        sock_path = Path(self.sock_path)
        sock_path.unlink(missing_ok=True)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(sock_path))
        self._sock.listen(5)
        self._sock.settimeout(1.0)

        Path(self.pid_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.pid_path).write_text(str(os.getpid()))
        register_daemon(self._daemon_metadata())

        try:
            signal.signal(signal.SIGTERM, self._handle_sigterm)
        except ValueError:
            pass  # not main thread

        logger.info("daemon started (PID %d, socket %s)", os.getpid(), self.sock_path)

        try:
            self._accept_loop()
        finally:
            self._cleanup()

    def _accept_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                self._reap_finished()
                continue
            except OSError:
                break
            try:
                conn.settimeout(10)
                self._handle_connection(conn)
            except Exception:
                logger.exception("error handling connection")
            finally:
                conn.close()

    def _handle_connection(self, conn: socket.socket) -> None:
        data = b""
        while len(data) < MAX_REQUEST_SIZE:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        if len(data) >= MAX_REQUEST_SIZE:
            self._send(conn, {"ok": False, "error": "request too large"})
            return

        try:
            request = json.loads(data.decode("utf-8").strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(conn, {"ok": False, "error": "invalid JSON"})
            return

        action = request.get("action", "")
        if action == "register":
            response = self._do_register(request)
        elif action == "status":
            response = self._do_status()
        elif action == "stop":
            response = self._do_stop(request.get("run_id", ""))
        elif action == "stop_all":
            response = self._do_stop_all()
        elif action == "list_runs":
            response = self._do_list_runs()
        elif action == "observe":
            response = self._do_observe(request.get("run_id", ""))
        elif action == "note_add":
            response = self._do_note_add(request)
        elif action == "note_list":
            response = self._do_note_list(request)
        elif action == "ping":
            response = {"ok": True, "pong": True}
        else:
            response = {"ok": False, "error": f"unknown action: {action}"}

        self._send(conn, response)

    def _do_register(self, request: dict) -> dict:
        spec_path = request.get("spec_path", "")
        pane_target = request.get("pane_target", "")
        workspace_root = request.get("workspace_root", os.getcwd())

        if not spec_path or not pane_target:
            return {"ok": False, "error": "spec_path and pane_target required"}

        try:
            spec = load_spec(spec_path)
        except Exception as e:
            return {"ok": False, "error": f"spec load failed: {e}"}

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        run_dir = str(Path(self.runs_dir) / run_id)
        store = StateStore(run_dir)
        surface_type = request.get("surface_type") or getattr(self.config, "surface_type", "tmux")
        state = store.load_or_init(
            spec, spec_path=spec_path, pane_target=pane_target,
            surface_type=surface_type,
            workspace_root=workspace_root,
        )

        if state.run_id != run_id:
            state.run_id = run_id
            store.save(state)

        entry = RunEntry(run_id, spec_path, pane_target, workspace_root,
                         thread=None, store=store)
        pane_owner = self._pane_owner_metadata(run_id, spec_path, pane_target, workspace_root)

        with self._lock:
            for existing in self._runs.values():
                if existing.pane_target == pane_target and existing.thread and existing.thread.is_alive():
                    return {"ok": False, "error": f"pane {pane_target} already has active run {existing.run_id}"}
            acquired, existing_owner = acquire_pane_lock(pane_target, pane_owner)
            if not acquired:
                return {
                    "ok": False,
                    "error": (
                        f"pane {pane_target} already owned by "
                        f"{existing_owner.get('run_id', '?')} in {existing_owner.get('cwd', '?')}"
                    ),
                }

            thread = threading.Thread(
                target=self._run_worker,
                args=(entry, spec, state),
                name=f"run-{run_id}",
                daemon=True,
            )
            entry.thread = thread
            self._runs[run_id] = entry
            self._update_daemon_record_locked()

        thread.start()
        logger.info("registered run %s: spec=%s pane=%s cwd=%s", run_id, spec_path, pane_target, workspace_root)
        return {"ok": True, "run_id": run_id}

    def _run_worker(self, entry: RunEntry, spec, state) -> None:
        """Worker thread: runs run_sidecar for one run."""
        try:
            surface_type = getattr(self.config, "surface_type", "tmux")
            terminal = create_surface(surface_type, entry.pane_target)
            from supervisor.domain.models import WorkerProfile
            worker = WorkerProfile(
                provider=getattr(self.config, "worker_provider", "unknown"),
                model_name=getattr(self.config, "worker_model", ""),
                trust_level=getattr(self.config, "worker_trust_level", "standard"),
            )
            loop = SupervisorLoop(
                entry.store,
                judge_model=self.config.judge_model,
                judge_temperature=self.config.judge_temperature,
                judge_max_tokens=self.config.judge_max_tokens,
                worker_profile=worker,
            )
            loop.run_sidecar(
                spec, state, terminal,
                poll_interval=self.config.poll_interval_sec,
                read_lines=self.config.read_lines,
                stop_event=entry.stop_event,
            )
            logger.info("run %s finished: %s", entry.run_id, state.top_state.value)
        except Exception:
            logger.exception("run %s crashed", entry.run_id)

    def _do_status(self) -> dict:
        with self._lock:
            runs = [e.to_dict() for e in self._runs.values()]
        return {"ok": True, "runs": runs}

    def _do_stop(self, run_id: str) -> dict:
        with self._lock:
            entry = self._runs.get(run_id)
        if not entry:
            return {"ok": False, "error": f"run {run_id} not found"}
        entry.stop_event.set()
        # Non-blocking: don't wait in the IPC handler thread.
        # Reaper will clean up after thread exits.
        logger.info("stop signal sent to run %s", run_id)
        return {"ok": True}

    def _do_stop_all(self) -> dict:
        with self._lock:
            entries = list(self._runs.values())
        for entry in entries:
            entry.stop_event.set()
        return {"ok": True, "stopped": len(entries)}

    # ------------------------------------------------------------------
    # P0: list + observe
    # ------------------------------------------------------------------

    def _do_list_runs(self) -> dict:
        """List all active runs with detailed state."""
        with self._lock:
            runs = []
            for e in self._runs.values():
                state = e._read_state() or {}
                runs.append({
                    "run_id": e.run_id,
                    "spec_id": state.get("spec_id", ""),
                    "spec_path": e.spec_path,
                    "pane_target": e.pane_target,
                    "workspace": e.workspace_root,
                    "alive": e.thread.is_alive() if e.thread else False,
                    "top_state": state.get("top_state", "UNKNOWN"),
                    "current_node": state.get("current_node_id", ""),
                    "done_nodes": state.get("done_node_ids", []),
                    "current_attempt": state.get("current_attempt", 0),
                })
        return {"ok": True, "runs": runs}

    def _do_observe(self, run_id: str) -> dict:
        """Read-only observation of a specific run's state + recent events."""
        with self._lock:
            entry = self._runs.get(run_id)
        if not entry:
            return {"ok": False, "error": f"run {run_id} not found"}

        state = entry._read_state() or {}
        recent: list[dict] = []
        try:
            if entry.store.session_log_path.exists():
                lines = entry.store.session_log_path.read_text().strip().splitlines()[-5:]
                for line in lines:
                    try:
                        recent.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

        return {
            "ok": True,
            "run_id": run_id,
            "state": state,
            "recent_events": recent,
        }

    # ------------------------------------------------------------------
    # P1: shared notes
    # ------------------------------------------------------------------

    def _shared_notes_path(self) -> Path:
        p = Path(self.runs_dir).parent / "shared"
        p.mkdir(parents=True, exist_ok=True)
        return p / "notes.jsonl"

    def _do_note_add(self, request: dict) -> dict:
        """Add a shared note."""
        content = request.get("content", "")
        if not content:
            return {"ok": False, "error": "content required"}

        note = {
            "note_id": f"note_{uuid.uuid4().hex[:12]}",
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "author_run_id": request.get("author_run_id", "human"),
            "note_type": request.get("note_type", "context"),
            "title": request.get("title", content[:80]),
            "content": content,
        }

        path = self._shared_notes_path()
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(note, ensure_ascii=False) + "\n")

        logger.info("note added: %s by %s", note["note_id"], note["author_run_id"])
        return {"ok": True, "note_id": note["note_id"]}

    def _do_note_list(self, request: dict) -> dict:
        """List shared notes, optionally filtered by type or run."""
        path = self._shared_notes_path()
        if not path.exists():
            return {"ok": True, "notes": []}

        filter_type = request.get("note_type")
        filter_run = request.get("run_id")
        limit = request.get("limit", 20)

        notes: list[dict] = []
        try:
            for line in path.read_text().strip().splitlines():
                try:
                    note = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if filter_type and note.get("note_type") != filter_type:
                    continue
                if filter_run and note.get("author_run_id") != filter_run:
                    continue
                notes.append(note)
        except OSError:
            pass

        # Return most recent first, up to limit
        return {"ok": True, "notes": notes[-limit:][::-1]}

    def _reap_finished(self) -> None:
        """Remove completed/stopped runs from registry.

        Two-phase: collect candidates under lock, then join outside lock
        to avoid blocking IPC while waiting for threads.
        """
        # Phase 1: identify candidates (under lock, fast)
        with self._lock:
            candidates = [
                (rid, e) for rid, e in self._runs.items()
                if not e.thread.is_alive() or e.stop_event.is_set()
            ]

        if not candidates:
            return

        # Phase 2: join threads outside lock (may block briefly)
        reaped = []
        for rid, e in candidates:
            if e.thread.is_alive():
                e.thread.join(timeout=2)
                if e.thread.is_alive():
                    continue  # still alive — skip, don't create zombie
            release_pane_lock(e.pane_target, e.run_id)
            reaped.append(rid)

        # Phase 3: remove from registry (under lock)
        if reaped:
            with self._lock:
                for rid in reaped:
                    self._runs.pop(rid, None)
                self._update_daemon_record_locked()
            logger.info("reaped %d finished run(s)", len(reaped))

    def _handle_sigterm(self, signum, frame):
        logger.info("SIGTERM received, shutting down")
        self._shutdown.set()

    def _cleanup(self) -> None:
        self._do_stop_all()
        # Wait for threads to finish (with timeout)
        with self._lock:
            entries = list(self._runs.values())
            threads = [e.thread for e in entries if e.thread]
        for t in threads:
            t.join(timeout=5)
        for entry in entries:
            release_pane_lock(entry.pane_target, entry.run_id)
        if self._sock:
            self._sock.close()
        Path(self.sock_path).unlink(missing_ok=True)
        Path(self.pid_path).unlink(missing_ok=True)
        unregister_daemon(self.sock_path)
        logger.info("daemon stopped")

    @staticmethod
    def _send(conn: socket.socket, data: dict) -> None:
        conn.sendall((json.dumps(data) + "\n").encode("utf-8"))

    def _daemon_metadata(self) -> dict:
        return {
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "socket": self.sock_path,
            "started_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "active_runs": len(self._runs),
        }

    def _pane_owner_metadata(self, run_id: str, spec_path: str,
                             pane_target: str, workspace_root: str) -> dict:
        return {
            "pid": os.getpid(),
            "cwd": workspace_root or os.getcwd(),
            "socket": self.sock_path,
            "run_id": run_id,
            "pane_target": pane_target,
            "spec_path": spec_path,
        }

    def _update_daemon_record_locked(self) -> None:
        update_daemon(
            self.sock_path,
            pid=os.getpid(),
            cwd=os.getcwd(),
            active_runs=len(self._runs),
        )
