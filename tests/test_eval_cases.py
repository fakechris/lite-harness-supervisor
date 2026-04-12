from supervisor.eval.cases import EvalCase, load_eval_suite


def test_load_eval_suite_from_jsonl(tmp_path):
    suite_path = tmp_path / "approval-core.jsonl"
    suite_path.write_text(
        '{"case_id":"approval_yes","category":"approval","conversation":[{"role":"user","content":"可以，就按这个开始"}],"expected":{"should_approve":true,"should_reask_confirmation":false,"should_attach_run":true}}\n'
        '{"case_id":"approval_ambiguous","category":"approval","conversation":[{"role":"user","content":"先给我看最终 spec"}],"expected":{"should_approve":false,"should_reask_confirmation":true,"should_attach_run":false}}\n',
        encoding="utf-8",
    )

    suite = load_eval_suite(suite_path)

    assert suite.name == "approval-core"
    assert len(suite.cases) == 2
    assert isinstance(suite.cases[0], EvalCase)
    assert suite.cases[0].case_id == "approval_yes"

