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


def test_read_last_seq_uses_tail_records(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    session_log = runtime / "session_log.jsonl"
    with session_log.open("w", encoding="utf-8") as f:
        for i in range(1, 200):
            f.write(f'{{"seq": {i}, "event_type": "checkpoint"}}\n')
        f.write("not-json\n")
        f.write('{"seq": 200, "event_type": "checkpoint"}\n')

    store = StateStore(str(runtime))
    assert store._read_last_seq() == 200
