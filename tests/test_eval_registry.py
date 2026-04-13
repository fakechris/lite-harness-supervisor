from supervisor.eval.registry import current_promotions, list_promotions, promote_candidate


def _gate(decision: str = "needs_canary") -> dict:
    return {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": "needs_human_review",
        "decision": decision,
        "compare": {"summary": {"weighted_wins": {"baseline": 0.0, "candidate": 0.0, "tie": 8.0}}},
        "canary": None,
        "next_action": "thin-supervisor-dev eval review-candidate --candidate-id candidate_demo",
    }


def test_promote_candidate_writes_registry_record(tmp_path):
    record = promote_candidate(
        _gate(),
        runtime_dir=str(tmp_path / ".supervisor" / "runtime"),
        approved_by="human",
    )

    assert record["candidate_id"] == "candidate_demo"
    assert record["status"] == "promoted"
    history = list_promotions(runtime_dir=str(tmp_path / ".supervisor" / "runtime"))
    assert len(history) == 1
    assert history[0]["approved_by"] == "human"


def test_promote_candidate_rejects_hold_without_force(tmp_path):
    gate = _gate(decision="hold")

    try:
        promote_candidate(
            gate,
            runtime_dir=str(tmp_path / ".supervisor" / "runtime"),
            approved_by="human",
        )
    except ValueError as exc:
        assert "cannot promote candidate with gate decision=hold" in str(exc)
    else:
        raise AssertionError("expected hold promotion to be rejected")


def test_current_promotions_returns_latest_per_suite():
    history = [
        {"suite": "approval-core", "candidate_id": "candidate_old", "status": "promoted", "promoted_at": "2026-04-12T00:00:00+00:00"},
        {"suite": "approval-core", "candidate_id": "candidate_new", "status": "promoted", "promoted_at": "2026-04-13T00:00:00+00:00"},
        {"suite": "routing-core", "candidate_id": "candidate_route", "status": "promoted", "promoted_at": "2026-04-13T01:00:00+00:00"},
    ]

    current = current_promotions(history)

    assert current["approval-core"]["candidate_id"] == "candidate_new"
    assert current["routing-core"]["candidate_id"] == "candidate_route"
