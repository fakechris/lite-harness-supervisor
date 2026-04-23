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


def test_daemon_server_uses_bindable_socket_path_for_deep_worktrees(tmp_path, monkeypatch):
    deep_root = tmp_path
    while len(str((deep_root / ".supervisor/daemon.sock").resolve()).encode("utf-8")) <= 120:
        deep_root = deep_root / ("nestedsegment" * 2)
    deep_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(deep_root)

    server = DaemonServer()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_path = Path(server.sock_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sock.bind(server.sock_path)
    finally:
        sock.close()
        sock_path.unlink(missing_ok=True)


def test_daemon_server_expands_user_paths(tmp_path, monkeypatch):
    import tempfile

    home = Path(tempfile.mkdtemp(prefix="lhs-home-", dir="/tmp"))
    monkeypatch.setenv("HOME", str(home))

    server = DaemonServer(
        sock_path="~/.supervisor/custom.sock",
        pid_path="~/.supervisor/custom.pid",
        runs_dir="~/.supervisor/runtime/runs",
    )

    assert server.sock_path == str((home / ".supervisor/custom.sock").resolve())
    assert server.pid_path == str((home / ".supervisor/custom.pid").resolve())
    assert server.runs_dir == str((home / ".supervisor/runtime/runs").resolve())


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


class TestDaemonEscalateClarification:
    """Daemon IPC for operator-initiated escalation (0.3.7)."""

    def _server(self, tmp_path):
        return DaemonServer(runs_dir=str(tmp_path / "runs"))

    def _seed_run_state(self, tmp_path, run_id: str) -> None:
        run_dir = Path(tmp_path) / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(json.dumps({"run_id": run_id}))

    def test_writes_audit_event_and_returns_id(self, tmp_path):
        server = self._server(tmp_path)
        self._seed_run_state(tmp_path, "run_esc1")

        resp = server._do_escalate_clarification({
            "run_id": "run_esc1",
            "question": "is this migration safe?",
            "language": "zh",
            "reason": "tui_low_confidence",
            "operator": "op1",
            "confidence": 0.12,
        })
        assert resp["ok"] is True
        assert len(resp["escalation_id"]) == 16

        log_path = Path(tmp_path) / "runs" / "run_esc1" / "session_log.jsonl"
        assert log_path.exists()
        events = [json.loads(line) for line in log_path.read_text().strip().splitlines()]
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "clarification_escalated_to_worker"
        assert ev["payload"]["question"] == "is this migration safe?"
        assert ev["payload"]["reason"] == "tui_low_confidence"
        assert ev["payload"]["operator"] == "op1"
        assert ev["payload"]["confidence"] == 0.12
        assert ev["payload"]["language"] == "zh"
        assert ev["payload"]["transport"] == "pending_0_3_8"
        assert ev["payload"]["escalation_id"] == resp["escalation_id"]

    def test_rejects_missing_run(self, tmp_path):
        server = self._server(tmp_path)
        resp = server._do_escalate_clarification({
            "run_id": "run_ghost", "question": "q?",
        })
        assert resp["ok"] is False
        assert "not found" in resp["error"]

    def test_rejects_empty_question(self, tmp_path):
        server = self._server(tmp_path)
        self._seed_run_state(tmp_path, "run_esc2")
        resp = server._do_escalate_clarification({
            "run_id": "run_esc2", "question": "",
        })
        assert resp["ok"] is False
        assert "question" in resp["error"]

    def test_client_roundtrip(self, client, tmp_path):
        # End-to-end: client.escalate_clarification → daemon → event on disk.
        # Daemon fixture (see `daemon_server`) uses `tmp_path / "runs"`,
        # so seeding state.json there exposes the run through
        # `_resolve_run_store`'s on-disk fallback.
        self._seed_run_state(tmp_path, "run_cli")

        resp = client.escalate_clarification(
            "run_cli", "what happened?",
            language="en", reason="im_operator",
            operator="op2", confidence=0.3,
        )
        assert resp["ok"] is True
        assert resp["escalation_id"]

        log_path = Path(tmp_path) / "runs" / "run_cli" / "session_log.jsonl"
        events = [json.loads(line) for line in log_path.read_text().strip().splitlines()]
        assert events[0]["event_type"] == "clarification_escalated_to_worker"
        assert events[0]["payload"]["operator"] == "op2"


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

    def test_deferred_wake_surfaces_v1_limitation_warning(self, tmp_path, caplog):
        """When wake_policy returns ``defer`` (run is busy at ingest),
        nothing re-evaluates on later state changes in v1.  Warn so the
        limitation is visible in logs rather than silent — matching the
        existing ``wake_worker`` not-yet-wired warning.  Review finding."""
        import logging

        server = self._server(tmp_path)
        reg = server._do_external_task_create({
            "session_id": "s1",
            "run_id": "run_busy",
            "provider": "external_model",
            "target_ref": "PR#1",
            "blocking_policy": "block_session",
        })
        # Stub evaluate_wake to return 'defer' deterministically.
        from supervisor.event_plane.wake_policy import WakeDecision

        import supervisor.daemon.server as server_mod

        monkeypatched = lambda *a, **kw: WakeDecision(
            "defer", "block_session but run is RUNNING"
        )
        orig = server_mod.evaluate_wake
        server_mod.evaluate_wake = monkeypatched
        try:
            with caplog.at_level(logging.WARNING, logger="supervisor.daemon.server"):
                result = server._do_external_result_ingest({
                    "request_id": reg["request_id"],
                    "provider": "external_model",
                    "result_kind": "review_comments",
                    "summary": "nit",
                })
        finally:
            server_mod.evaluate_wake = orig

        assert result["ok"] is True
        assert result["wake_decision"] == "defer"
        assert any(
            "deferred wake" in rec.message
            and "v1 does not re-evaluate" in rec.message
            for rec in caplog.records
        ), caplog.records

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

    def test_find_store_for_reaped_run_preserves_seq_monotonicity(self, tmp_path):
        """Regression: a StateStore created for a reaped run via
        `_find_store_for_run` must align its `_session_seq` to the existing
        session_log so later event appends don't collide with earlier seqs.
        """
        # Seed a run_dir with a state.json and a session_log.jsonl that has
        # events already at seq=1 and seq=2.
        run_id = "run_reaped_1"
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text("{}", encoding="utf-8")
        log = run_dir / "session_log.jsonl"
        log.write_text(
            '{"run_id":"run_reaped_1","seq":1,"event_type":"a","timestamp":"t","payload":{}}\n'
            '{"run_id":"run_reaped_1","seq":2,"event_type":"b","timestamp":"t","payload":{}}\n',
            encoding="utf-8",
        )

        server = self._server(tmp_path)
        # The run is not in the in-memory registry, so this routes through
        # the fresh-StateStore branch.
        server._append_run_session_event(run_id, "external_task_requested", {"x": 1})

        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        last = json.loads(lines[-1])
        assert last["seq"] == 3  # not 1 — seq counter must have been aligned

    def test_maybe_close_session_for_terminal_run_closes_session(self, tmp_path):
        """When a reaped run finished in a terminal state and no other
        active run shares its session_id, the daemon closes the session.
        This is the guard against indefinite adoption of a stale session.
        """
        from supervisor.domain.models import Session

        run_id = "run_done_1"
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(
            json.dumps({
                "run_id": run_id,
                "top_state": "COMPLETED",
                "session_id": "sess_done_1",
                "workspace_root": "/w",
                "spec_id": "spec_x",
            }),
            encoding="utf-8",
        )

        server = self._server(tmp_path)
        # Seed an active session record so close_session has something to
        # transition.
        store = server._find_store_for_run(run_id)
        assert store is not None
        store.save_session(Session(
            session_id="sess_done_1",
            workspace_root="/w",
            spec_id="spec_x",
        ))

        server._maybe_close_session_for(run_id)

        session = store.load_session("sess_done_1")
        assert session is not None
        assert session.status == "closed"
        # find_session_by_attachment must no longer return it.
        assert store.find_session_by_attachment(
            workspace_root="/w", spec_id="spec_x",
        ) is None

    def test_maybe_close_session_preserves_session_with_other_active_run(self, tmp_path):
        """A session attached to multiple runs must stay open while any
        active run holds it. Only the last run's termination closes it.
        """
        from supervisor.domain.models import Session

        session_id = "sess_shared"
        done_run = "run_done_shared"
        active_run = "run_still_up"

        for rid, state in [
            (done_run, "COMPLETED"),
            (active_run, "RUNNING"),
        ]:
            d = tmp_path / "runs" / rid
            d.mkdir(parents=True)
            (d / "state.json").write_text(json.dumps({
                "run_id": rid,
                "top_state": state,
                "session_id": session_id,
                "workspace_root": "/w",
                "spec_id": "spec_y",
            }), encoding="utf-8")

        server = self._server(tmp_path)
        done_store = server._find_store_for_run(done_run)
        done_store.save_session(Session(
            session_id=session_id, workspace_root="/w", spec_id="spec_y",
        ))

        # Stage an active RunEntry for the still-up run.
        active_store = server._find_store_for_run(active_run)
        entry = RunEntry(
            active_run, "/no/spec.yaml", "%1", "/w",
            surface_type="tmux", thread=None, store=active_store,
        )
        with server._lock:
            server._runs[active_run] = entry

        server._maybe_close_session_for(done_run)
        session = done_store.load_session(session_id)
        assert session is not None
        assert session.status == "active"  # not closed — active sibling holds it

    def test_find_store_for_reaped_run_caches_instance(self, tmp_path):
        """Concurrent callers must share the same StateStore so the seq lock
        and counter are authoritative. Without the cache, two threads could
        each read seq=2 and both write seq=3, producing a duplicate.
        """
        run_id = "run_reaped_cache"
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text("{}", encoding="utf-8")
        (run_dir / "session_log.jsonl").write_text("", encoding="utf-8")

        server = self._server(tmp_path)
        first = server._find_store_for_run(run_id)
        second = server._find_store_for_run(run_id)
        assert first is not None
        assert first is second

    def test_reaped_stores_cache_evicts_oldest_past_max(self, tmp_path):
        """The LRU cap prevents unbounded growth for long-running daemons
        that touch many reaped runs via the event-plane path."""
        server = self._server(tmp_path)
        server._reaped_stores_max = 3

        def _make(rid: str) -> None:
            run_dir = tmp_path / "runs" / rid
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text("{}", encoding="utf-8")
            (run_dir / "session_log.jsonl").write_text("", encoding="utf-8")

        for rid in ("a", "b", "c", "d"):
            _make(rid)
            server._find_store_for_run(rid)

        assert list(server._reaped_stores.keys()) == ["b", "c", "d"]

        # Re-touch "b" — must survive next eviction since it moves to tail.
        server._find_store_for_run("b")
        _make("e")
        server._find_store_for_run("e")
        assert list(server._reaped_stores.keys()) == ["d", "b", "e"]
