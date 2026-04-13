from __future__ import annotations

import re

from supervisor.eval.cases import EvalCase, EvalSuite


_APPROVAL_PATTERNS = [
    r"\bapprove\b",
    r"\bapproved\b",
    r"\bgo ahead\b",
    r"\bstart\b",
    r"\bship it\b",
    r"可以",
    r"同意",
    r"开始吧",
    r"按这个开始",
    r"按这个来",
    r"就这么做",
    r"开始执行",
]

_NOT_READY_PATTERNS = [
    r"\bnot yet\b",
    r"\bshow me\b",
    r"\bwait\b",
    r"\badjust\b",
    r"\brevise\b",
    r"先给我看",
    r"先别",
    r"等等",
    r"再看看",
    r"先改",
    r"修改",
]


def _last_user_message(case: EvalCase) -> str:
    for message in reversed(case.conversation):
        if (message.get("role") or "").lower() == "user":
            return str(message.get("content") or "")
    return ""


def _matches_any(text: str, patterns: list[str]) -> bool:
    lower = text.lower()
    for pattern in patterns:
        haystack = lower if "\\b" in pattern else text
        if re.search(pattern, haystack):
            return True
    return False


def _detect_approval(case: EvalCase) -> dict:
    text = _last_user_message(case).strip()
    approved = _matches_any(text, _APPROVAL_PATTERNS)
    not_ready = _matches_any(text, _NOT_READY_PATTERNS)

    if approved and not not_ready:
        return {
            "should_approve": True,
            "should_reask_confirmation": False,
            "should_attach_run": True,
        }

    return {
        "should_approve": False,
        "should_reask_confirmation": True,
        "should_attach_run": False,
    }


def _detect_approval_strict(case: EvalCase) -> dict:
    text = _last_user_message(case).strip().lower()
    approved = _matches_any(text, [r"\bapprove\b", r"\bapproved\b"])
    not_ready = _matches_any(text, _NOT_READY_PATTERNS)

    if approved and not not_ready:
        return {
            "should_approve": True,
            "should_reask_confirmation": False,
            "should_attach_run": True,
        }

    return {
        "should_approve": False,
        "should_reask_confirmation": True,
        "should_attach_run": False,
    }


_POLICIES = {
    "builtin-approval-v1": _detect_approval,
    "builtin-approval-strict-v1": _detect_approval_strict,
}


def run_eval_suite(suite: EvalSuite, *, policy: str = "builtin-approval-v1") -> dict:
    detector = _POLICIES.get(policy)
    if detector is None:
        raise ValueError(f"unsupported policy: {policy}")

    results: list[dict] = []
    passed = 0
    failed = 0

    for case in suite.cases:
        if case.category != "approval":
            raise ValueError(f"unsupported eval category: {case.category}")
        actual = detector(case)
        mismatches = {
            key: {"expected": expected, "actual": actual.get(key)}
            for key, expected in case.expected.items()
            if actual.get(key) != expected
        }
        case_passed = not mismatches
        passed += 1 if case_passed else 0
        failed += 0 if case_passed else 1
        results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "passed": case_passed,
                "expected": case.expected,
                "actual": actual,
                "mismatches": mismatches,
            }
        )

    total = len(results)
    return {
        "suite": suite.name,
        "policy": policy,
        "counts": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": (passed / total) if total else 0.0,
        },
        "results": results,
    }
