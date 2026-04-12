from supervisor.eval.cases import load_eval_suite
from supervisor.eval.proposals import propose_candidate_policy


def test_propose_candidate_policy_for_reduce_repeated_confirmation():
    suite = load_eval_suite("approval-core")

    proposal = propose_candidate_policy(
        suite,
        objective="reduce_repeated_confirmation",
        baseline_policy="builtin-approval-v1",
    )

    assert proposal["objective"] == "reduce_repeated_confirmation"
    assert proposal["recommended_candidate_policy"] in {
        "builtin-approval-v1",
        "builtin-approval-strict-v1",
    }
    assert proposal["baseline_policy"] == "builtin-approval-v1"
    assert proposal["suite"] == "approval-core"
    assert proposal["rationale"]
    assert "advisory_text" in proposal


def test_propose_candidate_policy_prefers_safety_for_false_approval_objective():
    suite = load_eval_suite("approval-core")

    proposal = propose_candidate_policy(
        suite,
        objective="reduce_false_approval",
        baseline_policy="builtin-approval-v1",
    )

    assert proposal["recommended_candidate_policy"] == "builtin-approval-strict-v1"
    assert proposal["objective"] == "reduce_false_approval"


def test_propose_candidate_policy_can_follow_model_advice():
    suite = load_eval_suite("approval-core")

    def advisor(_prompt: str) -> str:
        return (
            "Use builtin-approval-strict-v1. "
            "The failure cases show the baseline is too permissive for this objective."
        )

    proposal = propose_candidate_policy(
        suite,
        objective="reduce_repeated_confirmation",
        baseline_policy="builtin-approval-v1",
        advisor=advisor,
    )

    assert proposal["recommended_candidate_policy"] == "builtin-approval-strict-v1"
    assert proposal["advisory_source"] == "advisor"
    assert "failure cases" in proposal["advisory_text"]
