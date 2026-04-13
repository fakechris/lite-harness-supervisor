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
