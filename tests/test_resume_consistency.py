"""Tests for resume validation and state enrichment."""
import json

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


def test_fresh_init_writes_spec_hash(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml",
        pane_target="%0", workspace_root="/tmp/test",
    )
    assert state.spec_hash != ""
    assert state.pane_target == "%0"
    assert state.workspace_root == "/tmp/test"
    assert state.schema_version == 1


def test_resume_same_spec(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state1 = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml",
        pane_target="%0",
    )
    run_id = state1.run_id

    # Reload — should resume same run
    state2 = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml",
        pane_target="%0",
    )
    assert state2.run_id == run_id


def test_new_run_on_spec_change(tmp_path):
    spec1 = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state1 = store.load_or_init(
        spec1, spec_path="specs/examples/linear_plan.example.yaml",
        pane_target="%0",
    )
    run1 = state1.run_id

    # Load different spec
    spec2 = load_spec("specs/examples/workflow_ui_refactor.example.yaml")
    state2 = store.load_or_init(
        spec2, spec_path="specs/examples/workflow_ui_refactor.example.yaml",
        pane_target="%0",
    )
    # Different spec_id → new run
    assert state2.run_id != run1
    # Old state archived
    archived = list((tmp_path / "runtime").glob("state.run_*.json"))
    assert len(archived) == 1


def test_new_run_on_pane_change(tmp_path):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path / "runtime"))
    state1 = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml",
        pane_target="%0",
    )
    run1 = state1.run_id

    # Same spec, different pane
    state2 = store.load_or_init(
        spec, spec_path="specs/examples/linear_plan.example.yaml",
        pane_target="%5",
    )
    assert state2.run_id != run1
