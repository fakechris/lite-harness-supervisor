from __future__ import annotations

import json
from pathlib import Path

from supervisor.eval.dossier import build_candidate_dossier
from supervisor.eval.reporting import save_eval_report
from supervisor.eval.registry import promote_candidate


def _manifest_payload() -> dict:
    return {
        "candidate_id": "candidate_demo",
        "saved_at": "2026-04-12T00:00:00+00:00",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": "builtin-approval-strict-v1",
            "rationale": "Conservative candidate for safety.",
        },
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


def _write_manifest(base: Path) -> Path:
    path = base / ".supervisor" / "evals" / "candidates" / "candidate_demo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")
    return path


def test_build_candidate_dossier_collects_related_evidence(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    manifest_path = _write_manifest(tmp_path)

    proposal = {
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "baseline_policy": "builtin-approval-v1",
        "recommended_candidate_policy": "builtin-approval-strict-v1",
        "candidate": {"candidate_id": "candidate_demo"},
    }
    save_eval_report(proposal, report_kind="proposal", runtime_dir=str(runtime_dir))

    gate = {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": "needs_human_review",
        "decision": "needs_canary",
        "compare": {"summary": {"weighted_wins": {"baseline": 0.0, "candidate": 3.0, "tie": 5.0}}},
        "canary": None,
        "next_action": "thin-supervisor eval canary --run-id <recent_run>",
    }
    save_eval_report(gate, report_kind="gate", runtime_dir=str(runtime_dir))

    promote_candidate(
        {
            **gate,
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "manifest_path": str(manifest_path),
        },
        runtime_dir=str(runtime_dir),
        approved_by="human",
        force=True,
    )

    dossier = build_candidate_dossier(candidate_id="candidate_demo", runtime_dir=str(runtime_dir))

    assert dossier["candidate"]["candidate_id"] == "candidate_demo"
    assert dossier["review"]["review_status"] == "needs_human_review"
    assert dossier["evidence"]["report_counts"]["proposal"] == 1
    assert dossier["evidence"]["report_counts"]["gate"] == 1
    assert dossier["promotion"]["is_current"] is True
    assert dossier["promotion"]["current_record"]["approved_by"] == "human"


def test_build_candidate_dossier_uses_gate_next_action_when_not_promoted(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    _write_manifest(tmp_path)
    save_eval_report(
        {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "baseline_policy": "builtin-approval-v1",
            "suite": "approval-core",
            "review_status": "needs_human_review",
            "decision": "needs_canary",
            "compare": {"summary": {"weighted_wins": {"baseline": 0.0, "candidate": 3.0, "tie": 5.0}}},
            "canary": None,
            "next_action": "thin-supervisor eval canary --run-id <recent_run>",
        },
        report_kind="gate",
        runtime_dir=str(runtime_dir),
    )

    dossier = build_candidate_dossier(candidate_id="candidate_demo", runtime_dir=str(runtime_dir))

    assert dossier["promotion"]["is_current"] is False
    assert dossier["next_action"] == "thin-supervisor eval canary --run-id <recent_run>"
