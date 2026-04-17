"""Tests for the sidecar loop with a mock terminal adapter."""
from __future__ import annotations

import json

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.domain.enums import TopState
from supervisor.interventions import AutoInterventionManager
from supervisor.notifications import NotificationManager


class MockTerminal:
    """Simulates a tmux pane that produces scripted output."""

    def __init__(self, outputs: list[str]):
        self._outputs = list(outputs)
        self._index = 0
        self._read_done = False
        self.injected: list[str] = []
        self.keys_sent: list[str] = []

    def read(self, lines: int = 100) -> str:
        self._read_done = True
        if self._index < len(self._outputs):
            text = self._outputs[self._index]
            self._index += 1
            return text
        return ""

    def type_text(self, text: str) -> None:
        assert self._read_done, "read guard violated"
        self.injected.append(text)
        self._read_done = False

    def send_keys(self, *keys: str) -> None:
        assert self._read_done, "read guard violated"
        self.keys_sent.extend(keys)
        self._read_done = False

    def inject(self, text: str) -> None:
        """Type text + Enter in one guarded operation (matches real adapter)."""
        assert self._read_done, "read guard violated"
        self.injected.append(text)
        self.keys_sent.append("Enter")
        self._read_done = False


class ConsumeTrackingTerminal(MockTerminal):
    def __init__(self, outputs: list[str]):
        super().__init__(outputs)
        self.consume_calls = 0
        self._inject_calls = 0

    def inject(self, text: str) -> None:
        self._inject_calls += 1
        if self._inject_calls > 1:
            raise RuntimeError("delivery failed")
        super().inject(text)

    def consume_checkpoint(self) -> None:
        self.consume_calls += 1


class StickyCheckpointTerminal(MockTerminal):
    def __init__(self, checkpoint_text: str):
        super().__init__([])
        self.checkpoint_text = checkpoint_text
        self.read_count = 0
        self.consume_calls = 0
        self.consumed = False

    def read(self, lines: int = 100) -> str:
        self._read_done = True
        self.read_count += 1
        if self.consumed:
            return ""
        return self.checkpoint_text

    def consume_checkpoint(self) -> None:
        self.consume_calls += 1
        self.consumed = True


class RecordingChannel:
    def __init__(self):
        self.events = []

    def notify(self, event) -> None:
        self.events.append(event)


def _make_checkpoint(status: str, node: str, summary: str) -> str:
    return (
        f"<checkpoint>\n"
        f"status: {status}\n"
        f"current_node: {node}\n"
        f"summary: {summary}\n"
        f"evidence:\n"
        f"  - verifier: ok\n"
        f"candidate_next_actions:\n"
        f"  - continue\n"
        f"needs:\n"
        f"  - none\n"
        f"question_for_supervisor:\n"
        f"  - none\n"
        f"</checkpoint>\n"
    )


def _make_two_checkpoints(first_summary: str, second_summary: str, *, node: str = "write_test") -> str:
    return (
        _make_checkpoint("working", node, first_summary)
        + "\nnoise\n"
        + _make_checkpoint("working", node, second_summary)
    )


def _make_checkpoint_with_seq(status: str, node: str, summary: str, seq: int) -> str:
    return (
        f"<checkpoint>\n"
        f"checkpoint_seq: {seq}\n"
        f"status: {status}\n"
        f"current_node: {node}\n"
        f"summary: {summary}\n"
        f"evidence:\n"
        f"  - verifier: ok\n"
        f"candidate_next_actions:\n"
        f"  - continue\n"
        f"needs:\n"
        f"  - none\n"
        f"question_for_supervisor:\n"
        f"  - none\n"
        f"</checkpoint>\n"
    )


