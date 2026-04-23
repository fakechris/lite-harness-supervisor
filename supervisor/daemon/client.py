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
                 author_run_id: str = "human", target_run_id: str = "",
                 title: str = "", metadata: dict | None = None) -> dict:
        """Add a shared note, optionally scoped to a target run."""
        req: dict = {
            "action": "note_add",
            "content": content,
            "note_type": note_type,
            "author_run_id": author_run_id,
            "title": title,
            "metadata": metadata or {},
        }
        if target_run_id:
            req["target_run_id"] = target_run_id
        return self._request(req)

    def note_list(self, *, note_type: str = "", run_id: str = "",
                  target_run_id: str = "", limit: int = 20) -> dict:
        """List shared notes, optionally filtered by target run."""
        req: dict = {"action": "note_list", "limit": limit}
        if note_type:
            req["note_type"] = note_type
        if run_id:
            req["run_id"] = run_id
        if target_run_id:
            req["target_run_id"] = target_run_id
        return self._request(req)

    # ------------------------------------------------------------------
    # Operator channel APIs
    # ------------------------------------------------------------------

    def get_snapshot(self, run_id: str) -> dict:
        """Get canonical RunSnapshot for a run."""
        return self._request({"action": "get_snapshot", "run_id": run_id})

    def get_timeline(self, run_id: str, *, limit: int = 20, since_seq: int = 0) -> dict:
        """Get RunTimelineEvents for a run."""
        return self._request({
            "action": "get_timeline",
            "run_id": run_id,
            "limit": limit,
            "since_seq": since_seq,
        })

    def get_exchange(self, run_id: str) -> dict:
        """Get recent exchange summary."""
        return self._request({"action": "get_exchange", "run_id": run_id})

    def explain_run(self, run_id: str, *, language: str = "en") -> dict:
        """Submit async explain_run job. Returns {ok, job_id}."""
        return self._request({
            "action": "explain_run",
            "run_id": run_id,
            "language": language,
        })

    def explain_exchange(self, run_id: str, *, language: str = "en") -> dict:
        """Submit async explain_exchange job. Returns {ok, job_id}."""
        return self._request({
            "action": "explain_exchange",
            "run_id": run_id,
            "language": language,
        })

    def assess_drift(self, run_id: str, *, language: str = "en") -> dict:
        """Submit async assess_drift job. Returns {ok, job_id}."""
        return self._request({
            "action": "assess_drift",
            "run_id": run_id,
            "language": language,
        })

    def request_clarification(self, run_id: str, question: str, *,
                              language: str = "en") -> dict:
        """Submit async clarification request. Returns {ok, job_id}."""
        return self._request({
            "action": "request_clarification",
            "run_id": run_id,
            "question": question,
            "language": language,
        })

    def escalate_clarification(
        self,
        run_id: str,
        question: str,
        *,
        language: str = "en",
        reason: str = "operator_initiated",
        operator: str = "",
        confidence: float | None = None,
    ) -> dict:
        """Record an operator's decision to escalate a clarification.

        Audit-only in 0.3.7; the actual side-instruction transport to the
        worker is wired in 0.3.8. Returns ``{ok, escalation_id}``.
        """
        return self._request({
            "action": "escalate_clarification",
            "run_id": run_id,
            "question": question,
            "language": language,
            "reason": reason,
            "operator": operator,
            "confidence": confidence,
        })

    def get_job(self, job_id: str) -> dict:
        """Poll for async job result."""
        return self._request({"action": "get_job", "job_id": job_id})

    # ------------------------------------------------------------------
    # Event plane (Task 3): external task request / result / mailbox
    # ------------------------------------------------------------------

    def external_task_create(
        self,
        *,
        session_id: str,
        provider: str,
        target_ref: str,
        run_id: str = "",
        phase: str = "execute",
        task_kind: str = "review",
        blocking_policy: str = "notify_only",
        deadline_at: str = "",
        resume_policy: str = "",
    ) -> dict:
        """Register an external task request; creates an associated SessionWait."""
        req: dict = {
            "action": "external_task_create",
            "session_id": session_id,
            "provider": provider,
            "target_ref": target_ref,
            "phase": phase,
            "task_kind": task_kind,
            "blocking_policy": blocking_policy,
        }
        if run_id:
            req["run_id"] = run_id
        if deadline_at:
            req["deadline_at"] = deadline_at
        if resume_policy:
            req["resume_policy"] = resume_policy
        return self._request(req)

    def external_result_ingest(
        self,
        *,
        request_id: str,
        provider: str,
        result_kind: str,
        summary: str = "",
        payload: dict | None = None,
        run_id: str = "",
        idempotency_key: str = "",
    ) -> dict:
        """Ingest an external task result; resolves its wait + creates a mailbox item."""
        req: dict = {
            "action": "external_result_ingest",
            "request_id": request_id,
            "provider": provider,
            "result_kind": result_kind,
            "summary": summary,
            "payload": payload or {},
        }
        if run_id:
            req["run_id"] = run_id
        if idempotency_key:
            req["idempotency_key"] = idempotency_key
        return self._request(req)

    def mailbox_list(self, *, session_id: str, delivery_status: str = "") -> dict:
        """List mailbox items for a session, optionally filtered by delivery_status."""
        req: dict = {"action": "mailbox_list", "session_id": session_id}
        if delivery_status:
            req["delivery_status"] = delivery_status
        return self._request(req)

    def mailbox_ack(self, *, mailbox_item_id: str, delivery_status: str = "acknowledged") -> dict:
        """Transition a mailbox item to a new delivery_status."""
        return self._request({
            "action": "mailbox_ack",
            "mailbox_item_id": mailbox_item_id,
            "delivery_status": delivery_status,
        })

    def waits_list(self, *, session_id: str = "") -> dict:
        """List open session waits, optionally scoped to a session."""
        req: dict = {"action": "waits_list"}
        if session_id:
            req["session_id"] = session_id
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
