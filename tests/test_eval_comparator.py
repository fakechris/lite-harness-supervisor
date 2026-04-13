from supervisor.eval.cases import load_eval_suite
import supervisor.eval.comparator as comparator
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
    assert report["summary"]["weighted_wins"]["baseline"] >= 1.0
    first = report["comparisons"][0]
    assert first["blind"]["A"]["policy"] != first["blind"]["B"]["policy"]
    assert {
        first["blind"]["A"]["policy"],
        first["blind"]["B"]["policy"],
    } == {"baseline", "candidate"}
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


def test_compare_eval_policies_matches_cases_by_case_id(monkeypatch):
    suite = load_eval_suite("approval-core")

    def fake_run_eval_suite(_suite, *, policy):
        if policy == "baseline":
            return {
                "results": [
                    {"case_id": "a", "mismatches": {}},
                    {"case_id": "b", "mismatches": {"x": True}},
                ]
            }
        return {
            "results": [
                {"case_id": "b", "mismatches": {"x": True}},
                {"case_id": "a", "mismatches": {}},
            ]
        }

    monkeypatch.setattr(comparator, "run_eval_suite", fake_run_eval_suite)

    report = comparator.compare_eval_policies(
        suite,
        baseline_policy="baseline",
        candidate_policy="candidate",
    )

    assert report["summary"]["wins"]["tie"] == 2
    assert report["summary"]["weighted_wins"]["tie"] == 0.0
