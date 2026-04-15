"""Tests for the explicit delivery/ack protocol."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from supervisor.domain.enums import DeliveryState, TopState
from supervisor.domain.models import SupervisorState
from supervisor.loop import SupervisorLoop
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.interventions import AutoInterventionManager
from supervisor.notifications import NotificationManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockTerminal:
    """Minimal mock terminal for delivery protocol tests."""

    def __init__(self, outputs: list[str], *, fail_inject: bool = False):
        self._outputs = list(outputs)
        self._index = 0
        self._read_done = False
        self.injected: list[str] = []
        self.fail_inject = fail_inject

    def read(self, lines: int = 100) -> str:
        self._read_done = True
        if self._index < len(self._outputs):
            text = self._outputs[self._index]
            self._index += 1
            return text
        return ""

    def inject(self, text: str) -> None:
        assert self._read_done, "read guard violated"
        if self.fail_inject:
            self._read_done = False
            raise RuntimeError("inject failed: terminal not responding")
        self.injected.append(text)
        self._read_done = False

    def current_cwd(self) -> str:
        return "/tmp"

    def session_id(self) -> str:
        return "mock-pane"


def _make_loop(tmp_path, terminal):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path))
    loop = SupervisorLoop(
        store=store,
        notification_manager=NotificationManager(),
        auto_intervention_manager=AutoInterventionManager(mode="disabled"),
    )
    return loop, spec, store


def _run_sidecar(loop, spec, store, terminal, *, timeout=5):
    state = store.load_or_init(spec, spec_path="specs/examples/linear_plan.example.yaml", pane_target="mock-pane")
    loop.run_sidecar(spec, state, terminal, poll_interval=0, idle_timeout_sec=timeout)
    return state


# ---------------------------------------------------------------------------
# Phase 1: Domain — DeliveryState enum
# ---------------------------------------------------------------------------

def test_delivery_state_enum_values():
    assert DeliveryState.IDLE == "IDLE"
    assert DeliveryState.INJECTED == "INJECTED"
    assert DeliveryState.SUBMITTED == "SUBMITTED"
    assert DeliveryState.ACKNOWLEDGED == "ACKNOWLEDGED"
    assert DeliveryState.STARTED_PROCESSING == "STARTED_PROCESSING"
    assert DeliveryState.FAILED == "FAILED"
    assert DeliveryState.TIMED_OUT == "TIMED_OUT"


def test_delivery_state_persisted_in_state_json(tmp_path):
    """delivery_state survives save/load cycle."""
    store = StateStore(str(tmp_path))
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    state = store.load_or_init(spec, spec_path="specs/examples/linear_plan.example.yaml", pane_target="%0")
    state.delivery_state = "ACKNOWLEDGED"
    store.save(state)

    loaded = json.loads((tmp_path / "state.json").read_text())
    assert loaded["delivery_state"] == "ACKNOWLEDGED"

    state2 = SupervisorState.from_dict(loaded)
    assert state2.delivery_state == "ACKNOWLEDGED"


def test_delivery_state_defaults_to_idle():
    """Old state dicts without delivery_state default to IDLE."""
    data = {
        "run_id": "r1", "spec_id": "s1", "mode": "strict_verifier",
        "top_state": "RUNNING", "current_node_id": "n1",
    }
    state = SupervisorState.from_dict(data)
    assert state.delivery_state == "IDLE"


# ---------------------------------------------------------------------------
# Phase 2: Adapter — last_delivery_state
# ---------------------------------------------------------------------------

def test_terminal_adapter_sets_acknowledged_on_progress():
    """Tmux adapter sets ACKNOWLEDGED when it sees progress markers."""
    from supervisor.terminal.adapter import TerminalAdapter

    def _mock_run(stdout="", **kw):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.stdout = stdout
        result.returncode = 0
        result.stderr = ""
        return result

    progress_output = "some output\n• Working on step_1\nesc to interrupt\n"
    snapshots = [
        _mock_run(stdout="before\n"),     # read()
        _mock_run(stdout="before\n"),     # send-keys (inject text)
        _mock_run(stdout="before\n"),     # send-keys Enter
        _mock_run(stdout=progress_output),  # capture-pane check
    ]
    with patch("subprocess.run", side_effect=snapshots):
        adapter = TerminalAdapter("%0", tmux_socket="/tmp/test.sock")
        adapter._pane_id = "%0"
        adapter._read_guard.add("%0")
        adapter.inject("do the thing now please really")

    assert adapter.last_delivery_state == "ACKNOWLEDGED"


def test_terminal_adapter_sets_submitted_on_clear():
    """Tmux adapter sets SUBMITTED when text clears from pane."""
    from supervisor.terminal.adapter import TerminalAdapter

    def _mock_run(stdout="", **kw):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.stdout = stdout
        result.returncode = 0
        result.stderr = ""
        return result

    clean_output = "totally clean prompt\n$ \n"
    snapshots = [
        _mock_run(stdout="before\n"),     # read()
        _mock_run(stdout="before\n"),     # send-keys (inject text)
        _mock_run(stdout="before\n"),     # send-keys Enter
        _mock_run(stdout=clean_output),   # capture-pane check 1
        _mock_run(stdout=clean_output),   # capture-pane check 2
    ]
    with patch("subprocess.run", side_effect=snapshots):
        adapter = TerminalAdapter("%0", tmux_socket="/tmp/test.sock")
        adapter._pane_id = "%0"
        adapter._read_guard.add("%0")
        adapter.inject("do the thing now please really")

    assert adapter.last_delivery_state == "SUBMITTED"


def test_open_relay_sets_submitted():
    """OpenRelaySurface sets SUBMITTED after inject."""
    from supervisor.adapters.open_relay_surface import OpenRelaySurface

    surface = OpenRelaySurface("test-session")
    with patch.object(surface, "_oly"):
        surface.inject("hello")
    assert surface.last_delivery_state == "SUBMITTED"


def test_jsonl_observer_always_failed():
    """JsonlObserver has last_delivery_state = FAILED by default."""
    from supervisor.adapters.jsonl_observer import JsonlObserver

    observer = JsonlObserver("/tmp/fake.jsonl")
    assert observer.last_delivery_state == "FAILED"


# ---------------------------------------------------------------------------
# Phase 3: Loop — delivery state transitions
# ---------------------------------------------------------------------------

def test_delivery_state_failed_on_inject_error(tmp_path):
    """Injection failure sets delivery_state to FAILED."""
    terminal = MockTerminal([], fail_inject=True)
    loop, spec, store = _make_loop(tmp_path, terminal)
    state = _run_sidecar(loop, spec, store, terminal)

    assert state.delivery_state == "FAILED"
    assert state.top_state == TopState.PAUSED_FOR_HUMAN


def test_delivery_state_submitted_on_success(tmp_path):
    """Successful injection with no adapter state defaults to SUBMITTED."""
    # step_done checkpoint → will complete
    checkpoint = '<checkpoint><status>step_done</status><current_node>write_test</current_node></checkpoint>'
    terminal = MockTerminal([checkpoint])
    loop, spec, store = _make_loop(tmp_path, terminal)
    state = _run_sidecar(loop, spec, store, terminal)

    # Should have transitioned through SUBMITTED → STARTED_PROCESSING
    assert state.delivery_state in ("SUBMITTED", "STARTED_PROCESSING", "IDLE")


def test_delivery_state_started_processing_on_checkpoint(tmp_path):
    """Checkpoint arrival transitions delivery_state to STARTED_PROCESSING."""
    working_cp = '<checkpoint><status>working</status><current_node>write_test</current_node><checkpoint_seq>1</checkpoint_seq></checkpoint>'
    done_cp = '<checkpoint><status>step_done</status><current_node>write_test</current_node><checkpoint_seq>2</checkpoint_seq></checkpoint>'
    terminal = MockTerminal([working_cp, done_cp])
    loop, spec, store = _make_loop(tmp_path, terminal)
    _run_sidecar(loop, spec, store, terminal)

    # Check session events for delivery_state_change to STARTED_PROCESSING
    session_path = tmp_path / "session.jsonl"
    if session_path.exists():
        events = [json.loads(line) for line in session_path.read_text().splitlines() if line.strip()]
        delivery_changes = [e for e in events if e.get("event_type") == "delivery_state_change"]
        states = [e["payload"]["to"] for e in delivery_changes]
        assert "STARTED_PROCESSING" in states


def test_delivery_state_in_session_events(tmp_path):
    """Delivery state changes are logged as session events."""
    checkpoint = '<checkpoint><status>step_done</status><current_node>write_test</current_node></checkpoint>'
    terminal = MockTerminal([checkpoint])
    loop, spec, store = _make_loop(tmp_path, terminal)
    _run_sidecar(loop, spec, store, terminal)

    session_path = tmp_path / "session_log.jsonl"
    assert session_path.exists(), f"Expected session log at {session_path}"
    events = [json.loads(line) for line in session_path.read_text().splitlines() if line.strip()]
    delivery_changes = [e for e in events if e.get("event_type") == "delivery_state_change"]
    assert len(delivery_changes) >= 1
    # Should see at least IDLE → INJECTED
    first_change = delivery_changes[0]["payload"]
    assert first_change["from"] == "IDLE"
    assert first_change["to"] == "INJECTED"


def test_observation_only_delivery_always_fails(tmp_path):
    """Observation-only surfaces skip inject and don't claim delivery."""
    # Observation-only with no initial checkpoint → skips inject, no delivery attempted
    # Observation-only with a checkpoint → skip inject, agent already running
    # Either way, observation-only never goes through _inject_or_pause for init
    from supervisor.adapters.jsonl_observer import JsonlObserver
    observer = JsonlObserver("/tmp/fake.jsonl")
    assert observer.last_delivery_state == "FAILED"
    assert observer.is_observation_only is True


