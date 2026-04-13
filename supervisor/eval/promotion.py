from __future__ import annotations

from supervisor.eval.canary import run_canary_eval
from supervisor.eval.cases import EvalSuite
from supervisor.eval.comparator import compare_eval_policies


def evaluate_candidate_gate(
    review: dict,
    *,
    suite: EvalSuite,
    canary_report: dict | None = None,
) -> dict:
    baseline_policy = review.get("parent_id", "")
    candidate_policy = review.get("candidate_policy", "")
    compare = compare_eval_policies(
        suite,
        baseline_policy=baseline_policy,
        candidate_policy=candidate_policy,
    )
    decision = _decision_from_compare(compare)
    next_action = (
        f"thin-supervisor-dev eval canary --run-id <recent_run> --run-id <recent_run>"
        if decision == "needs_canary"
        else f"thin-supervisor-dev eval review-candidate --candidate-id {review.get('candidate_id', '')}"
    )

    if canary_report is not None:
        decision = canary_report.get("decision", decision)
        next_action = (
            f"thin-supervisor-dev eval review-candidate --candidate-id {review.get('candidate_id', '')}"
            if decision == "promote"
            else next_action
        )

    return {
        "candidate_id": review.get("candidate_id", ""),
        "candidate_policy": candidate_policy,
        "baseline_policy": baseline_policy,
        "suite": review.get("suite", suite.name),
        "review_status": review.get("review_status", "needs_human_review"),
        "decision": decision,
        "compare": compare,
        "canary": canary_report,
        "next_action": next_action,
    }


def run_candidate_gate(
    review: dict,
    *,
    suite: EvalSuite,
    run_ids: list[str],
    runtime_dir: str = ".supervisor/runtime",
    max_mismatch_rate: float = 0.25,
    max_friction_events: int = 0,
) -> dict:
    canary_report = None
    if run_ids:
        canary_report = run_canary_eval(
            run_ids,
            runtime_dir=runtime_dir,
            max_mismatch_rate=max_mismatch_rate,
            max_friction_events=max_friction_events,
        )
    return evaluate_candidate_gate(review, suite=suite, canary_report=canary_report)


def _decision_from_compare(compare: dict) -> str:
    weighted = compare.get("summary", {}).get("weighted_wins", {})
    baseline = float(weighted.get("baseline", 0.0) or 0.0)
    candidate = float(weighted.get("candidate", 0.0) or 0.0)
    if baseline > candidate:
        return "hold"
    return "needs_canary"