def test_sidecar_full_plan(tmp_path):
    """Walk through a 3-step linear plan via sidecar loop."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    # Scripted pane outputs: each step reports step_done
    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        # After verification passes and instruction is injected, agent works...
        _make_checkpoint("step_done", "implement_feature", "feature implemented"),
        _make_checkpoint("step_done", "final_verify", "all verified"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)
    assert final.top_state == TopState.COMPLETED
    assert set(final.done_node_ids) == {"write_test", "implement_feature", "final_verify"}
    # Supervisor should have injected instructions after first two steps
    assert len(terminal.injected) >= 2


def test_sidecar_escalation(tmp_path):
    """Agent reports needing external input → supervisor pauses."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=NotificationManager([channel]))

    terminal = MockTerminal([
        (
            "<checkpoint>\n"
            "status: blocked\n"
            "current_node: write_test\n"
            "summary: need credentials\n"
            "evidence:\n"
            "  - none\n"
            "candidate_next_actions:\n"
            "  - wait for user\n"
            "needs:\n"
            "  - 需要你提供 token\n"
            "question_for_supervisor:\n"
            "  - 需要你提供 API token 和权限\n"
            "</checkpoint>\n"
        ),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)
    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    # No instruction should have been injected (escalated to human)
    assert len(terminal.injected) == 0
    assert channel.events
    assert channel.events[-1].reason == "checkpoint says blocked"

    session_events = store.session_log_path.read_text().splitlines()
    assert any('"event_type": "human_pause"' in line for line in session_events)


def test_sidecar_skips_duplicate_checkpoints(tmp_path):
    """Same checkpoint summary is not re-processed."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    # Same checkpoint repeated — should only process once, then the
    # step_done advances, and the second different checkpoint processes
    terminal = MockTerminal([
        _make_checkpoint("working", "write_test", "still working"),
        _make_checkpoint("working", "write_test", "still working"),  # duplicate
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)
    assert final.top_state == TopState.COMPLETED


def test_sidecar_processes_multiple_checkpoints_in_single_read(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    terminal = MockTerminal([
        _make_two_checkpoints("first progress", "second progress"),
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)
    assert final.top_state == TopState.COMPLETED
    events = [
        line for line in store.event_log_path.read_text().splitlines()
        if "first progress" in line or "second progress" in line
    ]
    assert len(events) == 2


def test_sidecar_injects_initial_instruction_before_first_checkpoint(tmp_path):
    """The first node objective should be injected even when the pane is initially idle."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    terminal = MockTerminal([
        "",
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)
    assert final.top_state == TopState.COMPLETED
    assert terminal.injected[0].startswith("write a failing test")


