import pytest

from supervisor.plan.loader import load_spec, SpecValidationError

def test_load_linear_plan():
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    assert spec.kind == "linear_plan"
    assert spec.first_node_id() == "write_test"
    assert spec.next_node_id("write_test") == "implement_feature"

def test_load_conditional_workflow():
    spec = load_spec("specs/examples/workflow_ui_refactor.example.yaml")
    assert spec.kind == "conditional_workflow"
    assert spec.first_node_id() == "inspect_screen"


def test_load_spec_with_draft_approval(tmp_path):
    spec_path = tmp_path / "draft.yaml"
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: draft_plan\n"
        "goal: test clarify-first flow\n"
        "approval:\n"
        "  required: true\n"
        "  status: draft\n"
        "steps:\n"
        "  - id: s1\n"
        "    type: task\n"
        "    objective: do something\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
    )

    spec = load_spec(str(spec_path))

    assert spec.approval.required is True
    assert spec.approval.status == "draft"


def test_load_spec_defaults_required_draft_status_when_omitted(tmp_path):
    spec_path = tmp_path / "draft-default.yaml"
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: draft_default\n"
        "goal: test draft default\n"
        "approval:\n"
        "  required: true\n"
        "steps:\n"
        "  - id: s1\n"
        "    type: task\n"
        "    objective: do something\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
    )

    spec = load_spec(str(spec_path))

    assert spec.approval.required is True
    assert spec.approval.status == "draft"


def test_load_spec_rejects_non_boolean_approval_required(tmp_path):
    spec_path = tmp_path / "bad-required.yaml"
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: bad_required\n"
        "goal: test invalid approval required\n"
        "approval:\n"
        "  required: \"true\"\n"
        "steps:\n"
        "  - id: s1\n"
        "    type: task\n"
        "    objective: do something\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
    )

    with pytest.raises(SpecValidationError, match="approval.required must be a boolean"):
        load_spec(str(spec_path))
