from __future__ import annotations

from supervisor.domain.enums import TopState
from supervisor.interventions import AutoInterventionManager
from supervisor.loop import SupervisorLoop
from supervisor.notifications import NotificationManager
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


class MockTerminal:
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

    def inject(self, text: str) -> None:
        assert self._read_done
        self.injected.append(text)
        self.keys_sent.append("Enter")
        self._read_done = False


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


def test_sidecar_blocked_checkpoint_pauses_for_human(tmp_path):
    """Blocked checkpoints must escalate to human — no auto-recovery.

    This aligns with the contract: "If blocked, emit status: blocked.
    Supervisor will escalate to a human."
    """
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

    blocked = (
        "<checkpoint>\n"
        "status: blocked\n"
        "current_node: write_test\n"
        "summary: need credentials\n"
        "evidence:\n"
        "  - none\n"
        "candidate_next_actions:\n"
        "  - wait\n"
        "needs:\n"
        "  - API token\n"
        "question_for_supervisor:\n"
        "  - need API token\n"
        "</checkpoint>\n"
    )
    terminal = MockTerminal([blocked])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    # Blocked = escalate, NOT auto-recover
    assert final.top_state == TopState.PAUSED_FOR_HUMAN
    assert not any("auto-recovery mode" in text for text in terminal.injected)


def test_sidecar_auto_handles_node_mismatch_when_enabled(tmp_path):
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
        notification_manager=NotificationManager([]),
        auto_intervention_manager=AutoInterventionManager(mode="notify_then_ai"),
    )

    terminal = MockTerminal([
        _make_checkpoint("step_done", "write_test", "wrote the test"),
        _make_checkpoint("working", "final_verify", "mismatch one"),
        _make_checkpoint("working", "final_verify", "mismatch two"),
        _make_checkpoint("working", "final_verify", "mismatch three"),
        _make_checkpoint("working", "final_verify", "mismatch four"),
        _make_checkpoint("working", "final_verify", "mismatch five"),
        _make_checkpoint("step_done", "implement_feature", "feature done"),
        _make_checkpoint("step_done", "final_verify", "all done"),
    ])

    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    assert any("Supervisor expected current_node: implement_feature" in text for text in terminal.injected)


def test_maybe_plan_returns_none_when_spec_is_missing(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    manager = AutoInterventionManager(mode="notify_then_ai")
    terminal = MockTerminal([])

    plan = manager.maybe_plan(
        None,
        state,
        {"reason": "retry budget exhausted after failed verification"},
        terminal,
    )

    assert plan is None


def test_maybe_plan_builds_delivery_timeout_recipe(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    manager = AutoInterventionManager(mode="notify_then_ai")
    terminal = MockTerminal([])

    plan = manager.maybe_plan(
        spec,
        state,
        {"reason": "no checkpoint received within delivery timeout after injection"},
        terminal,
    )

    assert plan is not None
    assert plan.reason == "delivery ack timeout auto-recovery"
    assert state.current_node_id in plan.instruction
    assert "progress checkpoint" in plan.instruction


def test_maybe_plan_builds_idle_timeout_recipe(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    manager = AutoInterventionManager(mode="notify_then_ai")
    terminal = MockTerminal([])

    plan = manager.maybe_plan(
        spec,
        state,
        {"reason": "agent idle timeout after 120s without checkpoint or visible output"},
        terminal,
    )

    assert plan is not None
    assert plan.reason == "idle timeout auto-recovery"
    assert "no agent activity" in plan.instruction


def test_maybe_plan_builds_inject_failure_recipe(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    manager = AutoInterventionManager(mode="notify_then_ai")
    terminal = MockTerminal([])

    plan = manager.maybe_plan(
        spec,
        state,
        {"reason": "injection failed: tmux send-keys exited 1"},
        terminal,
    )

    assert plan is not None
    assert plan.reason == "inject failure auto-recovery"
    assert "transient injection error" in plan.instruction


def test_maybe_plan_respects_budget_exhaustion(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.auto_intervention_count = 2  # default max
    manager = AutoInterventionManager(mode="notify_then_ai", max_auto_interventions=2)
    terminal = MockTerminal([])

    plan = manager.maybe_plan(
        spec,
        state,
        {"reason": "no checkpoint received within delivery timeout after injection"},
        terminal,
    )

    # Budget exhausted — no more recipes, recovery must fall through to pause.
    assert plan is None