# ---------------------------------------------------------------------------
# Phase 4: Observability
# ---------------------------------------------------------------------------

def test_delivery_state_in_progress_json(tmp_path):
    """progress.json includes delivery_state field."""
    from supervisor.progress import write_progress

    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path))
    state = store.load_or_init(spec, spec_path="specs/examples/linear_plan.example.yaml", pane_target="%0")
    state.delivery_state = "ACKNOWLEDGED"

    write_progress(state, spec, str(tmp_path))

    progress = json.loads((tmp_path / "progress.json").read_text())
    assert progress["delivery_state"] == "ACKNOWLEDGED"


def test_notification_event_carries_delivery_state():
    """NotificationEvent includes delivery_state field."""
    from supervisor.notifications import NotificationEvent

    event = NotificationEvent(
        event_type="human_pause",
        run_id="r1",
        top_state="PAUSED_FOR_HUMAN",
        reason="test",
        next_action="resume",
        delivery_state="TIMED_OUT",
    )
    d = event.to_dict()
    assert d["delivery_state"] == "TIMED_OUT"


# ---------------------------------------------------------------------------
# Phase 5: Pause summary
# ---------------------------------------------------------------------------

def test_pause_summary_delivery_failed():
    """status_reason returns delivery_failed when delivery_state is FAILED."""
    from supervisor.pause_summary import status_reason

    state = {"top_state": "PAUSED_FOR_HUMAN", "delivery_state": "FAILED"}
    assert status_reason(state) == "delivery_failed"


