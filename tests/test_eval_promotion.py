from supervisor.eval.cases import load_eval_suite
from supervisor.eval.promotion import evaluate_candidate_gate
from supervisor.eval.reporting import review_candidate_manifest


def _manifest(candidate_policy: str = "builtin-approval-strict-v1") -> dict:
    return {
        "candidate_id": "candidate_demo",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": candidate_policy,
            "rationale": "Conservative candidate for safety.",
        },
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": candidate_policy,
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [
                {
                    "fragment": "approval-boundary",
                    "path": "skills/thin-supervisor/strategy/approval-boundary.md",
                    "instructions": ["Require explicit execution verbs when prior context is weak."],
                }
            ],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }


def test_evaluate_candidate_gate_holds_when_compare_favors_baseline():
    suite = load_eval_suite("approval-core")
    review = review_candidate_manifest(_manifest())

    gate = evaluate_candidate_gate(review, suite=suite)

    assert gate["decision"] == "hold"
    assert gate["compare"]["summary"]["weighted_wins"]["baseline"] > gate["compare"]["summary"]["weighted_wins"]["candidate"]


def test_evaluate_candidate_gate_needs_canary_when_compare_is_clean():
    suite = load_eval_suite("routing-core")
    review = review_candidate_manifest(_manifest(candidate_policy="builtin-approval-v1"))

    gate = evaluate_candidate_gate(review, suite=suite)

    assert gate["decision"] == "needs_canary"
    assert gate["next_action"].startswith("thin-supervisor eval canary")


def test_evaluate_candidate_gate_rolls_back_on_canary_regression():
    suite = load_eval_suite("routing-core")
    review = review_candidate_manifest(_manifest(candidate_policy="builtin-approval-v1"))
    canary = {
        "decision": "rollback",
        "summary": {
            "run_count": 2,
            "decision_count": 4,
            "mismatch_count": 1,
            "mismatch_rate": 0.25,
            "avg_pass_rate": 0.75,
            "mismatch_kinds": {"safety_regression": 1},
            "friction": {"total_events": 0, "by_kind": {}, "by_signal": {}},
        },
        "runs": [{"run_id": "run_a"}, {"run_id": "run_b"}],
    }

    gate = evaluate_candidate_gate(review, suite=suite, canary_report=canary)

    assert gate["decision"] == "rollback"
    assert gate["canary"]["decision"] == "rollback"
