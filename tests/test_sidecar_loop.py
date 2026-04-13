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
        f"  - ran: echo ok\n"
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
        f"  - ran: echo ok\n"
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
    assert "--pane" in channel.events[-1].next_action


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

    assert partial.top_state == TopState.RUNNING
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
        ticks["value"] += 1
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