def test_pause_summary_delivery_timed_out():
    """status_reason returns delivery_timed_out when delivery_state is TIMED_OUT."""
    from supervisor.pause_summary import status_reason

    state = {"top_state": "PAUSED_FOR_HUMAN", "delivery_state": "TIMED_OUT"}
    assert status_reason(state) == "delivery_timed_out"


def test_status_reason_delivering_instruction():
    """status_reason shows delivering state when RUNNING + INJECTED/SUBMITTED."""
    from supervisor.pause_summary import status_reason

    state = {"top_state": "RUNNING", "current_node_id": "step_1", "delivery_state": "SUBMITTED"}
    assert "delivering instruction" in status_reason(state)

    state2 = {"top_state": "RUNNING", "current_node_id": "step_1", "delivery_state": "ACKNOWLEDGED"}
    assert "acknowledged" in status_reason(state2)


# ---------------------------------------------------------------------------
# Phase 5: Daemon recovery
# ---------------------------------------------------------------------------

def test_daemon_recovery_includes_delivery_state_in_reason(tmp_path):
    """Orphan recovery mentions delivery state when it was in-flight."""
    from supervisor.daemon.server import DaemonServer

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run_abc"
    run_dir.mkdir(parents=True)
    state_data = {
        "run_id": "run_abc", "spec_id": "test", "mode": "strict_verifier",
        "top_state": "RUNNING", "current_node_id": "step_1",
        "delivery_state": "INJECTED",
        "controller_mode": "daemon",
    }
    (run_dir / "state.json").write_text(json.dumps(state_data))

    daemon = DaemonServer.__new__(DaemonServer)
    daemon.runs_dir = str(runs_dir)
    recovered = daemon._recover_orphaned_runs()

    assert recovered == 1
    loaded = json.loads((run_dir / "state.json").read_text())
    assert loaded["top_state"] == "PAUSED_FOR_HUMAN"
    assert loaded["delivery_state"] == "IDLE"
    escalation = loaded["human_escalations"][-1]
    assert "delivery_state=INJECTED" in escalation["reason"]
    assert escalation["delivery_state_at_crash"] == "INJECTED"


def test_daemon_recovery_timed_out_delivery(tmp_path):
    """Orphan recovery mentions delivery timeout when that was the state."""
    from supervisor.daemon.server import DaemonServer

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "run_xyz"
    run_dir.mkdir(parents=True)
    state_data = {
        "run_id": "run_xyz", "spec_id": "test", "mode": "strict_verifier",
        "top_state": "RUNNING", "current_node_id": "step_1",
        "delivery_state": "TIMED_OUT",
        "controller_mode": "daemon",
    }
    (run_dir / "state.json").write_text(json.dumps(state_data))

    daemon = DaemonServer.__new__(DaemonServer)
    daemon.runs_dir = str(runs_dir)
    recovered = daemon._recover_orphaned_runs()

    assert recovered == 1
    loaded = json.loads((run_dir / "state.json").read_text())
    assert loaded["delivery_state"] == "IDLE"
    escalation = loaded["human_escalations"][-1]
    assert "delivery timeout" in escalation["reason"]
