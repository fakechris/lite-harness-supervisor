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

