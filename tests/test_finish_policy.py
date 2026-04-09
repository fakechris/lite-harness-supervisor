"""Tests for finish_policy enforcement."""
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.domain.enums import TopState


def test_finish_all_steps_done(tmp_path):
    """finish_policy.require_all_steps_done blocks if nodes missing."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    # Only mark 2 of 3 steps done
    state.done_node_ids = ["write_test", "implement_feature"]
    state.current_node_id = "final_verify"

    result = loop.finish_gate.evaluate(spec, state)
    # final_verify not in done_node_ids
    assert result["ok"] is False
    assert "final_verify" in result["reason"]


def test_finish_all_done_passes(tmp_path):
    """All steps done → finish gate passes."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    state.done_node_ids = ["write_test", "implement_feature", "final_verify"]
    state.verification = {"ok": True}

    result = loop.finish_gate.evaluate(spec, state)
    assert result["ok"] is True


def test_verification_applies_finish_gate(tmp_path):
    """apply_verification uses finish_gate when no next node."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    # Set up: at final node, all previous done
    state.current_node_id = "final_verify"
    state.done_node_ids = ["write_test", "implement_feature"]
    state.verification = {"ok": True}

    verification = {"ok": True, "results": []}
    loop.apply_verification(spec, state, verification)
    # final_verify gets added to done, finish_gate checks all 3 → COMPLETED
    assert state.top_state == TopState.COMPLETED
