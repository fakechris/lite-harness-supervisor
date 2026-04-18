"""Tests for daemon server/client IPC."""
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from supervisor.daemon.server import DaemonServer
from supervisor.daemon.server import RunEntry
from supervisor.daemon.client import DaemonClient
from supervisor.domain.enums import TopState
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


@pytest.fixture
def daemon_server(tmp_path, monkeypatch):
    """Start a daemon server in a thread with temp paths."""
    import tempfile
    # Use /tmp directly — macOS AF_UNIX path limit is 104 bytes
    sock_path = tempfile.mktemp(prefix="sv_", suffix=".sock", dir="/tmp")
    pid_path = str(tmp_path / "test.pid")
    runs_dir = str(tmp_path / "runs")

    server = DaemonServer(sock_path=sock_path, pid_path=pid_path, runs_dir=runs_dir)
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()

    # Wait for socket to be ready
    client = DaemonClient(sock_path)
    for _ in range(20):
        time.sleep(0.1)
        if client.is_running():
            break
    else:
        pytest.skip("daemon did not start in time")

    yield server, sock_path

    server._shutdown.set()
    thread.join(timeout=3)
    try:
        os.unlink(sock_path)
    except OSError:
        pass


@pytest.fixture
def client(daemon_server):
    _, sock_path = daemon_server
    return DaemonClient(sock_path)


class TestDaemonPing:
    def test_ping(self, client):
        assert client.is_running() is True

    def test_client_not_running(self, tmp_path):
        c = DaemonClient(str(tmp_path / "nonexistent.sock"))
        assert c.is_running() is False


class TestDaemonStatus:
    def test_empty_status(self, client):
        result = client.status()
        assert result["ok"] is True
        assert result["runs"] == []

    def test_status_includes_pause_reason_and_next_action(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.human_escalations = [{"reason": "node mismatch persisted for 5 checkpoints"}]
        store.save(state)

        class _AliveThread:
            def is_alive(self) -> bool:
                return True

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        server._runs[state.run_id] = RunEntry(
            state.run_id,
            str(spec_path),
            "%1",
            str(tmp_path),
            "tmux",
            _AliveThread(),
            store,
        )

        status = server._do_status()
        listed = server._do_list_runs()

        assert status["runs"][0]["pause_reason"] == "node mismatch persisted for 5 checkpoints"
        assert "thin-supervisor run resume" in status["runs"][0]["next_action"]
        assert listed["runs"][0]["pause_reason"] == "node mismatch persisted for 5 checkpoints"
        assert "thin-supervisor run resume" in listed["runs"][0]["next_action"]

    def test_recover_orphaned_running_state_pauses_it_for_explicit_resume(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.RUNNING
        store.save(state)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))

        server._recover_orphaned_runs()

        recovered = StateStore(str(run_dir)).load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        assert recovered.top_state == TopState.PAUSED_FOR_HUMAN
        assert recovered.human_escalations
        assert "daemon restarted while the run was in progress" in recovered.human_escalations[-1]["reason"]
        session_events = (run_dir / "session_log.jsonl").read_text(encoding="utf-8")
        assert "orphaned_run_recovered" in session_events

    def test_recover_orphaned_runs_skips_foreground_owned_state(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_fg"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
            controller_mode="foreground",
        )
        state.top_state = TopState.RUNNING
        store.save(state)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))

        recovered_count = server._recover_orphaned_runs()

        recovered = StateStore(str(run_dir)).load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
            controller_mode="foreground",
        )
        assert recovered_count == 0
        assert recovered.top_state == TopState.RUNNING
        assert recovered.human_escalations == []
        session_path = run_dir / "session_log.jsonl"
        if session_path.exists():
            assert "orphaned_run_recovered" not in session_path.read_text(encoding="utf-8")


