from supervisor.eval.cases import load_eval_suite
from supervisor.eval.synthetic import expand_eval_suite


def test_expand_eval_suite_generates_provenance_tagged_variants():
    suite = load_eval_suite("approval-core")

    expanded = expand_eval_suite(suite, variants_per_case=2)

    assert expanded.name == "approval-core-synthetic"
    assert len(expanded.cases) == len(suite.cases) * 2
    first = expanded.cases[0]
    assert first.metadata["source"] == "synthetic"
    assert first.metadata["source_case_id"]
    assert first.metadata["transformation"]
    assert first.case_id != first.metadata["source_case_id"]


def test_expand_eval_suite_preserves_expected_contract():
    suite = load_eval_suite("approval-core")

    expanded = expand_eval_suite(suite, variants_per_case=1)

    for case in expanded.cases:
        assert "should_approve" in case.expected
        assert "should_reask_confirmation" in case.expected
        assert "should_attach_run" in case.expected


def test_approval_core_has_broader_contract_coverage():
    suite = load_eval_suite("approval-core")

    assert len(suite.cases) >= 8
    categories = {case.category for case in suite.cases}
    assert categories == {"approval"}
    positives = [case for case in suite.cases if case.expected["should_approve"]]
    negatives = [case for case in suite.cases if not case.expected["should_approve"]]
    assert len(positives) >= 3
    assert len(negatives) >= 3
