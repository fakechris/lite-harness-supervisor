"""Client for communicating with the supervisor daemon via Unix socket."""
from __future__ import annotations

import json
import socket
from pathlib import Path

SOCK_PATH = ".supervisor/daemon.sock"
PID_PATH = ".supervisor/daemon.pid"


class DaemonClient:
    """Connects to the supervisor daemon and sends JSON requests."""

    def __init__(self, sock_path: str = SOCK_PATH):
        self.sock_path = sock_path

    def is_running(self) -> bool:
        """Check if daemon is reachable."""
        try:
            resp = self._request({"action": "ping"})
            return resp.get("pong", False)
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return False

    def register(self, spec_path: str, pane_target: str, *,
                 workspace_root: str = "", surface_type: str = "") -> dict:
        """Register a new run with the daemon."""
        req: dict = {
            "action": "register",
            "spec_path": spec_path,
            "pane_target": pane_target,
        }
        if workspace_root:
            req["workspace_root"] = workspace_root
        if surface_type:
            req["surface_type"] = surface_type
        return self._request(req)

    def status(self) -> dict:
        """Get status of all runs."""
        return self._request({"action": "status"})

    def stop_run(self, run_id: str) -> dict:
        """Stop a specific run."""
        return self._request({"action": "stop", "run_id": run_id})

    def stop_all(self) -> dict:
        """Stop all runs."""
        return self._request({"action": "stop_all"})

    def resume(self, spec_path: str, pane_target: str, *,
               surface_type: str = "") -> dict:
        """Resume a paused or crashed run."""
        req: dict = {
            "action": "resume",
            "spec_path": spec_path,
            "pane_target": pane_target,
        }
        if surface_type:
            req["surface_type"] = surface_type
        return self._request(req)

    def ack_review(self, run_id: str, *, reviewer: str) -> dict:
        """Record reviewer acknowledgement for a run."""
        return self._request({
            "action": "ack_review",
            "run_id": run_id,
            "reviewer": reviewer,
        })

    def list_runs(self) -> dict:
        """List all active runs with detailed state."""
        return self._request({"action": "list_runs"})

    def observe(self, run_id: str) -> dict:
        """Read-only observation of a specific run."""
        return self._request({"action": "observe", "run_id": run_id})

    def note_add(self, content: str, *, note_type: str = "context",
                 author_run_id: str = "human", title: str = "") -> dict:
        """Add a shared note."""
        return self._request({
            "action": "note_add",
            "content": content,
            "note_type": note_type,
            "author_run_id": author_run_id,
            "title": title,
        })

    def note_list(self, *, note_type: str = "", run_id: str = "",
                  limit: int = 20) -> dict:
        """List shared notes."""
        req: dict = {"action": "note_list", "limit": limit}
        if note_type:
            req["note_type"] = note_type
        if run_id:
            req["run_id"] = run_id
        return self._request(req)

    def _request(self, data: dict) -> dict:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(self.sock_path)
            sock.sendall((json.dumps(data) + "\n").encode("utf-8"))
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            return json.loads(response.decode("utf-8").strip())
        finally:
            sock.close()

    @staticmethod
    def daemon_pid() -> int | None:
        """Read daemon PID from file, or None if not found."""
        try:
            return int(Path(PID_PATH).read_text().strip())
        except (OSError, ValueError):
            return None
