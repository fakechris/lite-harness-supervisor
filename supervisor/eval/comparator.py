from __future__ import annotations

from supervisor.eval.cases import EvalSuite
from supervisor.eval.executor import run_eval_suite


def _winner_for_case(left: dict, right: dict) -> str:
    left_mismatches = len(left.get("mismatches", {}))
    right_mismatches = len(right.get("mismatches", {}))
    if left_mismatches < right_mismatches:
        return "baseline"
    if right_mismatches < left_mismatches:
        return "candidate"
    return "tie"


def compare_eval_policies(
    suite: EvalSuite,
    *,
    baseline_policy: str,
    candidate_policy: str,
) -> dict:
    baseline = run_eval_suite(suite, policy=baseline_policy)
    candidate = run_eval_suite(suite, policy=candidate_policy)

    comparisons: list[dict] = []
    wins = {"baseline": 0, "candidate": 0, "tie": 0}

    for index, baseline_case in enumerate(baseline["results"]):
        candidate_case = candidate["results"][index]
        winner = _winner_for_case(baseline_case, candidate_case)
        wins[winner] += 1
        blind = {
            "A": {"policy": "baseline", "result": baseline_case},
            "B": {"policy": "candidate", "result": candidate_case},
        }
        comparisons.append(
            {
                "case_id": baseline_case["case_id"],
                "winner": winner,
                "blind": blind,
            }
        )

    total_cases = len(comparisons)
    return {
        "suite": suite.name,
        "baseline_policy": baseline_policy,
        "candidate_policy": candidate_policy,
        "summary": {
            "total_cases": total_cases,
            "wins": wins,
        },
        "comparisons": comparisons,
    }
