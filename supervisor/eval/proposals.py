from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from supervisor.eval.cases import EvalSuite
from supervisor.eval.comparator import compare_eval_policies
from supervisor.oracle.client import OracleClient


SUPPORTED_OBJECTIVES = {
    "reduce_repeated_confirmation",
    "reduce_false_approval",
}


def propose_candidate_policy(
    suite: EvalSuite,
    *,
    objective: str,
    baseline_policy: str = "builtin-approval-v1",
    advisor: Callable[[str], str] | None = None,
) -> dict:
    if objective not in SUPPORTED_OBJECTIVES:
        raise ValueError(f"unsupported objective: {objective}")

    candidate_pool = [
        "builtin-approval-v1",
        "builtin-approval-strict-v1",
    ]
    comparisons = [
        compare_eval_policies(
            suite,
            baseline_policy=baseline_policy,
            candidate_policy=candidate,
        )
        for candidate in candidate_pool
        if candidate != baseline_policy or objective == "reduce_repeated_confirmation"
    ]
    if not comparisons:
        raise ValueError(
            f"no candidate comparisons available for objective={objective} baseline_policy={baseline_policy}"
        )

    failure_cases = _failure_cases(comparisons)
    advisory_prompt = _build_advisory_prompt(
        suite_name=suite.name,
        objective=objective,
        baseline_policy=baseline_policy,
        candidate_pool=candidate_pool,
        failure_cases=failure_cases,
    )
    advisory_text, advisory_source = _get_advisory_text(advisory_prompt, advisor)

    if objective == "reduce_false_approval":
        recommended = "builtin-approval-strict-v1"
        rationale = (
            "Prefer the stricter approval detector when optimizing for false-approval reduction. "
            "This keeps the proposal conservative even if it risks more re-asks."
        )
    else:
        best = max(
            comparisons,
            key=lambda item: (
                item["summary"]["wins"]["candidate"],
                -item["summary"]["wins"]["baseline"],
            ),
        )
        recommended = best["candidate_policy"]
        rationale = (
            "Prefer the candidate with the strongest suite win count against the baseline "
            "for repeated-confirmation reduction."
        )
        advised = _extract_policy_from_text(advisory_text, candidate_pool)
        if advised:
            recommended = advised
            rationale = (
                "Advisory analysis over the failure cases recommended this candidate. "
                "The proposal remains constrained to the known candidate pool."
            )

    return {
        "suite": suite.name,
        "objective": objective,
        "baseline_policy": baseline_policy,
        "recommended_candidate_policy": recommended,
        "rationale": rationale,
        "candidate_pool": candidate_pool,
        "comparisons": comparisons,
        "failure_cases": failure_cases,
        "advisory_source": advisory_source,
        "advisory_text": advisory_text,
        "candidate": _candidate_lineage(
            suite_name=suite.name,
            objective=objective,
            baseline_policy=baseline_policy,
            candidate_policy=recommended,
            failure_cases=failure_cases,
            advisory_source=advisory_source,
        ),
    }


def _failure_cases(comparisons: list[dict]) -> list[dict]:
    failures: list[dict] = []
    for comparison in comparisons:
        if comparison["candidate_policy"] == comparison["baseline_policy"]:
            continue
        for item in comparison["comparisons"]:
            blind = item["blind"]
            baseline_case = blind["A"]["result"]
            candidate_case = blind["B"]["result"]
            if baseline_case.get("mismatches") or candidate_case.get("mismatches"):
                failures.append(
                    {
                        "candidate_policy": comparison["candidate_policy"],
                        "case_id": item["case_id"],
                        "winner": item["winner"],
                        "baseline_mismatches": baseline_case.get("mismatches", {}),
                        "candidate_mismatches": candidate_case.get("mismatches", {}),
                    }
                )
    return failures


def _build_advisory_prompt(
    *,
    suite_name: str,
    objective: str,
    baseline_policy: str,
    candidate_pool: list[str],
    failure_cases: list[dict],
) -> str:
    lines = [
        "You are helping choose a constrained candidate policy for offline evaluation.",
        f"Suite: {suite_name}",
        f"Objective: {objective}",
        f"Baseline policy: {baseline_policy}",
        f"Candidate pool: {', '.join(candidate_pool)}",
        "Failure cases:",
    ]
    if not failure_cases:
        lines.append("- none")
    else:
        for item in failure_cases:
            lines.append(
                f"- case={item['case_id']} candidate={item['candidate_policy']} winner={item['winner']} "
                f"baseline_mismatches={list(item['baseline_mismatches'].keys())} "
                f"candidate_mismatches={list(item['candidate_mismatches'].keys())}"
            )
    lines.append("Respond with the best candidate policy id and a short reason.")
    return "\n".join(lines)


def _get_advisory_text(prompt: str, advisor: Callable[[str], str] | None) -> tuple[str, str]:
    if advisor is not None:
        return advisor(prompt), "advisor"
    try:
        opinion = OracleClient().consult(
            question=prompt,
            file_paths=[],
            mode="plan",
            provider="auto",
        )
        return opinion.response_text, opinion.source
    except Exception as exc:
        return f"Fallback advisory unavailable: {exc}", "error"


def _extract_policy_from_text(text: str, candidate_pool: list[str]) -> str:
    for candidate in candidate_pool:
        pattern = re.compile(rf"(?<!\w){re.escape(candidate)}(?!\w)", re.IGNORECASE)
        if pattern.search(text):
            return candidate
    return ""


def _candidate_lineage(
    *,
    suite_name: str,
    objective: str,
    baseline_policy: str,
    candidate_policy: str,
    failure_cases: list[dict],
    advisory_source: str,
) -> dict:
    touched_fragments = ["approval-boundary"]
    mutation_operator, fragment_mutations = _fragment_mutations_for_objective(objective)
    basis = "|".join([suite_name, objective, baseline_policy, candidate_policy, ",".join(touched_fragments)])
    candidate_id = f"candidate_{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:10]}"
    return {
        "candidate_id": candidate_id,
        "candidate_policy": candidate_policy,
        "parent_id": baseline_policy,
        "objective": objective,
        "touched_fragments": touched_fragments,
        "mutation_operator": mutation_operator,
        "fragment_mutations": fragment_mutations,
        "originating_evidence": {
            "suite": suite_name,
            "failure_case_count": len(failure_cases),
            "failure_case_ids": [item.get("case_id", "") for item in failure_cases],
            "advisory_source": advisory_source,
        },
    }


def _fragment_mutations_for_objective(objective: str) -> tuple[str, list[dict]]:
    path = "skills/thin-supervisor/strategy/approval-boundary.md"
    if objective == "reduce_false_approval":
        return (
            "tighten_positive_boundary",
            [
                {
                    "fragment": "approval-boundary",
                    "path": path,
                    "instructions": [
                        "Require explicit execution verbs when prior context is weak or ambiguous.",
                        "Treat terse approvals as final approval only when the immediate prior turn explicitly asked for approval to run.",
                    ],
                }
            ],
        )
    return (
        "accept_repeated_approval",
        [
            {
                "fragment": "approval-boundary",
                "path": path,
                "instructions": [
                    "Accept the second approval utterance after friction without another re-ask.",
                    "Bias terse approvals toward approve when the spec is already in draft approval state.",
                ],
            }
        ],
    )
