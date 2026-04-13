from supervisor.eval.cases import EvalCase, list_bundled_suites, load_eval_suite


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
    assert suite.cases[0].severity == "medium"


def test_load_eval_suite_reports_line_context_on_invalid_json(tmp_path):
    suite_path = tmp_path / "broken.jsonl"
    suite_path.write_text(
        '{"case_id":"approval_yes","category":"approval","conversation":[],"expected":{}}\n'
        '{"case_id":"broken",\n',
        encoding="utf-8",
    )

    try:
        load_eval_suite(suite_path)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert "line 2" in message
    assert "broken.jsonl" in message


def test_eval_case_extended_fields_round_trip(tmp_path):
    suite_path = tmp_path / "routing-core.jsonl"
    suite_path.write_text(
        '{"case_id":"route","category":"gate_decision","conversation":[],"expected":{"decision":"VERIFY_STEP"},"severity":"critical","weights":{"case":4},"expected_decision":"VERIFY_STEP","allowed_alternatives":["CONTINUE"],"source_run_id":"run_123","source_checkpoint_seq":7}\n',
        encoding="utf-8",
    )

    suite = load_eval_suite(suite_path)
    case = suite.cases[0]

    assert case.severity == "critical"
    assert case.weights["case"] == 4
    assert case.expected_decision == "VERIFY_STEP"
    assert case.allowed_alternatives == ["CONTINUE"]
    assert case.source_run_id == "run_123"
    assert case.source_checkpoint_seq == 7


def test_list_bundled_suites_includes_new_policy_suites():
    suites = list_bundled_suites()

    assert "approval-core" in suites
    assert "approval-adversarial" in suites
    assert "pause-ux-core" in suites
