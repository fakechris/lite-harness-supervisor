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
from supervisor.terminal.adapter import TerminalAdapter
from supervisor.config import RuntimeConfig

logger = logging.getLogger(__name__)

SOCK_PATH = ".supervisor/daemon.sock"
PID_PATH = ".supervisor/daemon.pid"
RUNS_DIR = ".supervisor/runtime/runs"


class RunEntry:
    """Registry entry for one active run."""

    def __init__(self, run_id: str, spec_path: str, pane_target: str,
                 thread: threading.Thread, store: StateStore):
        self.run_id = run_id
        self.spec_path = spec_path
        self.pane_target = pane_target
        self.thread = thread
        self.store = store
        self.stop_event = threading.Event()

    def to_dict(self) -> dict:
        state = self._read_state()
        return {
            "run_id": self.run_id,
            "spec_path": self.spec_path,
            "pane_target": self.pane_target,
            "alive": self.thread.is_alive(),
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

    def __init__(self, config: RuntimeConfig | None = None):
        self.config = config or RuntimeConfig()
        self._runs: dict[str, RunEntry] = {}
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._sock: socket.socket | None = None

    def start(self) -> None:
        """Start the daemon: bind socket, write PID, accept connections."""
        Path(RUNS_DIR).mkdir(parents=True, exist_ok=True)

        # Clean stale socket
        sock_path = Path(SOCK_PATH)
        sock_path.unlink(missing_ok=True)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(sock_path))
        self._sock.listen(5)
        self._sock.settimeout(1.0)  # allow periodic shutdown check

        # Write PID
        Path(PID_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(PID_PATH).write_text(str(os.getpid()))

        # SIGTERM handler (only in main thread)
        try:
            signal.signal(signal.SIGTERM, self._handle_sigterm)
        except ValueError:
            pass  # not main thread (e.g., in tests)

        logger.info("daemon started (PID %d, socket %s)", os.getpid(), SOCK_PATH)

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
                self._handle_connection(conn)
            except Exception:
                logger.exception("error handling connection")
            finally:
                conn.close()

    def _handle_connection(self, conn: socket.socket) -> None:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

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
        elif action == "ping":
            response = {"ok": True, "pong": True}
        else:
            response = {"ok": False, "error": f"unknown action: {action}"}

        self._send(conn, response)

    def _do_register(self, request: dict) -> dict:
        spec_path = request.get("spec_path", "")
        pane_target = request.get("pane_target", "")

        if not spec_path or not pane_target:
            return {"ok": False, "error": "spec_path and pane_target required"}

        try:
            spec = load_spec(spec_path)
        except Exception as e:
            return {"ok": False, "error": f"spec load failed: {e}"}

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        run_dir = str(Path(RUNS_DIR) / run_id)
        store = StateStore(run_dir)
        state = store.load_or_init(
            spec, spec_path=spec_path, pane_target=pane_target,
            workspace_root=os.getcwd(),
        )

        if state.run_id != run_id:
            state.run_id = run_id
            store.save(state)

        entry = RunEntry(run_id, spec_path, pane_target, thread=None, store=store)

        # Hold lock across duplicate check AND registry insertion (no race)
        with self._lock:
            for existing in self._runs.values():
                if existing.pane_target == pane_target and existing.thread.is_alive():
                    return {"ok": False, "error": f"pane {pane_target} already has active run {existing.run_id}"}

            thread = threading.Thread(
                target=self._run_worker,
                args=(entry, spec, state),
                name=f"run-{run_id}",
                daemon=True,
            )
            entry.thread = thread
            self._runs[run_id] = entry

        thread.start()
        logger.info("registered run %s: spec=%s pane=%s", run_id, spec_path, pane_target)
        return {"ok": True, "run_id": run_id}

    def _run_worker(self, entry: RunEntry, spec, state) -> None:
        """Worker thread: runs run_sidecar for one run."""
        try:
            terminal = TerminalAdapter(entry.pane_target)
            loop = SupervisorLoop(
                entry.store,
                judge_model=self.config.judge_model,
                judge_temperature=self.config.judge_temperature,
                judge_max_tokens=self.config.judge_max_tokens,
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
        # The run_sidecar loop checks interrupted_ref which we can't directly
        # reach from here. But the thread is daemon=True, so it dies with the
        # process. For graceful stop, we save state and remove from registry.
        state = entry._read_state()
        if state and state.get("top_state") not in ("COMPLETED", "FAILED", "ABORTED"):
            # Mark as paused
            try:
                full_state = json.loads(entry.store.state_path.read_text())
                full_state["top_state"] = "PAUSED_FOR_HUMAN"
                entry.store.state_path.write_text(json.dumps(full_state, indent=2))
            except Exception:
                pass
        with self._lock:
            self._runs.pop(run_id, None)
        logger.info("stopped run %s", run_id)
        return {"ok": True}

    def _do_stop_all(self) -> dict:
        with self._lock:
            run_ids = list(self._runs.keys())
        for rid in run_ids:
            self._do_stop(rid)
        return {"ok": True, "stopped": len(run_ids)}

    def _reap_finished(self) -> None:
        """Remove completed runs from registry."""
        with self._lock:
            finished = [rid for rid, e in self._runs.items() if not e.thread.is_alive()]
            for rid in finished:
                del self._runs[rid]

    def _handle_sigterm(self, signum, frame):
        logger.info("SIGTERM received, shutting down")
        self._shutdown.set()

    def _cleanup(self) -> None:
        self._do_stop_all()
        if self._sock:
            self._sock.close()
        Path(SOCK_PATH).unlink(missing_ok=True)
        Path(PID_PATH).unlink(missing_ok=True)
        logger.info("daemon stopped")

    @staticmethod
    def _send(conn: socket.socket, data: dict) -> None:
        conn.sendall((json.dumps(data) + "\n").encode("utf-8"))
