from supervisor.plan.loader import load_spec
from supervisor.verifiers.suite import VerifierSuite

def test_verifier_suite_runs():
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    node = spec.get_node("implement_feature")
    suite = VerifierSuite()
    result = suite.run(node.verify, {"current_node_done": False})
    assert result["ok"] is True

def test_artifact_verifier_runs():
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    node = spec.get_node("write_test")
    suite = VerifierSuite()
    result = suite.run(node.verify, {"current_node_done": False})
    assert result["ok"] is True
