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
import time
import uuid
from pathlib import Path

from supervisor.domain.enums import DeliveryState, TopState
from supervisor.domain.models import SupervisorState
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.gates.finish_gate import FinishGate
from supervisor.domain.state_machine import FINAL_STATES, transition_top_state
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
from supervisor.interventions import AutoInterventionManager
from supervisor.notifications import NotificationManager
from supervisor.pause_summary import summarize_state
from supervisor.spec_approval import load_runnable_spec

logger = logging.getLogger(__name__)

# Default paths — overridden by config in production
DEFAULT_SOCK_PATH = ".supervisor/daemon.sock"
DEFAULT_PID_PATH = ".supervisor/daemon.pid"
DEFAULT_RUNS_DIR = ".supervisor/runtime/runs"

MAX_REQUEST_SIZE = 64 * 1024  # 64KB max request
RECOVERABLE_ORPHANED_STATES = {
    TopState.RUNNING,
    TopState.GATING,
    TopState.VERIFYING,
}


class RunEntry:
    """Registry entry for one active run."""

    def __init__(self, run_id: str, spec_path: str, pane_target: str,
                 workspace_root: str, surface_type: str, thread: threading.Thread, store: StateStore):
        self.run_id = run_id
        self.spec_path = spec_path
        self.pane_target = pane_target
        self.workspace_root = workspace_root
        self.surface_type = surface_type
        self.thread = thread
        self.store = store
        self.stop_event = threading.Event()

    def to_dict(self) -> dict:
        state = self._read_state()
        summary = summarize_state(state or {}) if state else {}
        return {
            "run_id": self.run_id,
            "spec_path": self.spec_path,
            "pane_target": self.pane_target,
            "alive": self.thread.is_alive() if self.thread else False,
            "top_state": state.get("top_state", "UNKNOWN") if state else "UNKNOWN",
            "current_node": state.get("current_node_id", "") if state else "",
            "status_reason": summary.get("status_reason", ""),
            "pause_reason": summary.get("pause_reason", ""),
            "next_action": summary.get("next_action", ""),
        }

    def _read_state(self) -> dict | None:
        try:
            return json.loads(self.store.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None


DEFAULT_IDLE_SHUTDOWN_SEC = 600  # 10 minutes


class DaemonServer:
    """Single-process daemon managing multiple supervisor runs."""

    def __init__(self, config: RuntimeConfig | None = None, *,
                 sock_path: str = "", pid_path: str = "", runs_dir: str = "",
                 idle_shutdown_sec: int | None = None):
        self.config = config or RuntimeConfig()
        self.sock_path = sock_path or DEFAULT_SOCK_PATH
        self.pid_path = pid_path or DEFAULT_PID_PATH
        self.runs_dir = runs_dir or DEFAULT_RUNS_DIR
        self.idle_shutdown_sec = idle_shutdown_sec if idle_shutdown_sec is not None else DEFAULT_IDLE_SHUTDOWN_SEC
        self._runs: dict[str, RunEntry] = {}
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._sock: socket.socket | None = None
        self._started_at = time.time()
        self._last_run_finished_at: float = 0
        self._last_client_contact_at: float = time.time()

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
        recovered = self._recover_orphaned_runs()
        if recovered:
            logger.warning("recovered %d orphaned persisted run(s) into PAUSED_FOR_HUMAN", recovered)

        try:
            signal.signal(signal.SIGTERM, self._handle_sigterm)
        except ValueError:
            pass  # not main thread

        logger.info("daemon started (PID %d, socket %s)", os.getpid(), self.sock_path)

        try:
            self._accept_loop()
        finally:
            self._cleanup()

    def _recover_orphaned_runs(self) -> int:
        runs_dir = Path(self.runs_dir)
        if not runs_dir.exists():
            return 0

        recovered = 0
        for run_dir in sorted(runs_dir.iterdir()):
            state_path = run_dir / "state.json"
            if not state_path.exists():
                continue
            try:
                state_data = json.loads(state_path.read_text())
                state = SupervisorState.from_dict(state_data)
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                continue

            if state.top_state not in RECOVERABLE_ORPHANED_STATES:
                continue
            if getattr(state, "controller_mode", "daemon") == "foreground":
                continue

            previous_top_state = state_data.get("top_state", state.top_state.value)
            delivery = state_data.get("delivery_state", DeliveryState.IDLE)
            transition_top_state(state, TopState.PAUSED_FOR_HUMAN, reason="daemon startup orphan recovery")
            if delivery in (DeliveryState.INJECTED, DeliveryState.SUBMITTED):
                recovery_detail = (
                    f"daemon restarted while delivery was in progress "
                    f"(delivery_state={delivery}, top_state={previous_top_state}); "
                    "instruction may not have been processed — explicit resume required"
                )
            elif delivery == DeliveryState.TIMED_OUT:
                recovery_detail = (
                    f"daemon restarted after delivery timeout "
                    f"(top_state={previous_top_state}); "
                    "check agent state before resuming"
                )
            else:
                recovery_detail = (
                    f"daemon restarted while the run was in progress "
                    f"({previous_top_state}); "
                    "explicit resume is required"
                )
            state.delivery_state = DeliveryState.IDLE
            state.human_escalations.append({
                "reason": recovery_detail,
                "orphaned_from": previous_top_state,
                "delivery_state_at_crash": delivery,
            })
            store = StateStore(str(run_dir))
            store._session_seq = store._read_last_seq()
            store.append_session_event(
                state.run_id,
                "orphaned_run_recovered",
                {
                    "previous_top_state": previous_top_state,
                    "delivery_state_at_crash": delivery,
                    "reason": state.human_escalations[-1]["reason"],
                },
            )
            store.save(state)
            recovered += 1

        return recovered

    def _accept_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                self._reap_finished()
                self._refresh_idle_state()
                self._check_idle_shutdown()
                continue
            except OSError:
                break
            self._last_client_contact_at = time.time()
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
        elif action == "resume":
            response = self._do_resume(request)
        elif action == "ack_review":
            response = self._do_ack_review(request)
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
            spec = load_runnable_spec(spec_path)
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
            controller_mode="daemon",
        )

        if state.run_id != run_id:
            state.run_id = run_id
            store.save(state)

        entry = RunEntry(run_id, spec_path, pane_target, workspace_root,
                         surface_type=surface_type, thread=None, store=store)
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
            terminal = create_surface(entry.surface_type, entry.pane_target)
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
                notification_manager=NotificationManager.from_config(
                    self.config,
                    runtime_root=entry.store.runtime_root,
                ),
                auto_intervention_manager=AutoInterventionManager(
                    mode=self.config.pause_handling_mode,
                    max_auto_interventions=self.config.max_auto_interventions,
                ),
            )
            loop.run_sidecar(
                spec, state, terminal,
                poll_interval=self.config.poll_interval_sec,
                read_lines=self.config.read_lines,
                stop_event=entry.stop_event,
                idle_timeout_sec=self.config.default_agent_timeout_sec,
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

    def _do_resume(self, request: dict) -> dict:
        """Resume a paused or crashed run by scanning existing run dirs."""
        spec_path = request.get("spec_path", "")
        pane_target = request.get("pane_target", "")

        if not spec_path or not pane_target:
            return {"ok": False, "error": "spec_path and pane_target required"}

        # Scan run dirs for matching state
        runs_dir = Path(self.runs_dir)
        if not runs_dir.exists():
            return {"ok": False, "error": "no runs directory"}

        # Load spec to get spec_id for matching
        try:
            target_spec = load_runnable_spec(spec_path)
        except Exception as e:
            return {"ok": False, "error": f"spec load failed: {e}"}

        for run_dir in sorted(runs_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True):
            state_path = run_dir / "state.json"
            if not state_path.exists():
                continue
            try:
                state_data = json.loads(state_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            # Match by spec_id + pane_target (not just pane)
            if (state_data.get("spec_id") == target_spec.id
                    and state_data.get("pane_target", "") == pane_target
                    and state_data.get("top_state") in ("PAUSED_FOR_HUMAN", "RUNNING", "READY", "GATING", "VERIFYING")):
                run_id = state_data["run_id"]
                resumable_state = state_data.get("top_state", "")
                if resumable_state in {"GATING", "VERIFYING"}:
                    return {
                        "ok": False,
                        "error": (
                            f"run {run_id} is in {resumable_state} and cannot safely resume. "
                            "Stop or inspect the run before restarting it."
                        ),
                    }
                current_spec_hash = StateStore._hash_spec(spec_path)
                saved_spec_hash = state_data.get("spec_hash", "")
                if current_spec_hash and not saved_spec_hash:
                    return {
                        "ok": False,
                        "error": (
                            f"run {run_id} has no persisted spec hash. "
                            "Legacy runs must be re-registered instead of resumed."
                        ),
                    }
                if current_spec_hash and saved_spec_hash and current_spec_hash != saved_spec_hash:
                    return {
                        "ok": False,
                        "error": (
                            "spec was modified since the run was created. "
                            "Use register to start a new run, or revert the spec to resume."
                        ),
                    }

                store = StateStore(str(run_dir))
                surface_type = request.get("surface_type") or state_data.get("surface_type", "tmux")
                state = store.load_or_init(
                    target_spec, spec_path=spec_path, pane_target=pane_target,
                    surface_type=surface_type,
                    workspace_root=state_data.get("workspace_root", os.getcwd()),
                    controller_mode="daemon",
                )
                resumed_from = state.top_state.value
                entry = RunEntry(run_id, spec_path, pane_target,
                                 state_data.get("workspace_root", ""),
                                 surface_type=surface_type, thread=None, store=store)

                # Acquire pane lock before starting
                pane_owner = self._pane_owner_metadata(run_id, spec_path, pane_target,
                                                       state_data.get("workspace_root", ""))
                with self._lock:
                    if run_id in self._runs:
                        return {"ok": False, "error": f"run {run_id} is already active"}
                    for existing in self._runs.values():
                        if existing.pane_target == pane_target and existing.thread and existing.thread.is_alive():
                            return {"ok": False, "error": f"pane {pane_target} owned by run {existing.run_id}"}
                    acquired, existing_owner = acquire_pane_lock(pane_target, pane_owner)
                    if not acquired:
                        return {"ok": False, "error": f"pane {pane_target} locked by {existing_owner}"}
                    if state.top_state == TopState.PAUSED_FOR_HUMAN:
                        transition_top_state(state, TopState.RUNNING, reason="resume requested")
                        state.delivery_state = DeliveryState.IDLE
                        state.auto_intervention_count = 0
                        state.node_mismatch_count = 0
                        state.last_mismatch_node_id = ""
                        state.human_escalations = []
                        store.append_session_event(
                            run_id,
                            "resume_requested",
                            {"resumed_from": resumed_from},
                        )
                        store.save(state)
                    thread = threading.Thread(
                        target=self._run_worker, args=(entry, target_spec, state),
                        name=f"run-{run_id}", daemon=True,
                    )
                    entry.thread = thread
                    self._runs[run_id] = entry
                    self._update_daemon_record_locked()
                thread.start()
                logger.info("resumed run %s from %s", run_id, resumed_from)
                return {"ok": True, "run_id": run_id, "resumed_from": resumed_from}

        return {"ok": False, "error": "no resumable run found for this spec + pane"}

    def _do_ack_review(self, request: dict) -> dict:
        """Record review acknowledgement and re-check finish gate."""
        run_id = request.get("run_id", "")
        reviewer = request.get("reviewer", "")
        if not run_id or not reviewer:
            return {"ok": False, "error": "run_id and reviewer required"}
        if reviewer not in {"human", "stronger_reviewer"}:
            return {"ok": False, "error": f"invalid reviewer: {reviewer}"}

        with self._lock:
            active_entry = self._runs.get(run_id)
            if active_entry and active_entry.thread and active_entry.thread.is_alive():
                return {
                    "ok": False,
                    "error": (
                        f"run {run_id} is currently active; "
                        "stop it or wait for it to pause before acknowledging review"
                    ),
                }
            if active_entry is not None:
                store = active_entry.store
                try:
                    state_data = json.loads(store.state_path.read_text())
                except (OSError, json.JSONDecodeError):
                    return {"ok": False, "error": f"could not read state for {run_id}"}
            else:
                store = None
                state_data = None
                for run_dir in sorted(Path(self.runs_dir).iterdir()):
                    state_path = run_dir / "state.json"
                    if not state_path.exists():
                        continue
                    try:
                        candidate = json.loads(state_path.read_text())
                    except (OSError, json.JSONDecodeError):
                        continue
                    if candidate.get("run_id") == run_id:
                        store = StateStore(str(run_dir))
                        state_data = candidate
                        break
                if store is None or state_data is None:
                    return {"ok": False, "error": f"run {run_id} not found"}

            spec_path = state_data.get("spec_path", "")
            if not spec_path:
                return {"ok": False, "error": f"run {run_id} has no spec_path"}
            current_spec_hash = StateStore._hash_spec(spec_path)
            saved_spec_hash = state_data.get("spec_hash", "")
            if current_spec_hash and saved_spec_hash and current_spec_hash != saved_spec_hash:
                return {
                    "ok": False,
                    "error": (
                        "spec was modified since the run was created. "
                        "Revert the spec to acknowledge the review for this run."
                    ),
                }
            try:
                spec = load_spec(spec_path)
            except Exception as e:
                return {"ok": False, "error": f"spec load failed: {e}"}

            state = store.load_or_init(
                spec,
                spec_path=spec_path,
                pane_target=state_data.get("pane_target", ""),
                surface_type=state_data.get("surface_type", "tmux"),
                workspace_root=state_data.get("workspace_root", os.getcwd()),
                controller_mode="daemon",
            )
            completed_reviews = set(getattr(state, "completed_reviews", []) or [])
            completed_reviews.add(reviewer)
            state.completed_reviews = sorted(completed_reviews)
            store.append_session_event(run_id, "review_acknowledged", {"reviewer": reviewer})

            finish = FinishGate().evaluate(spec, state, cwd=state.workspace_root)
            if finish["ok"]:
                if state.top_state not in FINAL_STATES:
                    transition_top_state(state, TopState.COMPLETED, reason="review acknowledged and finish gate passed")
                    store.append_session_event(run_id, "completed_after_review", {"reviewer": reviewer})
            store.save(state)
            return {"ok": True, "run_id": run_id, "top_state": state.top_state.value}

    # ------------------------------------------------------------------
    # P0: list + observe
    # ------------------------------------------------------------------

    def _do_list_runs(self) -> dict:
        """List all active runs with detailed state."""
        with self._lock:
            runs = []
            for e in self._runs.values():
                state = e._read_state() or {}
                summary = summarize_state(state)
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
                    "pause_reason": summary.get("pause_reason", ""),
                    "next_action": summary.get("next_action", ""),
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
        raw_metadata = request.get("metadata", {})
        if raw_metadata is None:
            metadata = {}
        elif isinstance(raw_metadata, dict):
            metadata = raw_metadata
        else:
            return {"ok": False, "error": "metadata must be an object"}

        note = {
            "note_id": f"note_{uuid.uuid4().hex[:12]}",
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "author_run_id": request.get("author_run_id", "human"),
            "note_type": request.get("note_type", "context"),
            "title": request.get("title", content[:80]),
            "content": content,
            "metadata": metadata,
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
            self._last_run_finished_at = time.time()
            with self._lock:
                for rid in reaped:
                    self._runs.pop(rid, None)
                self._update_daemon_record_locked()
            logger.info("reaped %d finished run(s)", len(reaped))

    def _refresh_idle_state(self) -> None:
        """Update registry with current idle duration (called every ~1s from accept loop)."""
        with self._lock:
            active = len(self._runs)
        if active > 0:
            return
        now = time.time()
        last_activity = max(self._last_run_finished_at, self._last_client_contact_at, self._started_at)
        update_daemon(
            self.sock_path,
            state="idle",
            active_runs=0,
            idle_for_sec=int(now - last_activity),
        )

    def _check_idle_shutdown(self) -> None:
        """Auto-shutdown if idle for too long with zero active runs."""
        if self.idle_shutdown_sec <= 0:
            return
        with self._lock:
            active = len(self._runs)
        if active > 0:
            return
        now = time.time()
        last_activity = max(self._last_run_finished_at, self._last_client_contact_at, self._started_at)
        idle_for = now - last_activity
        if idle_for >= self.idle_shutdown_sec:
            logger.info("idle shutdown: no active runs for %.0fs (threshold=%ds)", idle_for, self.idle_shutdown_sec)
            update_daemon(self.sock_path, state="shutting_down", idle_for_sec=int(idle_for))
            self._shutdown.set()

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
        from datetime import datetime, timezone
        return {
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "socket": self.sock_path,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "active_runs": len(self._runs),
            "state": "active" if self._runs else "idle",
            "idle_shutdown_sec": self.idle_shutdown_sec,
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
            "controller_mode": "daemon",
        }

    def _update_daemon_record_locked(self) -> None:
        now = time.time()
        last_activity = max(self._last_run_finished_at, self._last_client_contact_at, self._started_at)
        update_daemon(
            self.sock_path,
            pid=os.getpid(),
            cwd=os.getcwd(),
            active_runs=len(self._runs),
            state="active" if self._runs else "idle",
            idle_for_sec=int(now - last_activity) if not self._runs else 0,
        )