def test_initial_instruction_includes_checkpoint_protocol_context(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    terminal = MockTerminal([
        "",
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    injected = terminal.injected[0]
    assert "current_node: write_test" in injected
    assert "<checkpoint>" in injected
    assert "step_done" in injected


def test_sidecar_does_not_consume_checkpoint_buffer_when_followup_inject_fails(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    terminal = ConsumeTrackingTerminal([
        "",
        _make_checkpoint("step_done", "write_test", "wrote the test"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert terminal.consume_calls == 0


def test_sidecar_reinjects_guidance_after_working_checkpoint(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    terminal = MockTerminal([
        "",
        _make_checkpoint("working", "write_test", "making progress"),
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    assert any(
        "Continue with the highest-priority remaining action" in injected
        for injected in terminal.injected
    )
    session_events = [
        json.loads(line)
        for line in store.session_log_path.read_text().splitlines()
    ]
    continue_injections = [
        event for event in session_events
        if event["event_type"] == "injection"
        and event["payload"].get("trigger_type") == "continue"
    ]
    assert len(continue_injections) == 1


def test_sidecar_drops_deferred_continue_when_same_batch_advances_node(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    terminal = MockTerminal([
        _make_checkpoint("working", "write_test", "making progress")
        + _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    session_events = [
        json.loads(line)
        for line in store.session_log_path.read_text().splitlines()
    ]
    continue_injections = [
        event for event in session_events
        if event["event_type"] == "injection"
        and event["payload"].get("trigger_type") == "continue"
    ]
    assert continue_injections == []


def test_sidecar_notifies_on_checkpoint_mismatch_pause(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(
        spec,
        spec_path="/tmp/plan.yaml",
        pane_target="%0",
        surface_type="tmux",
    )
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=NotificationManager([channel]))

    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("working", "final_verify", "mismatch one"),
        _make_checkpoint("working", "final_verify", "mismatch two"),
        _make_checkpoint("working", "final_verify", "mismatch three"),
        _make_checkpoint("working", "final_verify", "mismatch four"),
        _make_checkpoint("working", "final_verify", "mismatch five"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert channel.events
    assert channel.events[-1].reason == "node mismatch persisted for 5 checkpoints"
    # Node-mismatch pauses are `recovery` class — operator's first move is
    # observe, not a blind resume that would loop into the same fault.
    assert channel.events[-1].pause_class == "recovery"
    assert channel.events[-1].next_action == f"thin-supervisor observe {final.run_id}"


def test_sidecar_discards_stale_mismatch_batch_after_auto_intervention(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(
        spec,
        spec_path="/tmp/plan.yaml",
        pane_target="%0",
        surface_type="tmux",
    )
    loop = SupervisorLoop(
        store,
        auto_intervention_manager=AutoInterventionManager(
            mode="notify_then_ai",
            max_auto_interventions=1,
        ),
    )

    stale_batch = "".join(
        _make_checkpoint("working", "final_verify", f"stale mismatch {idx}")
        for idx in range(10)
    )
    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        stale_batch,
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    assert final.auto_intervention_count == 0
    session_events = [
        json.loads(line)
        for line in store.session_log_path.read_text().splitlines()
    ]
    auto_interventions = [
        event for event in session_events
        if event["event_type"] == "auto_intervention"
    ]
    assert len(auto_interventions) == 1


def test_sidecar_persists_node_mismatch_count_across_loop_restarts(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    class _StopAfter:
        def __init__(self, terminal, limit):
            self.terminal = terminal
            self.limit = limit

        def is_set(self):
            return self.terminal.read_count >= self.limit

    terminal = StickyCheckpointTerminal(
        _make_checkpoint("working", "final_verify", "same mismatch kept in buffer")
    )
    partial = loop.run_sidecar(
        spec,
        state,
        terminal,
        poll_interval=0,
        read_lines=50,
        stop_event=_StopAfter(terminal, 3),
    )

    # Fresh attach lands in ATTACHED; node mismatches don't emit execution
    # evidence, so no CONTINUE fires and the run stays on the attach side of
    # the boundary.
    assert partial.top_state == TopState.ATTACHED
    assert partial.node_mismatch_count == 3
    assert partial.last_mismatch_node_id == "final_verify"

    terminal2 = StickyCheckpointTerminal(
        _make_checkpoint("working", "final_verify", "same mismatch kept in buffer")
    )
    final = loop.run_sidecar(
        spec,
        partial,
        terminal2,
        poll_interval=0,
        read_lines=50,
        stop_event=_StopAfter(terminal2, 3),
    )

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert "node mismatch persisted for 5 checkpoints" in final.human_escalations[-1]["reason"]


def test_sidecar_counts_flapping_wrong_nodes_toward_mismatch_threshold(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    terminal = MockTerminal([
        _make_checkpoint("working", "final_verify", "wrong node 1"),
        _make_checkpoint("working", "implement_feature", "wrong node 2"),
        _make_checkpoint("working", "final_verify", "wrong node 3"),
        _make_checkpoint("working", "implement_feature", "wrong node 4"),
        _make_checkpoint("working", "final_verify", "wrong node 5"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert final.node_mismatch_count >= 5
    assert final.last_mismatch_node_id == "final_verify"
    assert "node mismatch persisted for 5 checkpoints" in final.human_escalations[-1]["reason"]


def test_sidecar_progress_checkpoint_preserves_escalation_history(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.human_escalations = [{"reason": "earlier pause"}]
    loop = SupervisorLoop(store)

    class _StopAfterContinue:
        def __init__(self, terminal):
            self.terminal = terminal

        def is_set(self):
            return len(self.terminal.injected) >= 1

    terminal = MockTerminal([
        _make_checkpoint("working", "write_test", "resumed progress"),
    ])

    final = loop.run_sidecar(
        spec, state, terminal, poll_interval=0, read_lines=50, stop_event=_StopAfterContinue(terminal)
    )

    assert final.top_state == TopState.RUNNING
    assert final.human_escalations == [{"reason": "earlier pause"}]


def test_sidecar_accepts_checkpoint_seq_reset_after_worker_restart(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.checkpoint_seq = 7
    loop = SupervisorLoop(store)

    class _StopAfter:
        def __init__(self, terminal):
            self.terminal = terminal

        def is_set(self):
            return self.terminal.read_count >= 2

    terminal = StickyCheckpointTerminal(
        _make_checkpoint_with_seq("step_done", "write_test", "worker restarted", 1)
    )
    stop_event = _StopAfter(terminal)

    final = loop.run_sidecar(
        spec, state, terminal, poll_interval=0, read_lines=50, stop_event=stop_event
    )

    assert "write_test" in final.done_node_ids
    assert final.current_node_id == "implement_feature"
    assert final.checkpoint_seq == 1


def test_sidecar_pauses_after_idle_timeout(tmp_path, monkeypatch):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)
    terminal = MockTerminal([])

    ticks = {"value": 0}

    def _fake_monotonic():
        ticks["value"] += 0.1
        return ticks["value"]

    monkeypatch.setattr("supervisor.loop.time.monotonic", _fake_monotonic)

    final = loop.run_sidecar(
        spec,
        state,
        terminal,
        poll_interval=0,
        read_lines=50,
        idle_timeout_sec=3,
    )

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert "idle timeout" in final.human_escalations[-1]["reason"]
    session_events = store.session_log_path.read_text()
    assert "agent_idle_timeout" in session_events


def test_sidecar_zero_poll_interval_falls_back_to_idle_guard_and_nonzero_sleep(tmp_path, monkeypatch):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)
    terminal = MockTerminal([])

    ticks = {"value": 0}
    sleep_calls: list[float] = []

    def _fake_monotonic():
        ticks["value"] += 0.1
        return ticks["value"]

    def _fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr("supervisor.loop.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("supervisor.loop.time.sleep", _fake_sleep)

    final = loop.run_sidecar(
        spec,
        state,
        terminal,
        poll_interval=0,
        read_lines=50,
    )

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert "idle timeout" in final.human_escalations[-1]["reason"]
    assert sleep_calls
    assert all(seconds > 0 for seconds in sleep_calls)


def test_sidecar_small_positive_poll_interval_is_floored(tmp_path, monkeypatch):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)
    terminal = MockTerminal([])

    ticks = {"value": 0}
    sleep_calls: list[float] = []

    def _fake_monotonic():
        ticks["value"] += 0.1
        return ticks["value"]

    def _fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr("supervisor.loop.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("supervisor.loop.time.sleep", _fake_sleep)

    final = loop.run_sidecar(
        spec,
        state,
        terminal,
        poll_interval=0.001,
        read_lines=50,
        idle_timeout_sec=0.3,
    )

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert "idle timeout" in final.human_escalations[-1]["reason"]
    assert sleep_calls
    assert all(seconds >= 0.01 for seconds in sleep_calls)


def test_sidecar_none_poll_interval_uses_safe_defaults(tmp_path, monkeypatch):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)
    terminal = MockTerminal([])

    ticks = {"value": 0}
    sleep_calls: list[float] = []

    def _fake_monotonic():
        ticks["value"] += 0.1
        return ticks["value"]

    def _fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr("supervisor.loop.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("supervisor.loop.time.sleep", _fake_sleep)

    final = loop.run_sidecar(
        spec,
        state,
        terminal,
        poll_interval=None,
        read_lines=50,
    )

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert "idle timeout" in final.human_escalations[-1]["reason"]
    assert sleep_calls
    assert all(seconds >= 0.01 for seconds in sleep_calls)


def test_sidecar_does_not_consume_mismatch_checkpoint_before_threshold(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    class _StopAfter:
        def __init__(self, terminal):
            self.terminal = terminal

        def is_set(self):
            return self.terminal.read_count >= 6

    terminal = StickyCheckpointTerminal(
        _make_checkpoint("working", "final_verify", "same mismatch kept in buffer")
    )
    stop_event = _StopAfter(terminal)

    final = loop.run_sidecar(
        spec, state, terminal, poll_interval=0, read_lines=50, stop_event=stop_event
    )

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert terminal.consume_calls == 0


def test_sidecar_notifies_on_verified_step_and_completion(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(
        spec,
        spec_path="/tmp/plan.yaml",
        pane_target="%0",
        surface_type="tmux",
    )
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=NotificationManager([channel]))

    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    event_types = [event.event_type for event in channel.events]
    assert "step_verified" in event_types
    assert "run_completed" in event_types
    assert channel.events[-1].event_type == "run_completed"
    assert channel.events[-1].reason == "workflow completed"
    assert channel.events[-1].next_action == f"thin-supervisor run summarize {final.run_id}"
    session_events = [
        json.loads(line)["event_type"]
        for line in store.session_log_path.read_text().splitlines()
    ]
    assert "step_verified" in session_events
    assert "run_completed" in session_events


# ---------------------------------------------------------------------------
# Slice 3: RECOVERY_NEEDED + broader auto-intervention
# ---------------------------------------------------------------------------


def test_enter_recovery_requires_pause_class_recovery(tmp_path):
    """_enter_recovery must reject payloads without pause_class='recovery'.

    RECOVERY_NEEDED is reserved for auto-recoverable triggers. Allowing other
    pause classes here would collapse the taxonomy we just introduced.
    """
    import pytest

    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    with pytest.raises(ValueError, match="pause_class='recovery'"):
        loop._enter_recovery(state, {"reason": "x", "pause_class": "business"})
    with pytest.raises(ValueError, match="pause_class='recovery'"):
        loop._enter_recovery(state, {"reason": "x"})


def test_recovery_needed_auto_recovers_node_mismatch_without_human_pause(tmp_path):
    """A node-mismatch storm with an active auto-intervention recipe should
    recover silently — operator is never notified with `human_pause`."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(
        spec,
        spec_path="/tmp/plan.yaml",
        pane_target="%0",
        surface_type="tmux",
    )
    channel = RecordingChannel()
    loop = SupervisorLoop(
        store,
        notification_manager=NotificationManager([channel]),
        auto_intervention_manager=AutoInterventionManager(mode="notify_then_ai"),
    )

    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("working", "final_verify", "mismatch 1"),
        _make_checkpoint("working", "final_verify", "mismatch 2"),
        _make_checkpoint("working", "final_verify", "mismatch 3"),
        _make_checkpoint("working", "final_verify", "mismatch 4"),
        _make_checkpoint("working", "final_verify", "mismatch 5"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    event_types = [event.event_type for event in channel.events]
    assert "human_pause" not in event_types
    assert "auto_intervention" in event_types
    # Session log should contain the recovery_needed transition
    session_types = [
        json.loads(line)["event_type"]
        for line in store.session_log_path.read_text().splitlines()
    ]
    assert "recovery_needed" in session_types


def test_recovery_needed_checkpoint_arrival_does_not_crash(tmp_path):
    """Crash-resume scenario: if a state is persisted as RECOVERY_NEEDED
    (sidecar died between `_enter_recovery` and the follow-up transition),
    the next checkpoint must not crash on an illegal `RECOVERY_NEEDED → GATING`
    transition. `handle_event` preserves the state instead of forcing a
    transition, so the loop can re-run the recovery path cleanly.
    """
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    # Stage the state directly into RECOVERY_NEEDED to simulate the post-
    # crash persistence shape. RUNNING is the legal source state for
    # `_enter_recovery`; call it through the legitimate entry point so the
    # transition rules stay honest.
    from supervisor.domain.state_machine import transition_top_state
    transition_top_state(state, TopState.RUNNING, reason="simulate running")
    loop._enter_recovery(state, {"reason": "simulated crash", "pause_class": "recovery"})
    assert state.top_state == TopState.RECOVERY_NEEDED

    event = {
        "type": "agent_output",
        "payload": {
            "checkpoint": {
                "status": "working",
                "current_node": state.current_node_id,
                "evidence": [{"verifier": "ok"}],
            }
        },
    }
    # No exception: the arriving checkpoint is absorbed without forcing a
    # transition. State stays RECOVERY_NEEDED for the recovery path to handle.
    loop.handle_event(state, event)
    assert state.top_state == TopState.RECOVERY_NEEDED


def test_recovery_needed_falls_through_to_pause_when_budget_exhausted(tmp_path):
    """If auto-intervention has no plan left, recovery must fall through to
    a `PAUSED_FOR_HUMAN` with `pause_class='recovery'`."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(
        spec,
        spec_path="/tmp/plan.yaml",
        pane_target="%0",
        surface_type="tmux",
    )
    # max=0 means every recipe request is refused — same shape as budget
    # exhaustion.
    channel = RecordingChannel()
    loop = SupervisorLoop(
        store,
        notification_manager=NotificationManager([channel]),
        auto_intervention_manager=AutoInterventionManager(
            mode="notify_then_ai",
            max_auto_interventions=0,
        ),
    )

    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("working", "final_verify", "mismatch 1"),
        _make_checkpoint("working", "final_verify", "mismatch 2"),
        _make_checkpoint("working", "final_verify", "mismatch 3"),
        _make_checkpoint("working", "final_verify", "mismatch 4"),
        _make_checkpoint("working", "final_verify", "mismatch 5"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert final.human_escalations[-1]["pause_class"] == "recovery"
    # Both events should fire: first recovery_needed, then human_pause
    session_types = [
        json.loads(line)["event_type"]
        for line in store.session_log_path.read_text().splitlines()
    ]
    assert session_types.count("recovery_needed") >= 1
    assert "human_pause" in session_types
    # Operator notification must carry the recovery-class routing
    assert channel.events[-1].event_type == "human_pause"
    assert channel.events[-1].pause_class == "recovery"


# ---------------------------------------------------------------------------
# Slice 4 — ATTACHED + first-execution gate
# ---------------------------------------------------------------------------


def _admin_only_checkpoint(node: str, summary: str) -> str:
    """First-checkpoint-after-attach that cites only admin artifacts.

    This is the Phase 17 incident shape: attach + clarify + plan, but no
    concrete work on the current node.  The ATTACHED gate must treat this as
    admin-only evidence and re-inject, not CONTINUE.
    """
    return (
        f"<checkpoint>\n"
        f"status: working\n"
        f"current_node: {node}\n"
        f"summary: {summary}\n"
        f"evidence:\n"
        f"  - attach: opened pane tmux://alpha\n"
        f"  - clarify: confirmed spec scope\n"
        f"  - plan: drafted step order\n"
        f"candidate_next_actions:\n"
        f"  - continue\n"
        f"needs:\n"
        f"  - none\n"
        f"question_for_supervisor:\n"
        f"  - none\n"
        f"</checkpoint>\n"
    )


def test_fresh_attach_lands_in_attached_not_running(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    assert state.top_state == TopState.READY

    # No checkpoint yet — loop injects, ATTACHED persists.
    class _StopOnInject:
        def __init__(self, terminal):
            self.terminal = terminal

        def is_set(self):
            return bool(self.terminal.injected)

    terminal = MockTerminal([""])
    loop = SupervisorLoop(store)
    final = loop.run_sidecar(
        spec, state, terminal, poll_interval=0, read_lines=50,
        stop_event=_StopOnInject(terminal),
    )
    assert final.top_state == TopState.ATTACHED
    assert len(terminal.injected) == 1


def test_attached_admin_only_checkpoint_reinjects_without_pause(tmp_path):
    """Phase 17 regression: attach → admin-only checkpoint → RE_INJECT, not pause.

    The gate must emit RE_INJECT (not CONTINUE, not RETRY), no operator
    notification fires, and the retry budget is untouched.  State remains
    ATTACHED so the next checkpoint is scrutinized again.
    """
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=NotificationManager([channel]))

    class _StopAfterReInject:
        def __init__(self, terminal):
            self.terminal = terminal

        def is_set(self):
            # First inject is the initial handoff, second is the RE_INJECT.
            return len(self.terminal.injected) >= 2

    terminal = MockTerminal([
        "",  # initial inject
        _admin_only_checkpoint("write_test", "attached and reviewed plan"),
    ])

    partial = loop.run_sidecar(
        spec, state, terminal, poll_interval=0, read_lines=50,
        stop_event=_StopAfterReInject(terminal),
    )

    assert partial.top_state == TopState.ATTACHED
    # Retry budget untouched — RE_INJECT is not RETRY.
    assert partial.current_attempt == 0
    assert partial.retry_budget.used_global == 0
    # No operator notification for an attach-boundary re-inject.
    assert not any(e.event_type == "human_pause" for e in channel.events)
    # Decision log must show a RE_INJECT.
    decisions = [
        json.loads(line) for line in store.decision_log_path.read_text().splitlines()
    ]
    assert any(d.get("decision", "").upper() == "RE_INJECT" for d in decisions)


def test_attached_real_execution_advances_to_running(tmp_path):
    """ATTACHED + concrete execution evidence → CONTINUE → RUNNING.

    Stops after the first gate decision to capture the ATTACHED→RUNNING
    transition explicitly, rather than letting the run complete and lose the
    intermediate state.
    """
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    class _StopAfterGate:
        def __init__(self, store):
            self.store = store

        def is_set(self):
            path = self.store.session_log_path
            if not path.exists():
                return False
            for line in path.read_text().splitlines():
                if '"event_type": "gate_decision"' in line:
                    record = json.loads(line)
                    if record.get("payload", {}).get("decision", "").upper() == "CONTINUE":
                        return True
            return False

    terminal = MockTerminal([
        "",
        # _make_checkpoint uses "verifier: ok" — real execution evidence.
        _make_checkpoint("working", "write_test", "started writing the test"),
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])
    partial = loop.run_sidecar(
        spec, state, terminal, poll_interval=0, read_lines=50,
        stop_event=_StopAfterGate(store),
    )
    # After a CONTINUE on real execution evidence, we must be in RUNNING
    # (or a post-RUNNING state like VERIFYING) — never back-sliding to ATTACHED.
    assert partial.top_state in (
        TopState.RUNNING, TopState.VERIFYING, TopState.COMPLETED,
    )

    # At least one CONTINUE was emitted out of ATTACHED.
    decisions = [
        json.loads(line)
        for line in store.decision_log_path.read_text().splitlines()
    ]
    assert any(d.get("decision", "").upper() == "CONTINUE" for d in decisions)


def test_re_inject_loop_caps_and_pauses_recovery(tmp_path):
    """If the agent keeps emitting admin-only checkpoints after attach, the
    supervisor must not RE_INJECT forever. After MAX_RE_INJECTS attempts it
    pauses with pause_class='recovery' so an operator can intervene.

    Without this cap, a responsive-but-off-task agent would loop gate →
    RE_INJECT → inject → admin-only cp → gate → … indefinitely; the
    delivery-ack timeout is the only other bound and it only fires when the
    agent goes silent.
    """
    from supervisor.loop import MAX_RE_INJECTS

    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=NotificationManager([channel]))

    # Feed MAX_RE_INJECTS + 2 admin-only checkpoints so the cap is definitely
    # tripped. Initial inject reads "" then each subsequent admin-only cp
    # triggers another RE_INJECT until the cap fires.
    outputs: list[str] = [""]  # initial handoff reads empty
    for _ in range(MAX_RE_INJECTS + 2):
        outputs.append(_admin_only_checkpoint("write_test", "still reviewing plan"))
    terminal = MockTerminal(outputs)

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    # Recovery pause — not business/safety/review.
    assert final.human_escalations
    assert final.human_escalations[-1].get("pause_class") == "recovery"
    # Retry budget still untouched — RE_INJECT must not bleed into RETRY
    # semantics even when the cap trips.
    assert final.current_attempt == 0
    assert final.retry_budget.used_global == 0
    # Operator notification fired with the recovery class.
    pause_events = [e for e in channel.events if e.event_type == "human_pause"]
    assert pause_events and pause_events[-1].pause_class == "recovery"


def test_attached_step_done_admin_only_reinjects_not_verify(tmp_path):
    """Reviewer P1-2: `status: step_done` on the first checkpoint must NOT
    short-circuit into VERIFY_STEP when evidence is admin-only.  That would
    bypass the attach boundary entirely — an agent claiming "step done" with
    no execution evidence on the current node should be RE_INJECTed, not
    verified.
    """
    from supervisor.domain.enums import DecisionType
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    state.top_state = TopState.ATTACHED
    state.last_agent_checkpoint = {
        "status": "step_done",
        "current_node": state.current_node_id,
        "summary": "claimed done without doing work",
        "evidence": [
            {"attach": "tmux://alpha"},
            {"plan": "drafted step order"},
        ],
    }

    decision = loop.gate(spec, state)
    assert decision.decision == DecisionType.RE_INJECT.value
    assert "step_done" in decision.reason


def test_attached_workflow_done_admin_only_reinjects_not_verify(tmp_path):
    """Reviewer P1-2: same guard must cover `status: workflow_done`. A
    first-checkpoint workflow_done with admin-only evidence is exactly the
    Phase 17 shape at the terminal of the plan instead of the head — still
    must RE_INJECT, not VERIFY_STEP.
    """
    from supervisor.domain.enums import DecisionType
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    state.top_state = TopState.ATTACHED
    state.last_agent_checkpoint = {
        "status": "workflow_done",
        "current_node": state.current_node_id,
        "summary": "claimed workflow done without doing any nodes",
        "evidence": [
            {"clarify": "confirmed spec scope"},
            {"plan": "drafted step order"},
        ],
    }

    decision = loop.gate(spec, state)
    assert decision.decision == DecisionType.RE_INJECT.value
    assert "workflow_done" in decision.reason


def test_attached_admin_only_with_missing_input_text_escalates_at_loop_level(tmp_path):
    """Regression for the loop-integration gap found after PR #71.

    ContinueGate already ran escalation-before-attach, but the supervisor
    loop has its OWN ATTACHED admin-only guard that short-circuits to
    RE_INJECT before ContinueGate is called.  That means a first checkpoint
    with admin-only evidence AND `need credentials` text in
    needs / question_for_supervisor would (incorrectly) route to RE_INJECT
    instead of ESCALATE_TO_HUMAN.

    This test exercises the real runtime entry point `loop.gate()` — the
    previous P2-3 test only covered `ContinueGate.decide()` in isolation
    and never hit the loop-level short-circuit.  Covers both
    `status: working` and `status: step_done` variants.
    """
    from supervisor.domain.enums import DecisionType
    spec = load_spec("specs/examples/linear_plan.example.yaml")

    # Monotonic counter for unique runtime dirs; `id(object())` can recycle
    # addresses across sequential calls and would collide.
    counter = iter(range(1000))

    def _mk_state():
        store = StateStore(str(tmp_path / f"runtime_{next(counter)}"))
        s = store.load_or_init(spec)
        s.top_state = TopState.ATTACHED
        return SupervisorLoop(store), s

    # status: working + admin-only + missing-input text → ESCALATE
    loop, state = _mk_state()
    state.last_agent_checkpoint = {
        "status": "working",
        "current_node": state.current_node_id,
        "summary": "attached and reviewed plan",
        "evidence": [
            {"attach": "tmux://alpha"},
            {"plan": "drafted step order"},
        ],
        "needs": ["need credentials for upstream API"],
        "question_for_supervisor": ["need access token to proceed"],
    }
    d = loop.gate(spec, state)
    assert d.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert d.needs_human is True
    assert d.reason == "missing external input"

    # status: step_done + admin-only + missing-input text → ESCALATE
    # (must not fall through to the VERIFY_STEP short-circuit either)
    loop, state = _mk_state()
    state.last_agent_checkpoint = {
        "status": "step_done",
        "current_node": state.current_node_id,
        "summary": "claimed done",
        "evidence": [
            {"clarify": "confirmed spec scope"},
        ],
        "needs": ["need credentials to proceed"],
    }
    d = loop.gate(spec, state)
    assert d.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert d.reason == "missing external input"

    # status: working + admin-only + DANGEROUS_ACTION signal → ESCALATE.
    # Locks loop-level coverage for every escalation class ContinueGate sees;
    # a new class added to `classify_text` must not slip through one layer.
    loop, state = _mk_state()
    state.last_agent_checkpoint = {
        "status": "working",
        "current_node": state.current_node_id,
        "summary": "attached and reviewed plan",
        "evidence": [
            {"attach": "tmux://alpha"},
            {"plan": "drafted step order"},
        ],
        "needs": ["force push to main"],
    }
    d = loop.gate(spec, state)
    assert d.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert d.reason == "dangerous irreversible action"

    # status: working + admin-only + BLOCKED signal (via summary, not the
    # status field) → ESCALATE.  The cp_status == "blocked" branch at the
    # top of gate() covers the explicit-status shape; this covers
    # classifier-matched BLOCKED on a non-blocked status.
    loop, state = _mk_state()
    state.last_agent_checkpoint = {
        "status": "working",
        "current_node": state.current_node_id,
        "summary": "blocked: cannot proceed",
        "evidence": [
            {"attach": "tmux://alpha"},
        ],
    }
    d = loop.gate(spec, state)
    assert d.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert d.reason == "agent reported blocked"

    # Negative control: ATTACHED + admin-only + NO escalation signal → still
    # RE_INJECT.  The fix must not over-escalate on plain admin-only output.
    loop, state = _mk_state()
    state.last_agent_checkpoint = {
        "status": "working",
        "current_node": state.current_node_id,
        "summary": "attached and reviewed plan",
        "evidence": [
            {"attach": "tmux://alpha"},
            {"plan": "drafted step order"},
        ],
    }
    d = loop.gate(spec, state)
    assert d.decision == DecisionType.RE_INJECT.value

    # Escalation signal carried ONLY in the agent_ask question payload (not in
    # the checkpoint fields) must still escalate.  ContinueGate classifies both
    # checkpoint and question; the loop-level guard must match.
    loop, state = _mk_state()
    state.last_agent_checkpoint = {
        "status": "working",
        "current_node": state.current_node_id,
        "summary": "attached and reviewed plan",
        "evidence": [
            {"attach": "tmux://alpha"},
            {"plan": "drafted step order"},
        ],
    }
    state.last_event = {
        "type": "agent_ask",
        "payload": {"question": "need access credentials to upstream API"},
    }
    d = loop.gate(spec, state)
    assert d.decision == DecisionType.ESCALATE_TO_HUMAN.value


def test_attached_step_done_real_evidence_verifies(tmp_path):
    """Counter-example for P1-2: `step_done` with real execution evidence on
    the current node is the legitimate shape — must route to VERIFY_STEP so
    the verifier can confirm (and then ATTACHED → RUNNING).
    """
    from supervisor.domain.enums import DecisionType
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    state.top_state = TopState.ATTACHED
    state.last_agent_checkpoint = {
        "status": "step_done",
        "current_node": state.current_node_id,
        "summary": "wrote the test and it passed",
        "evidence": [
            {"command": "pytest tests/test_foo.py"},
            {"output": "3 passed"},
        ],
    }

    decision = loop.gate(spec, state)
    assert decision.decision == DecisionType.VERIFY_STEP.value


def test_non_fresh_resume_skips_attached(tmp_path):
    """A state that already has a RUNNING checkpoint must not re-enter ATTACHED.

    Covers the 'resume after PAUSED_FOR_HUMAN[business]' path: re-gating a run
    that has already proved it can execute would be a regression for every
    resume flow.
    """
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    # Simulate a run that already advanced past the attach boundary.
    state.top_state = TopState.RUNNING
    store.save(state)

    loop = SupervisorLoop(store)
    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])
    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    # Must complete without ever re-entering ATTACHED from RUNNING.
    assert final.top_state == TopState.COMPLETED
    session_log_lines = store.session_log_path.read_text().splitlines()
    transitions = [
        (json.loads(line)["payload"]["from"], json.loads(line)["payload"]["to"])
        for line in session_log_lines
        if '"top_state_change"' in line
    ]
    assert not any(src == "RUNNING" and dst == "ATTACHED" for src, dst in transitions)


def test_sidecar_boots_in_recovery_needed_pauses_for_human(tmp_path):
    """Crash-resume fail-safe: a run whose persisted top_state is
    RECOVERY_NEEDED must not silently continue. Without the fail-safe the
    sidecar would enter the main loop with no delivery_ack armed and no
    pending checkpoint, spinning forever on an empty pane. Instead it
    must pause for human with pause_class=recovery + rec.crash_during_recovery
    so an operator can decide how to unstick the run.
    """
    from supervisor.protocol.reason_code import REC_CRASH_DURING_RECOVERY

    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    # Simulate a prior sidecar that crashed between _enter_recovery and
    # its follow-up transition — the only way RECOVERY_NEEDED ever
    # persists across process boundaries.
    state.top_state = TopState.ATTACHED
    state.top_state = TopState.RECOVERY_NEEDED
    store.save(state)

    loop = SupervisorLoop(store)
    terminal = MockTerminal([""])  # empty pane — no checkpoint to drive a transition
    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert final.human_escalations
    last = final.human_escalations[-1]
    assert last["pause_class"] == "recovery"
    assert last["reason_code"] == REC_CRASH_DURING_RECOVERY
    # Must not have attempted any inject — no recipe to replay.
    assert terminal.injected == []
