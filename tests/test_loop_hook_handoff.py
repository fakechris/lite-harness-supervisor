"""Loop integration tests for the Stop-hook handoff delivery path."""
from __future__ import annotations

import pytest

from supervisor.domain.enums import DeliveryState
from supervisor.interventions import AutoInterventionManager
from supervisor.loop import SupervisorLoop
from supervisor.notifications import NotificationManager
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


class HookHandoffSurface:
    """Observation-only surface that mimics JsonlObserver hook delivery."""

    is_observation_only = True

    def __init__(self, *, ack_after_calls: int | None = 1):
        self.handoffs: list[tuple[str, str]] = []  # (instruction_id, content)
        self.poll_count = 0
        self._ack_after = ack_after_calls  # None → never ACK
        self.last_delivery_state = DeliveryState.FAILED

    # SessionAdapter-ish
    def read(self, lines: int = 100) -> str:
        return ""

    def inject(self, text: str) -> None:
        self.handoffs.append(("legacy", text))

    def inject_with_id(
        self, text: str, *, instruction_id: str, run_id: str = "", node_id: str = "",
    ) -> None:
        self.handoffs.append((instruction_id, text))

    def poll_delivery(self, instruction_id: str) -> bool:
        self.poll_count += 1
        if self._ack_after is None:
            return False
        if not self.handoffs:
            return False
        last_id = self.handoffs[-1][0]
        if last_id != instruction_id:
            return False
        return self.poll_count >= self._ack_after

    def current_cwd(self) -> str:
        return "/tmp"

    def session_id(self) -> str:
        return "hook-handoff-sid"


def _make_loop(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path))
    loop = SupervisorLoop(
        store=store,
        notification_manager=NotificationManager(),
        auto_intervention_manager=AutoInterventionManager(mode="disabled"),
    )
    return loop, spec, store


def _build_instruction(loop, spec, state):
    contract = spec.acceptance
    policy = loop.policy_engine.determine(loop.worker_profile, contract, state)
    return loop.composer.build(
        spec.get_node(state.current_node_id),
        state,
        triggered_by_decision_id="",
        trigger_type="init",
        policy=policy,
        first_node_delivery=True,
    )


def test_hook_handoff_succeeds_when_ack_arrives(tmp_path, monkeypatch):
    monkeypatch.setattr("supervisor.loop.time.sleep", lambda _s: None)
    loop, spec, store = _make_loop(tmp_path)
    state = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml", pane_target="jsonl"
    )
    surface = HookHandoffSurface(ack_after_calls=2)
    instruction = _build_instruction(loop, spec, state)

    delivered = loop._inject_or_pause(state, surface, instruction, spec=spec)

    assert delivered is True
    assert state.delivery_state == "ACKNOWLEDGED"
    assert surface.handoffs and surface.handoffs[-1][0] == instruction.instruction_id

    session_log = store.session_log_path.read_text()
    assert "injection_hook_handoff" in session_log
    assert "injection_hook_ack" in session_log


def test_hook_handoff_times_out_when_no_ack(tmp_path, monkeypatch):
    monkeypatch.setattr("supervisor.loop.time.sleep", lambda _s: None)
    # Shrink the timeout for the test — deterministic + fast.
    monkeypatch.setattr("supervisor.loop.OBSERVATION_HOOK_ACK_TIMEOUT_SEC", 0.0)
    loop, spec, store = _make_loop(tmp_path)
    state = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml", pane_target="jsonl"
    )
    surface = HookHandoffSurface(ack_after_calls=None)
    instruction = _build_instruction(loop, spec, state)

    delivered = loop._inject_or_pause(state, surface, instruction, spec=spec)

    assert delivered is False
    assert state.delivery_state == "TIMED_OUT"
    session_log = store.session_log_path.read_text()
    assert "injection_hook_handoff" in session_log
    assert "injection_hook_ack_timeout" in session_log


def test_hook_handoff_pauses_when_inject_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("supervisor.loop.time.sleep", lambda _s: None)
    loop, spec, store = _make_loop(tmp_path)
    state = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml", pane_target="jsonl"
    )

    class BrokenSurface(HookHandoffSurface):
        def inject_with_id(self, text, **kw):
            raise OSError("disk full")

    surface = BrokenSurface()
    instruction = _build_instruction(loop, spec, state)

    delivered = loop._inject_or_pause(state, surface, instruction, spec=spec)

    assert delivered is False
    assert state.delivery_state == "FAILED"
