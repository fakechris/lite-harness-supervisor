"""Tests for the sidecar loop with a mock terminal adapter."""
from __future__ import annotations

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.domain.enums import TopState


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
    loop = SupervisorLoop(store)

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
