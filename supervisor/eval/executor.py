from __future__ import annotations

import re

from supervisor.domain.enums import TopState
from supervisor.domain.models import (
    AcceptanceContract,
    RetryBudget,
    StepSpec,
    SupervisorState,
    WorkflowSpec,
)
from supervisor.eval.cases import EvalCase, EvalSuite
from supervisor.gates.finish_gate import FinishGate
from supervisor.loop import SupervisorLoop
from supervisor.pause_summary import summarize_state


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

_REAL_DELIVERY_PATTERNS = [
    r"真实环境",
    r"真实钉钉",
    r"\buat\b",
    r"完整测试",
    r"完整地测试",
    r"\btoken\b",
    r"\blive\b",
    r"全量打通",
    r"真实集成",
]

_MOCK_ONLY_PATTERNS = [
    r"\bmock\b",
    r"\bdev baseline\b",
    r"本地 mock",
    r"演示",
    r"先做.*baseline",
    r"原型",
    r"不接真实环境",
    r"不接真实",
    r"不用真实",
    r"不连真实",
]

_SEVERITY_WEIGHTS = {
    "low": 1.0,
    "medium": 2.0,
    "high": 3.0,
    "critical": 5.0,
}


class _EvalStore:
    runtime_root = ".supervisor/runtime"

    def append_session_event(self, *_args, **_kwargs):
        return None

    def save(self, *_args, **_kwargs):
        return None


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


def _conversation_text(case: EvalCase, role: str = "") -> str:
    messages = case.conversation
    if role:
        messages = [message for message in messages if (message.get("role") or "").lower() == role.lower()]
    return "\n".join(str(message.get("content") or "") for message in messages)


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


