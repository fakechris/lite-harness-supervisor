"""Tests for conditional_workflow branch execution."""
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.domain.enums import TopState


def test_branch_gate_selects_option(tmp_path):
    """Decision node with options — branch gate picks one."""
    spec = load_spec("specs/examples/workflow_ui_refactor.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    # Move to the decision node
    state.current_node_id = "applicability_gate"
    state.top_state = TopState.GATING
    state.last_agent_checkpoint = {"status": "step_done", "current_node": "applicability_gate"}

    decision = loop.gate(spec, state)
    # Stub judge returns escalate_to_human for branches (low confidence)
    assert decision["decision"] in ("BRANCH", "ESCALATE_TO_HUMAN")


def test_apply_branch_decision(tmp_path):
    """apply_decision with BRANCH sets next node and records history."""
    spec = load_spec("specs/examples/workflow_ui_refactor.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    state.current_node_id = "applicability_gate"
    decision = {
        "decision": "BRANCH",
        "selected_branch": "applicable",
        "next_node_id": "run_clarify",
        "reason": "test",
        "confidence": 0.9,
    }
    loop.apply_decision(spec, state, decision)

    assert state.current_node_id == "run_clarify"
    assert state.top_state == TopState.RUNNING
    assert len(state.branch_history) == 1
    assert state.branch_history[0]["selected_branch"] == "applicable"


def test_loader_parses_branch_options():
    """Spec loader parses BranchOption dataclasses."""
    spec = load_spec("specs/examples/workflow_ui_refactor.example.yaml")
    gate_node = spec.get_node("applicability_gate")
    assert len(gate_node.options) == 2
    assert gate_node.options[0].id == "applicable"
    assert gate_node.options[0].next == "run_clarify"
    assert gate_node.options[1].id == "skip"
    assert gate_node.options[1].next == "finish_skip"
