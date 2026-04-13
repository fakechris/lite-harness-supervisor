from supervisor.eval.cases import EvalCase, EvalSuite
from supervisor.eval.executor import run_eval_suite


def test_run_eval_suite_passes_explicit_approval_case():
    suite = EvalSuite(
        name="approval-core",
        cases=[
            EvalCase(
                case_id="approval_yes",
                category="approval",
                conversation=[
                    {"role": "assistant", "content": "这里是 draft spec"},
                    {"role": "user", "content": "可以，就按这个开始"},
                ],
                expected={
                    "should_approve": True,
                    "should_reask_confirmation": False,
                    "should_attach_run": True,
                },
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["suite"] == "approval-core"
    assert report["counts"]["total"] == 1
    assert report["counts"]["passed"] == 1
    assert report["counts"]["failed"] == 0
    assert report["results"][0]["passed"] is True


def test_run_eval_suite_flags_ambiguous_approval_case():
    suite = EvalSuite(
        name="approval-core",
        cases=[
            EvalCase(
                case_id="approval_ambiguous",
                category="approval",
                conversation=[
                    {"role": "assistant", "content": "这里是 draft spec"},
                    {"role": "user", "content": "先给我看最终 spec"},
                ],
                expected={
                    "should_approve": False,
                    "should_reask_confirmation": True,
                    "should_attach_run": False,
                },
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["counts"]["passed"] == 1
    assert report["results"][0]["actual"]["should_approve"] is False
    assert report["results"][0]["actual"]["should_reask_confirmation"] is True


def test_run_eval_suite_supports_gate_decision_cases():
    suite = EvalSuite(
        name="routing-core",
        cases=[
            EvalCase(
                case_id="blocked",
                category="gate_decision",
                conversation=[],
                expected={"decision": "ESCALATE_TO_HUMAN", "needs_human": True},
                severity="critical",
                metadata={"checkpoint_status": "blocked", "current_node_id": "s1", "step_ids": ["s1"]},
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["counts"]["passed"] == 1
    assert report["results"][0]["actual"]["decision"] == "ESCALATE_TO_HUMAN"
    assert report["results"][0]["weight"] == 5.0


def test_run_eval_suite_supports_finish_gate_cases():
    suite = EvalSuite(
        name="finish-gate-core",
        cases=[
            EvalCase(
                case_id="review_required",
                category="finish_gate",
                conversation=[],
                expected={
                    "finish_ok": False,
                    "risk_class": "standard",
                    "reason_contains": "requires review by: human",
                },
                severity="high",
                metadata={
                    "step_ids": ["s1"],
                    "current_node_id": "s1",
                    "done_node_ids": ["s1"],
                    "verification_ok": True,
                    "must_review_by": "human",
                },
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["counts"]["passed"] == 1
    assert report["results"][0]["actual"]["finish_ok"] is False
    assert report["weighted"]["total"] == 3.0


def test_run_eval_suite_supports_pause_summary_cases():
    suite = EvalSuite(
        name="pause-ux-core",
        cases=[
            EvalCase(
                case_id="pause_resume_hint",
                category="pause_summary",
                conversation=[],
                expected={
                    "pause_reason": "node mismatch persisted for 5 checkpoints",
                    "next_action": "thin-supervisor run resume --spec /tmp/spec.yaml --pane %9 --surface tmux",
                    "is_waiting_for_review": False,
                },
                metadata={
                    "state": {
                        "run_id": "run_123",
                        "top_state": "PAUSED_FOR_HUMAN",
                        "spec_path": "/tmp/spec.yaml",
                        "pane_target": "%9",
                        "surface_type": "tmux",
                        "human_escalations": [{"reason": "node mismatch persisted for 5 checkpoints"}],
                    }
                },
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["counts"]["passed"] == 1
    assert report["results"][0]["actual"]["pause_reason"] == "node mismatch persisted for 5 checkpoints"


def test_run_eval_suite_supports_contract_scope_cases():
    suite = EvalSuite(
        name="clarify-contract-core",
        cases=[
            EvalCase(
                case_id="real_uat_not_mock",
                category="contract_scope",
                conversation=[
                    {"role": "assistant", "content": "我会先做一个本地 mock/dev baseline。"},
                    {"role": "user", "content": "目标是配上钉钉 token 就能完整测试，必须是真实环境全量打通。"},
                ],
                expected={
                    "delivery_target": "real_integration_ready",
                    "should_forbid_mock_only_delivery": True,
                    "should_require_scope_clarification": False,
                },
                severity="critical",
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["counts"]["passed"] == 1
    assert report["results"][0]["actual"]["delivery_target"] == "real_integration_ready"
    assert report["results"][0]["actual"]["should_forbid_mock_only_delivery"] is True


def test_run_eval_suite_requires_clarification_when_scope_is_ambiguous():
    suite = EvalSuite(
        name="clarify-contract-core",
        cases=[
            EvalCase(
                case_id="ambiguous_goal_needs_clarify",
                category="contract_scope",
                conversation=[
                    {"role": "user", "content": "把整个 PRD 开发完。"},
                ],
                expected={
                    "delivery_target": "unspecified",
                    "should_forbid_mock_only_delivery": False,
                    "should_require_scope_clarification": True,
                },
                severity="high",
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["counts"]["passed"] == 1
    assert report["results"][0]["actual"]["should_require_scope_clarification"] is True


def test_run_eval_suite_allows_explicit_mock_only_delivery():
    suite = EvalSuite(
        name="clarify-contract-core",
        cases=[
            EvalCase(
                case_id="explicit_mock_only",
                category="contract_scope",
                conversation=[
                    {"role": "assistant", "content": "我会先起一个 baseline。"},
                    {"role": "user", "content": "这次先做本地 mock 演示就行，不接真实环境。"},
                ],
                expected={
                    "delivery_target": "mock_dev_baseline",
                    "should_forbid_mock_only_delivery": False,
                    "should_require_scope_clarification": False,
                },
                severity="medium",
            )
        ],
    )

    report = run_eval_suite(suite)

    assert report["counts"]["passed"] == 1
    assert report["results"][0]["actual"]["delivery_target"] == "mock_dev_baseline"