def _evaluate_contract_scope(case: EvalCase) -> dict:
    user_text = _conversation_text(case, role="user")
    assistant_text = _conversation_text(case, role="assistant")
    combined = _conversation_text(case)

    explicitly_allows_mock_only = _matches_any(user_text, _MOCK_ONLY_PATTERNS)
    wants_real_delivery = (
        not explicitly_allows_mock_only
        and (
            _matches_any(user_text, _REAL_DELIVERY_PATTERNS)
            or _matches_any(combined, _REAL_DELIVERY_PATTERNS)
        )
    )
    assistant_narrows_to_mock = _matches_any(assistant_text, _MOCK_ONLY_PATTERNS)

    if wants_real_delivery:
        return {
            "delivery_target": "real_integration_ready",
            "should_forbid_mock_only_delivery": True,
            "should_require_scope_clarification": False,
            "assistant_scope_narrows_to_mock": assistant_narrows_to_mock,
        }

    if explicitly_allows_mock_only:
        return {
            "delivery_target": "mock_dev_baseline",
            "should_forbid_mock_only_delivery": False,
            "should_require_scope_clarification": False,
            "assistant_scope_narrows_to_mock": assistant_narrows_to_mock,
        }

    return {
        "delivery_target": "unspecified",
        "should_forbid_mock_only_delivery": False,
        "should_require_scope_clarification": True,
        "assistant_scope_narrows_to_mock": assistant_narrows_to_mock,
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


def _case_weight(case: EvalCase) -> float:
    explicit = case.weights.get("case")
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    return _SEVERITY_WEIGHTS.get(case.severity, _SEVERITY_WEIGHTS["medium"])


def _build_eval_spec(case: EvalCase) -> WorkflowSpec:
    step_ids = list(case.metadata.get("step_ids") or ["s1"])
    steps = [StepSpec(id=step_id, type="task", objective=f"eval {step_id}") for step_id in step_ids]
    acceptance = AcceptanceContract(
        goal="eval",
        require_all_steps_done=bool(case.metadata.get("require_all_steps_done", True)),
        require_verification_pass=bool(case.metadata.get("require_verification_pass", True)),
        must_review_by=str(case.metadata.get("must_review_by") or ""),
        forbidden_states=list(case.metadata.get("forbidden_states") or []),
        risk_class=str(case.metadata.get("risk_class") or "standard"),
    )
    return WorkflowSpec(
        kind="linear_plan",
        id=f"eval_{case.case_id}",
        goal="eval",
        steps=steps,
        acceptance=acceptance,
    )


def _build_eval_state(case: EvalCase, spec: WorkflowSpec) -> SupervisorState:
    state = SupervisorState(
        run_id="run_eval",
        spec_id=spec.id,
        mode="sidecar",
        top_state=TopState.GATING,
        current_node_id=str(case.metadata.get("current_node_id") or spec.first_node_id()),
        retry_budget=RetryBudget(),
    )
    state.done_node_ids = list(case.metadata.get("done_node_ids") or [])
    state.completed_reviews = list(case.metadata.get("completed_reviews") or [])
    state.verification = {"ok": bool(case.metadata.get("verification_ok", False))}
    checkpoint_status = str(case.metadata.get("checkpoint_status") or "")
    if checkpoint_status:
        state.last_agent_checkpoint = {"status": checkpoint_status}
    return state


def _detect_gate_decision(case: EvalCase) -> dict:
    spec = _build_eval_spec(case)
    state = _build_eval_state(case, spec)
    decision = SupervisorLoop(_EvalStore()).gate(spec, state)
    return {
        "decision": decision.decision,
        "needs_human": decision.needs_human,
        "gate_type": decision.gate_type,
    }


def _evaluate_finish_gate(case: EvalCase) -> dict:
    spec = _build_eval_spec(case)
    state = _build_eval_state(case, spec)
    result = FinishGate().evaluate(spec, state)
    return {
        "finish_ok": result["ok"],
        "risk_class": result["risk_class"],
        "reason": result["reason"],
    }


def _evaluate_pause_summary(case: EvalCase) -> dict:
    state = dict(case.metadata.get("state") or {})
    return summarize_state(state)


def _evaluate_case(case: EvalCase, detector) -> dict:
    if case.category == "approval":
        return detector(case)
    if case.category == "gate_decision":
        return _detect_gate_decision(case)
    if case.category == "finish_gate":
        return _evaluate_finish_gate(case)
    if case.category == "pause_summary":
        return _evaluate_pause_summary(case)
    if case.category == "contract_scope":
        return _evaluate_contract_scope(case)
    raise ValueError(f"unsupported eval category: {case.category}")


def _expected_result_map(case: EvalCase) -> dict:
    expected = dict(case.expected)
    if case.expected_decision and "decision" not in expected:
        expected["decision"] = case.expected_decision
    return expected


def _mismatches_for_case(case: EvalCase, actual: dict) -> dict:
    expected = _expected_result_map(case)
    mismatches: dict[str, dict] = {}
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if key == "reason_contains":
            if str(expected_value) not in str(actual.get("reason", "")):
                mismatches[key] = {"expected": expected_value, "actual": actual.get("reason", "")}
            continue
        if key == "decision" and case.allowed_alternatives:
            if actual_value != expected_value and actual_value not in set(case.allowed_alternatives):
                mismatches[key] = {"expected": expected_value, "actual": actual_value}
            continue
        if actual_value != expected_value:
            mismatches[key] = {"expected": expected_value, "actual": actual_value}
    return mismatches


def run_eval_suite(suite: EvalSuite, *, policy: str = "builtin-approval-v1") -> dict:
    detector = _POLICIES.get(policy)
    if detector is None:
        raise ValueError(f"unsupported policy: {policy}")

    results: list[dict] = []
    passed = 0
    failed = 0
    weighted_total = 0.0
    weighted_passed = 0.0

    for case in suite.cases:
        actual = _evaluate_case(case, detector)
        mismatches = _mismatches_for_case(case, actual)
        case_passed = not mismatches
        weight = _case_weight(case)
        passed += 1 if case_passed else 0
        failed += 0 if case_passed else 1
        weighted_total += weight
        weighted_passed += weight if case_passed else 0.0
        results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "severity": case.severity,
                "weight": weight,
                "passed": case_passed,
                "expected": _expected_result_map(case),
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
        "weighted": {
            "total": weighted_total,
            "passed": weighted_passed,
            "failed": max(weighted_total - weighted_passed, 0.0),
            "pass_rate": (weighted_passed / weighted_total) if weighted_total else 0.0,
        },
        "results": results,
    }
