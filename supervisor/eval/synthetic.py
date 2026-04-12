from __future__ import annotations

from supervisor.eval.cases import EvalCase, EvalSuite


_TRANSFORMATIONS = [
    ("zh-shorter", "同意，直接开始"),
    ("zh-explicit", "可以，按这个来，开始执行"),
    ("en-shorter", "Approved, go ahead."),
]

_NEGATIVE_TRANSFORMATIONS = [
    ("zh-review-first", "先给我看最终 spec，再决定"),
    ("zh-adjust-first", "先改第二步，我再确认"),
    ("en-wait-revise", "Wait, revise the plan first."),
]


def expand_eval_suite(suite: EvalSuite, *, variants_per_case: int = 2) -> EvalSuite:
    cases: list[EvalCase] = []

    for case in suite.cases:
        should_approve = bool(case.expected.get("should_approve"))
        transformations = _TRANSFORMATIONS if should_approve else _NEGATIVE_TRANSFORMATIONS
        for index in range(max(0, variants_per_case)):
            transform_name, replacement = transformations[index % len(transformations)]
            conversation = list(case.conversation)
            for message in range(len(conversation) - 1, -1, -1):
                if (conversation[message].get("role") or "").lower() == "user":
                    updated = dict(conversation[message])
                    updated["content"] = replacement
                    conversation[message] = updated
                    break
            cases.append(
                EvalCase(
                    case_id=f"{case.case_id}__{transform_name}_{index + 1}",
                    category=case.category,
                    conversation=conversation,
                    expected=dict(case.expected),
                    user_profile=dict(case.user_profile),
                    anti_goals=list(case.anti_goals),
                    metadata={
                        **dict(case.metadata),
                        "source": "synthetic",
                        "source_case_id": case.case_id,
                        "transformation": transform_name,
                    },
                )
            )

    return EvalSuite(
        name=f"{suite.name}-synthetic",
        cases=cases,
        source_path=suite.source_path,
    )
