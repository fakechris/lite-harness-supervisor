from supervisor.plan.loader import load_spec

def test_load_linear_plan():
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    assert spec.kind == "linear_plan"
    assert spec.first_node_id() == "write_test"
    assert spec.next_node_id("write_test") == "implement_feature"

def test_load_conditional_workflow():
    spec = load_spec("specs/examples/workflow_ui_refactor.example.yaml")
    assert spec.kind == "conditional_workflow"
    assert spec.first_node_id() == "inspect_screen"
