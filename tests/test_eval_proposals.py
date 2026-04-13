from pathlib import Path

from supervisor.eval.cases import load_eval_suite
from supervisor.eval.proposals import _extract_policy_from_text, propose_candidate_policy
from supervisor.eval.reporting import save_candidate_manifest


def test_propose_candidate_policy_for_reduce_repeated_confirmation():
    suite = load_eval_suite("approval-core")

    proposal = propose_candidate_policy(
        suite,
        objective="reduce_repeated_confirmation",
        baseline_policy="builtin-approval-v1",
    )

    assert proposal["objective"] == "reduce_repeated_confirmation"
    assert proposal["recommended_candidate_policy"] in {
        "builtin-approval-v1",
        "builtin-approval-strict-v1",
    }
    assert proposal["baseline_policy"] == "builtin-approval-v1"
    assert proposal["suite"] == "approval-core"
    assert proposal["rationale"]
    assert "advisory_text" in proposal
    assert proposal["candidate"]["candidate_id"].startswith("candidate_")
    assert proposal["candidate"]["parent_id"] == "builtin-approval-v1"
    assert proposal["candidate"]["touched_fragments"] == ["approval-boundary"]
    assert proposal["candidate"]["mutation_operator"] == "accept_repeated_approval"
    assert proposal["candidate"]["fragment_mutations"][0]["fragment"] == "approval-boundary"
    assert proposal["candidate"]["originating_evidence"]["suite"] == "approval-core"


def test_propose_candidate_policy_prefers_safety_for_false_approval_objective():
    suite = load_eval_suite("approval-core")

    proposal = propose_candidate_policy(
        suite,
        objective="reduce_false_approval",
        baseline_policy="builtin-approval-v1",
    )

    assert proposal["recommended_candidate_policy"] == "builtin-approval-strict-v1"
    assert proposal["objective"] == "reduce_false_approval"
    assert proposal["candidate"]["candidate_policy"] == "builtin-approval-strict-v1"
    assert proposal["candidate"]["mutation_operator"] == "tighten_positive_boundary"
    assert proposal["candidate"]["fragment_mutations"][0]["instructions"]


def test_propose_candidate_policy_can_follow_model_advice():
    suite = load_eval_suite("approval-core")

    def advisor(_prompt: str) -> str:
        return (
            "Use builtin-approval-strict-v1. "
            "The failure cases show the baseline is too permissive for this objective."
        )

    proposal = propose_candidate_policy(
        suite,
        objective="reduce_repeated_confirmation",
        baseline_policy="builtin-approval-v1",
        advisor=advisor,
    )

    assert proposal["recommended_candidate_policy"] == "builtin-approval-strict-v1"
    assert proposal["advisory_source"] == "advisor"
    assert "failure cases" in proposal["advisory_text"]


def test_extract_policy_from_text_uses_exact_boundaries():
    candidate = _extract_policy_from_text(
        "Do not use builtin-approval-strict-v1ish here.",
        ["builtin-approval-v1", "builtin-approval-strict-v1"],
    )

    assert candidate == ""


def test_save_candidate_manifest_persists_lineage(tmp_path):
    proposal = {
        "objective": "reduce_false_approval",
        "suite": "approval-core",
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [
                {
                    "fragment": "approval-boundary",
                    "path": "skills/thin-supervisor/strategy/approval-boundary.md",
                    "instructions": ["Require explicit execution verbs when prior context is weak."],
                }
            ],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }

    path = save_candidate_manifest(proposal, runtime_dir=str(tmp_path / ".supervisor" / "runtime"))

    assert path == tmp_path / ".supervisor" / "evals" / "candidates" / "candidate_demo.json"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "builtin-approval-strict-v1" in text
    assert "tighten_positive_boundary" in text
