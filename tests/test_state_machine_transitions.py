from __future__ import annotations

import json

from supervisor.domain.enums import DecisionType, TopState
from supervisor.domain.models import SupervisorDecision, SupervisorState
from supervisor.domain.state_machine import InvalidTopStateTransition, normalize_top_state, transition_top_state
from supervisor.loop import SupervisorLoop
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


class RecordingChannel:
    def __init__(self):
        self.events = []

    def notify(self, event) -> None:
        self.events.append(event)


def test_normalize_legacy_top_state_values():
    assert normalize_top_state("INIT") == TopState.READY
    assert normalize_top_state("AWAITING_AGENT_EVENT") == TopState.RUNNING


def test_supervisor_state_from_dict_normalizes_legacy_top_state(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    raw = state.to_dict()
    raw["top_state"] = "INIT"

    restored = SupervisorState.from_dict(raw)

    assert restored.top_state == TopState.READY


def test_supervisor_state_from_dict_ignores_legacy_node_status_field(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    raw = state.to_dict()
    raw["node_status"] = "CURRENT_STEP_DONE"

    restored = SupervisorState.from_dict(raw)

    assert restored.top_state == state.top_state
    assert not hasattr(restored, "node_status")


def test_transition_table_rejects_completed_to_running(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.top_state = TopState.COMPLETED

    try:
        transition_top_state(state, TopState.RUNNING, reason="invalid restart")
    except InvalidTopStateTransition as exc:
        assert "COMPLETED -> RUNNING" in str(exc)
    else:
        raise AssertionError("expected InvalidTopStateTransition")


def test_apply_decision_continue_keeps_run_running(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    decision = SupervisorDecision.make(
        decision=DecisionType.CONTINUE.value,
        reason="still working",
        gate_type="continue",
        confidence=0.9,
        next_instruction="Continue with the highest-priority remaining action in the current node.",
    )

    loop.apply_decision(spec, state, decision)

    assert state.top_state == TopState.RUNNING
    assert state.current_node_id == "write_test"
    assert state.last_decision["decision"] == DecisionType.CONTINUE.value
    assert "Continue with the highest-priority remaining action" in state.last_decision["next_instruction"]


def test_apply_decision_finish_runs_finish_gate_instead_of_direct_complete(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.top_state = TopState.GATING
    loop = SupervisorLoop(store)

    decision = SupervisorDecision.make(
        decision=DecisionType.FINISH.value,
        reason="judge thinks everything is done",
        gate_type="continue",
        confidence=0.9,
    )

    loop.apply_decision(spec, state, decision)

    assert state.top_state == TopState.PAUSED_FOR_HUMAN
    assert state.human_escalations
    assert "nodes not done" in state.human_escalations[-1]["reason"]


def test_handle_event_preserves_verifying_state_on_new_checkpoint(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.top_state = TopState.VERIFYING
    loop = SupervisorLoop(store)

    event = {
        "type": "agent_output",
        "payload": {
            "checkpoint": {
                "status": "working",
                "current_node": "write_test",
                "summary": "extra output during verify",
            }
        },
    }

    loop.handle_event(state, event)

    assert state.last_agent_checkpoint["summary"] == "extra output during verify"
    assert state.top_state == TopState.VERIFYING


def test_apply_verification_success_advances_and_notifies(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=None)
    loop.notification_manager.channels = [channel]

    loop.apply_verification(spec, state, {"ok": True, "results": []})

    assert state.top_state == TopState.RUNNING
    assert state.current_node_id == "implement_feature"
    assert state.done_node_ids == ["write_test"]
    assert channel.events[-1].event_type == "step_verified"
    assert "advanced to implement_feature" in channel.events[-1].reason


def test_apply_verification_success_completes_and_notifies(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.current_node_id = "final_verify"
    state.done_node_ids = ["write_test", "implement_feature"]
    state.last_agent_checkpoint = {"status": "workflow_done"}
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=None)
    loop.notification_manager.channels = [channel]

    loop.apply_verification(spec, state, {"ok": True, "results": []})

    assert state.top_state == TopState.COMPLETED
    assert state.done_node_ids == ["write_test", "implement_feature", "final_verify"]
    assert channel.events[-1].event_type == "run_completed"


def test_apply_verification_failure_retries_before_pause(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    loop.apply_verification(spec, state, {"ok": False, "results": [{"type": "command", "ok": False}]})

    assert state.top_state == TopState.RUNNING
    assert state.current_attempt == 1
    assert state.retry_budget.used_global == 1


def test_apply_verification_failure_exhaustion_pauses(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    state.current_attempt = state.retry_budget.per_node - 1
    state.retry_budget.used_global = 0
    channel = RecordingChannel()
    loop = SupervisorLoop(store, notification_manager=None)
    loop.notification_manager.channels = [channel]

    loop.apply_verification(spec, state, {"ok": False, "results": [{"type": "command", "ok": False}]})

    assert state.top_state == TopState.PAUSED_FOR_HUMAN
    assert state.human_escalations
    assert "verification retry budget exhausted" in state.human_escalations[-1]["reason"]
    assert channel.events[-1].event_type == "human_pause"


def test_continue_transition_is_persisted_as_continue_injection(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    class MockTerminal:
        def __init__(self):
            self._read_done = False
            self.injected = []
            self.outputs = [
                "",
                (
                    "<checkpoint>\n"
                    "status: working\n"
                    "current_node: write_test\n"
                    "summary: making progress\n"
                    "evidence:\n"
                    "  - verifier: ok\n"
                    "candidate_next_actions:\n"
                    "  - continue\n"
                    "needs:\n"
                    "  - none\n"
                    "question_for_supervisor:\n"
                    "  - none\n"
                    "</checkpoint>\n"
                ),
                (
                    "<checkpoint>\n"
                    "status: step_done\n"
                    "current_node: write_test\n"
                    "summary: done\n"
                    "evidence:\n"
                    "  - verifier: ok\n"
                    "candidate_next_actions:\n"
                    "  - continue\n"
                    "needs:\n"
                    "  - none\n"
                    "question_for_supervisor:\n"
                    "  - none\n"
                    "</checkpoint>\n"
                ),
                (
                    "<checkpoint>\n"
                    "status: step_done\n"
                    "current_node: implement_feature\n"
                    "summary: done\n"
                    "evidence:\n"
                    "  - verifier: ok\n"
                    "candidate_next_actions:\n"
                    "  - continue\n"
                    "needs:\n"
                    "  - none\n"
                    "question_for_supervisor:\n"
                    "  - none\n"
                    "</checkpoint>\n"
                ),
                (
                    "<checkpoint>\n"
                    "status: step_done\n"
                    "current_node: final_verify\n"
                    "summary: done\n"
                    "evidence:\n"
                    "  - verifier: ok\n"
                    "candidate_next_actions:\n"
                    "  - continue\n"
                    "needs:\n"
                    "  - none\n"
                    "question_for_supervisor:\n"
                    "  - none\n"
                    "</checkpoint>\n"
                ),
            ]

        def read(self, lines: int = 100) -> str:
            self._read_done = True
            return self.outputs.pop(0) if self.outputs else ""

        def inject(self, text: str) -> None:
            assert self._read_done is True
            self.injected.append(text)
            self._read_done = False

    terminal = MockTerminal()
    final = loop.run_sidecar(spec, state, terminal, poll_interval=0, read_lines=50)

    assert final.top_state == TopState.COMPLETED
    session_events = [
        json.loads(line)
        for line in store.session_log_path.read_text().splitlines()
        if json.loads(line)["event_type"] == "injection"
    ]
    assert any(event["payload"].get("trigger_type") == "continue" for event in session_events)