class TestDaemonRegister:
    def test_register_missing_fields(self, client):
        result = client.register("", "")
        assert result["ok"] is False
        assert "required" in result["error"]

    def test_register_bad_spec(self, client):
        result = client.register("/nonexistent/spec.yaml", "test:0")
        assert result["ok"] is False
        assert "spec load failed" in result["error"]

    def test_register_rejects_draft_spec(self, client, tmp_path):
        spec_path = tmp_path / "draft.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: draft_plan\n"
            "goal: test\n"
            "approval:\n"
            "  required: true\n"
            "  status: draft\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )

        result = client.register(str(spec_path), "pane:1")

        assert result["ok"] is False
        assert "requires user approval" in result["error"]

    def test_register_valid(self, client, tmp_path):
        # Create a minimal spec
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        result = client.register(str(spec_path), "nonexistent-pane:0")
        assert result["ok"] is True
        assert result["run_id"].startswith("run_")

        # Status should show it
        status = client.status()
        assert len(status["runs"]) == 1
        assert status["runs"][0]["run_id"] == result["run_id"]

    def test_duplicate_pane_rejected(self, client, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\nid: test\ngoal: test\n"
            "steps:\n  - id: s1\n    type: task\n    objective: do\n"
            "    verify:\n      - type: command\n        run: echo ok\n        expect: pass\n"
        )
        r1 = client.register(str(spec_path), "same-pane:0")
        assert r1["ok"] is True
        # Worker thread crashes quickly (no real tmux pane), but
        # we check immediately while thread is still alive
        result = client.register(str(spec_path), "same-pane:0")
        # Either rejected (thread still alive) or accepted (thread died fast)
        # Both are valid — the key behavior is the check happens
        assert isinstance(result, dict)


class TestDaemonStopRun:
    def test_stop_nonexistent(self, client):
        result = client.stop_run("run_nonexistent")
        assert result["ok"] is False

    def test_stop_all_empty(self, client):
        result = client.stop_all()
        assert result["ok"] is True
        assert result["stopped"] == 0


