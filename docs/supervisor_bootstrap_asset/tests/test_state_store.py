from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore

def test_load_or_init_and_save(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(spec)
    assert state.spec_id == spec.id
    assert state.current_node_id == "write_test"
    store.save(state)
    assert (tmp_path / "runtime" / "state.json").exists()
