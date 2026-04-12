from supervisor.eval.cases import load_eval_suite
from supervisor.eval.comparator import compare_eval_policies


def test_compare_eval_policies_blinds_outputs_and_finds_baseline_win():
    suite = load_eval_suite("approval-core")

    report = compare_eval_policies(
        suite,
        baseline_policy="builtin-approval-v1",
        candidate_policy="builtin-approval-strict-v1",
    )

    assert report["suite"] == "approval-core"
    assert report["summary"]["total_cases"] >= 1
    assert report["summary"]["wins"]["baseline"] >= 1
    assert report["summary"]["wins"]["candidate"] == 0
    first = report["comparisons"][0]
    assert first["blind"]["A"]["policy"] in {"baseline", "candidate"}
    assert first["blind"]["B"]["policy"] in {"baseline", "candidate"}
    assert first["winner"] in {"baseline", "candidate", "tie"}


def test_compare_eval_policies_reports_ties_for_identical_policies():
    suite = load_eval_suite("approval-core")

    report = compare_eval_policies(
        suite,
        baseline_policy="builtin-approval-v1",
        candidate_policy="builtin-approval-v1",
    )

    assert report["summary"]["wins"]["baseline"] == 0
    assert report["summary"]["wins"]["candidate"] == 0
    assert report["summary"]["wins"]["tie"] == report["summary"]["total_cases"]
