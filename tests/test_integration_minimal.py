from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop

def test_integration_gate_and_verify(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    event = {
        "type": "agent_output",
        "payload": {
            "checkpoint": {
                "status": "step_done",
                "current_node": "write_test",
                "summary": "wrote the test",
                "evidence": ["modified: tests/test_example.py"],
                "candidate_next_actions": ["move to next step"],
                "needs": ["none"],
                "question_for_supervisor": ["none"],
            }
        }
    }

    loop.handle_event(state, event)
    decision = loop.gate(spec, state)
    assert decision.decision == "VERIFY_STEP"
    loop.apply_decision(spec, state, decision)
    verification = loop.verify_current_node(spec, state)
    loop.apply_verification(spec, state, verification)
    assert state.current_node_id == "implement_feature"