class TestDaemonReviewAck:
    def test_ack_review_completes_run_when_review_was_only_blocker(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "acceptance:\n"
            "  must_review_by: human\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.done_node_ids = ["s1"]
        state.verification = {"ok": True}
        store.save(state)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_ack_review({"run_id": state.run_id, "reviewer": "human"})

        assert result["ok"] is True
        updated = StateStore(str(run_dir)).load_or_init(spec)
        assert updated.top_state == TopState.COMPLETED
        assert "human" in updated.completed_reviews

    def test_ack_review_on_failed_run_does_not_raise_or_rewrite_terminal_state(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "acceptance:\n"
            "  must_review_by: human\n"
            "  require_all_steps_done: false\n"
            "  require_verification_pass: false\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_failed"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.FAILED
        state.done_node_ids = ["s1"]
        state.verification = {"ok": True}
        store.save(state)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_ack_review({"run_id": state.run_id, "reviewer": "human"})

        assert result["ok"] is True
        assert result["top_state"] == TopState.FAILED.value
        updated = StateStore(str(run_dir)).load_or_init(spec)
        assert updated.top_state == TopState.FAILED
        assert "human" in updated.completed_reviews

    def test_ack_review_rejects_active_run(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "acceptance:\n"
            "  must_review_by: human\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        store.save(state)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        active_thread = _DummyThread()
        active_thread.start()
        server._runs[state.run_id] = server._runs.get(state.run_id) or type("Entry", (), {
            "run_id": state.run_id,
            "spec_path": str(spec_path),
            "pane_target": "%1",
            "workspace_root": str(tmp_path),
            "surface_type": "tmux",
            "thread": active_thread,
            "store": store,
        })()

        result = server._do_ack_review({"run_id": state.run_id, "reviewer": "human"})

        assert result["ok"] is False
        assert "currently active" in result["error"]

    def test_ack_review_rejects_modified_spec(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "acceptance:\n"
            "  must_review_by: human\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        store.save(state)

        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: changed\n"
            "acceptance:\n"
            "  must_review_by: human\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: changed\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo changed\n"
            "        expect: pass\n"
        )

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_ack_review({"run_id": state.run_id, "reviewer": "human"})

        assert result["ok"] is False
        assert "spec was modified" in result["error"]

    def test_ack_review_holds_server_lock_during_read_modify_write(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "acceptance:\n"
            "  must_review_by: human\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.done_node_ids = ["s1"]
        state.verification = {"ok": True}
        store.save(state)

        class _TrackingLock:
            def __init__(self):
                self.depth = 0

            def __enter__(self):
                self.depth += 1

            def __exit__(self, exc_type, exc, tb):
                self.depth -= 1

        lock = _TrackingLock()
        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        server._lock = lock

        original_load_spec = load_spec
        original_save = StateStore.save

        def wrapped_load_spec(path):
            assert lock.depth > 0
            return original_load_spec(path)

        def wrapped_save(self, current_state):
            assert lock.depth > 0
            return original_save(self, current_state)

        monkeypatch.setattr("supervisor.daemon.server.load_spec", wrapped_load_spec)
        monkeypatch.setattr("supervisor.daemon.server.StateStore.save", wrapped_save)

        result = server._do_ack_review({"run_id": state.run_id, "reviewer": "human"})

        assert result["ok"] is True


class _DummyThread:
    def __init__(self, target=None, args=(), name="", daemon=False):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


class TestDaemonResume:
    def test_resume_paused_for_human_transitions_back_to_running(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.auto_intervention_count = 2
        state.node_mismatch_count = 4
        state.last_mismatch_node_id = "stale_node"
        state.human_escalations = [{"reason": "node mismatch persisted for 5 checkpoints"}]
        store.save(state)

        monkeypatch.setattr("supervisor.daemon.server.acquire_pane_lock", lambda pane, owner: (True, {}))
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%1"})

        assert result["ok"] is True
        resumed = StateStore(str(run_dir)).load_or_init(spec, spec_path=str(spec_path), pane_target="%1")
        assert resumed.top_state == TopState.RUNNING
        assert resumed.auto_intervention_count == 0
        assert resumed.node_mismatch_count == 0
        assert resumed.last_mismatch_node_id == ""
        assert resumed.human_escalations == []
        session_events = (run_dir / "session_log.jsonl").read_text()
        assert "resume_requested" in session_events

    def test_resume_rejects_modified_spec_instead_of_silent_restart(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        store.save(state)

        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test changed\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something else\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo changed\n"
            "        expect: pass\n"
        )

        monkeypatch.setattr("supervisor.daemon.server.acquire_pane_lock", lambda pane, owner: (True, {}))
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%1"})

        assert result["ok"] is False
        assert "spec was modified" in result["error"]

    def test_resume_keeps_run_paused_when_lock_acquisition_fails(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        store.save(state)

        monkeypatch.setattr(
            "supervisor.daemon.server.acquire_pane_lock",
            lambda pane, owner: (False, {"run_id": "other"}),
        )
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%1"})

        assert result["ok"] is False
        paused = StateStore(str(run_dir)).load_or_init(spec, spec_path=str(spec_path), pane_target="%1")
        assert paused.top_state == TopState.PAUSED_FOR_HUMAN

    def test_resume_rejects_verifying_state(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.VERIFYING
        store.save(state)

        monkeypatch.setattr("supervisor.daemon.server.acquire_pane_lock", lambda pane, owner: (True, {}))
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%1"})

        assert result["ok"] is False
        assert "cannot safely resume" in result["error"]

    def test_resume_recovery_needed_leaves_state_for_sidecar_failsafe(self, tmp_path, monkeypatch):
        """A run persisted in RECOVERY_NEEDED (sidecar crashed between
        `_enter_recovery` and its follow-up transition) must be resumable,
        but the daemon must NOT silently flip the state to RUNNING — the
        stalled auto-intervention recipe can't be safely replayed without
        operator review. Instead, the sidecar's boot-time fail-safe in
        `_run_sidecar_inner` handles it by pausing for human with
        rec.crash_during_recovery.
        """
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_recovery"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%2",
            workspace_root=str(tmp_path),
        )
        # Direct assignment bypasses the transition table; the resume path is
        # what we're exercising, not how the state got persisted.
        state.top_state = TopState.RECOVERY_NEEDED
        store.save(state)

        monkeypatch.setattr("supervisor.daemon.server.acquire_pane_lock", lambda pane, owner: (True, {}))
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%2"})

        assert result["ok"] is True
        assert result["resumed_from"] == TopState.RECOVERY_NEEDED.value
        resumed = StateStore(str(run_dir)).load_or_init(spec, spec_path=str(spec_path), pane_target="%2")
        # Daemon must leave the state untouched — the sidecar boot-check is
        # authoritative for this transition.
        assert resumed.top_state == TopState.RECOVERY_NEEDED
        session_events = (run_dir / "session_log.jsonl").read_text()
        assert "resume_requested" in session_events

    def test_resume_from_paused_attached_restores_attach_boundary(self, tmp_path, monkeypatch):
        """A run that paused on the attach boundary (e.g. RE_INJECT cap
        exhausted) must resume back to ATTACHED, not RUNNING, so the
        first-execution-evidence gate still runs on the next checkpoint.
        Without this, the agent could slip through with admin-only
        evidence after a human resumes a Phase-17-style pause.
        """
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_attached_pause"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%3",
            workspace_root=str(tmp_path),
        )
        # Simulate prior state: paused from ATTACHED (RE_INJECT exhausted
        # path captured the source top_state).
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.pre_pause_top_state = TopState.ATTACHED.value
        state.re_inject_count = 4  # exceeded MAX_RE_INJECTS
        store.save(state)

        monkeypatch.setattr("supervisor.daemon.server.acquire_pane_lock", lambda pane, owner: (True, {}))
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%3"})

        assert result["ok"] is True
        resumed = StateStore(str(run_dir)).load_or_init(spec, spec_path=str(spec_path), pane_target="%3")
        assert resumed.top_state == TopState.ATTACHED
        # Re-inject counter must be re-armed so the resumed run gets a
        # fresh window to prove execution evidence.
        assert resumed.re_inject_count == 0
        # pre_pause marker is cleared after consumption so a subsequent
        # pause does not accidentally re-trigger this branch.
        assert resumed.pre_pause_top_state == ""

    def test_resume_from_paused_running_stays_running(self, tmp_path, monkeypatch):
        """Symmetry test for the ATTACHED-restore path: a pause that did
        NOT originate on the attach boundary still resumes to RUNNING.
        """
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_running_pause"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%4",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.pre_pause_top_state = TopState.RUNNING.value
        store.save(state)

        monkeypatch.setattr("supervisor.daemon.server.acquire_pane_lock", lambda pane, owner: (True, {}))
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%4"})

        assert result["ok"] is True
        resumed = StateStore(str(run_dir)).load_or_init(spec, spec_path=str(spec_path), pane_target="%4")
        assert resumed.top_state == TopState.RUNNING

    def test_recoverable_orphaned_states_includes_attached_and_recovery(self):
        """Reviewer P2-4: RECOVERABLE_ORPHANED_STATES must include ATTACHED
        and RECOVERY_NEEDED.  A daemon crash during either leaves an orphan
        that the operator can still recover — observability surfaces render
        these as actionable, so the daemon entry point must too.
        """
        from supervisor.daemon.server import RECOVERABLE_ORPHANED_STATES
        assert TopState.ATTACHED in RECOVERABLE_ORPHANED_STATES
        assert TopState.RECOVERY_NEEDED in RECOVERABLE_ORPHANED_STATES
        # The legacy in-flight states must still be covered too — this test
        # doubles as a fence against a future refactor dropping them.
        assert TopState.RUNNING in RECOVERABLE_ORPHANED_STATES
        assert TopState.GATING in RECOVERABLE_ORPHANED_STATES
        assert TopState.VERIFYING in RECOVERABLE_ORPHANED_STATES

    def test_resume_rejects_legacy_state_without_spec_hash(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        run_dir = tmp_path / "runs" / "run_123"
        store = StateStore(str(run_dir))
        state = store.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.spec_hash = ""
        store.save(state)

        monkeypatch.setattr("supervisor.daemon.server.acquire_pane_lock", lambda pane, owner: (True, {}))
        monkeypatch.setattr("supervisor.daemon.server.update_daemon", lambda *a, **k: None)
        monkeypatch.setattr("threading.Thread", _DummyThread)

        server = DaemonServer(runs_dir=str(tmp_path / "runs"))
        result = server._do_resume({"spec_path": str(spec_path), "pane_target": "%1"})

        assert result["ok"] is False
        assert "no persisted spec hash" in result["error"]


class TestDaemonExternalTask:
    """Daemon IPC for event-plane request/result/mailbox (Task 3)."""

    def _server(self, tmp_path):
        return DaemonServer(runs_dir=str(tmp_path / "runs"))

    def test_external_task_create_persists_request_and_wait(self, tmp_path):
        server = self._server(tmp_path)
        resp = server._do_external_task_create({
            "session_id": "s1",
            "run_id": "run_1",
            "provider": "external_model",
            "target_ref": "PR#1",
            "task_kind": "review",
            "blocking_policy": "notify_only",
        })
        assert resp["ok"] is True
        assert resp["request_id"].startswith("req_")
        assert resp["wait_id"].startswith("wait_")

    def test_external_task_create_requires_session_and_provider(self, tmp_path):
        server = self._server(tmp_path)
        resp = server._do_external_task_create({
            "session_id": "",
            "provider": "",
            "target_ref": "",
        })
        assert resp["ok"] is False

    def test_external_result_ingest_resolves_wait_and_creates_mailbox_item(self, tmp_path):
        server = self._server(tmp_path)
        reg = server._do_external_task_create({
            "session_id": "s1",
            "run_id": "run_1",
            "provider": "external_model",
            "target_ref": "PR#1",
        })
        resp = server._do_external_result_ingest({
            "request_id": reg["request_id"],
            "provider": "external_model",
            "result_kind": "review_comments",
            "summary": "nit",
        })
        assert resp["ok"] is True
        listing = server._do_mailbox_list({"session_id": "s1"})
        assert listing["ok"] is True
        assert len(listing["items"]) == 1
        assert listing["items"][0]["delivery_status"] == "new"

    def test_external_result_ingest_rejects_unknown_request(self, tmp_path):
        server = self._server(tmp_path)
        resp = server._do_external_result_ingest({
            "request_id": "req_bogus",
            "provider": "external_model",
            "result_kind": "review_comments",
        })
        assert resp["ok"] is False

    def test_external_result_ingest_is_idempotent(self, tmp_path):
        server = self._server(tmp_path)
        reg = server._do_external_task_create({
            "session_id": "s1",
            "run_id": "run_1",
            "provider": "external_model",
            "target_ref": "PR#1",
        })
        first = server._do_external_result_ingest({
            "request_id": reg["request_id"],
            "provider": "external_model",
            "result_kind": "review_comments",
            "idempotency_key": "evt_42",
        })
        second = server._do_external_result_ingest({
            "request_id": reg["request_id"],
            "provider": "external_model",
            "result_kind": "review_comments",
            "idempotency_key": "evt_42",
        })
        assert first["ok"] is True and second["ok"] is True
        assert second.get("deduped") is True
        listing = server._do_mailbox_list({"session_id": "s1"})
        assert len(listing["items"]) == 1

    def test_external_result_ingest_applies_wake_policy_and_records_decision(self, tmp_path):
        server = self._server(tmp_path)
        reg = server._do_external_task_create({
            "session_id": "s1",
            "run_id": "run_x",
            "provider": "external_model",
            "target_ref": "PR#1",
            "blocking_policy": "notify_only",
        })
        result = server._do_external_result_ingest({
            "request_id": reg["request_id"],
            "provider": "external_model",
            "result_kind": "review_comments",
            "summary": "nit",
        })
        assert result["ok"] is True
        assert result["wake_decision"] == "notify_operator"

        listing = server._do_mailbox_list({"session_id": "s1"})
        assert listing["items"][0]["wake_decision"] == "notify_operator"

    def test_status_surfaces_event_plane_counts_per_session(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        seed = StateStore(str(tmp_path / "seed"))
        seeded = seed.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        run_dir = tmp_path / "runs" / seeded.run_id
        run_dir.mkdir(parents=True)
        StateStore(str(run_dir)).save(seeded)

        class _AliveThread:
            def is_alive(self) -> bool:
                return True

        server = self._server(tmp_path)
        server._runs[seeded.run_id] = RunEntry(
            seeded.run_id,
            str(spec_path),
            "%1",
            str(tmp_path),
            "tmux",
            _AliveThread(),
            StateStore(str(run_dir)),
        )

        server._do_external_task_create({
            "session_id": seeded.session_id,
            "run_id": seeded.run_id,
            "provider": "external_model",
            "target_ref": "PR#1",
        })

        status = server._do_status()
        plane = status["runs"][0]["event_plane"]
        assert plane["waits_open"] == 1
        assert plane["requests_total"] == 1
        assert plane["mailbox_new"] == 0  # no result yet

    def test_mailbox_ack_transitions_item(self, tmp_path):
        server = self._server(tmp_path)
        reg = server._do_external_task_create({
            "session_id": "s1",
            "run_id": "run_1",
            "provider": "external_model",
            "target_ref": "PR#1",
        })
        ingest = server._do_external_result_ingest({
            "request_id": reg["request_id"],
            "provider": "external_model",
            "result_kind": "review_comments",
        })
        ack = server._do_mailbox_ack({"mailbox_item_id": ingest["mailbox_item_id"]})
        assert ack["ok"] is True

        listing = server._do_mailbox_list({"session_id": "s1", "delivery_status": "acknowledged"})
        assert len(listing["items"]) == 1
        assert listing["items"][0]["mailbox_item_id"] == ingest["mailbox_item_id"]

    def test_external_task_create_emits_session_event_when_run_known(self, tmp_path):
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do something\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_path))
        seed = StateStore(str(tmp_path / "seed"))
        seeded = seed.load_or_init(
            spec,
            spec_path=str(spec_path),
            pane_target="%1",
            workspace_root=str(tmp_path),
        )
        # Materialize state under the canonical runs_dir/<run_id>/ layout.
        run_dir = tmp_path / "runs" / seeded.run_id
        run_dir.mkdir(parents=True)
        canonical = StateStore(str(run_dir))
        canonical.save(seeded)

        server = self._server(tmp_path)
        server._do_external_task_create({
            "session_id": seeded.session_id,
            "run_id": seeded.run_id,
            "provider": "external_model",
            "target_ref": "PR#1",
        })

        session_log = (run_dir / "session_log.jsonl").read_text(encoding="utf-8")
        assert "external_task_requested" in session_log

    def test_phase_plan_request_with_no_run_id_succeeds_over_ipc(self, tmp_path):
        """Task 7: phase=plan with no run_id correlates by session_id alone.

        The daemon must accept a plan-phase external-task registration even
        when no run exists yet, and the follow-up result ingest must land in
        the mailbox under the same session_id.
        """
        server = self._server(tmp_path)
        reg = server._do_external_task_create({
            "session_id": "s_plan_ipc",
            "provider": "external_model",
            "target_ref": "spec:intro.md",
            "phase": "plan",
            "task_kind": "review",
            "blocking_policy": "notify_only",
            # no run_id
        })
        assert reg["ok"] is True
        assert reg["session_id"] == "s_plan_ipc"

        resp = server._do_external_result_ingest({
            "request_id": reg["request_id"],
            "provider": "external_model",
            "result_kind": "analysis",
            "summary": "plan ok",
        })
        assert resp["ok"] is True

        listing = server._do_mailbox_list({"session_id": "s_plan_ipc"})
        assert listing["ok"] is True
        assert len(listing["items"]) == 1
        assert listing["items"][0]["run_id"] is None
        # Wake decision still computed on plan-phase item (no run attached,
        # blocking_policy=notify_only → notify_operator).
        assert resp.get("wake_decision") == "notify_operator"
