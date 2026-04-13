from supervisor.eval.rollouts import current_rollouts, list_rollouts, record_rollout


def _canary_report() -> dict:
    return {
        "run_ids": ["run_a", "run_b"],
        "decision": "promote",
        "summary": {
            "run_count": 2,
            "decision_count": 8,
            "mismatch_count": 0,
            "mismatch_rate": 0.0,
            "avg_pass_rate": 1.0,
            "mismatch_kinds": {},
            "friction": {
                "total_events": 0,
                "by_kind": {},
                "by_signal": {},
            },
        },
    }


def test_record_rollout_writes_candidate_bound_record(tmp_path):
    record = record_rollout(
        candidate_id="candidate_demo",
        phase="shadow",
        canary_report=_canary_report(),
        runtime_dir=str(tmp_path / ".supervisor" / "runtime"),
    )

    assert record["candidate_id"] == "candidate_demo"
    assert record["phase"] == "shadow"
    assert record["decision"] == "promote"
    history = list_rollouts(runtime_dir=str(tmp_path / ".supervisor" / "runtime"))
    assert len(history) == 1
    assert history[0]["run_ids"] == ["run_a", "run_b"]


def test_list_rollouts_can_filter_by_candidate(tmp_path):
    runtime_dir = str(tmp_path / ".supervisor" / "runtime")
    record_rollout(candidate_id="candidate_a", phase="shadow", canary_report=_canary_report(), runtime_dir=runtime_dir)
    record_rollout(candidate_id="candidate_b", phase="limited", canary_report=_canary_report(), runtime_dir=runtime_dir)

    filtered = list_rollouts(runtime_dir=runtime_dir, candidate_id="candidate_b")

    assert len(filtered) == 1
    assert filtered[0]["candidate_id"] == "candidate_b"
    assert filtered[0]["phase"] == "limited"


def test_current_rollouts_returns_latest_per_candidate():
    history = [
        {"candidate_id": "candidate_a", "phase": "shadow", "saved_at": "2026-04-12T00:00:00+00:00"},
        {"candidate_id": "candidate_a", "phase": "limited", "saved_at": "2026-04-13T00:00:00+00:00"},
        {"candidate_id": "candidate_b", "phase": "shadow", "saved_at": "2026-04-13T01:00:00+00:00"},
    ]

    current = current_rollouts(history)

    assert current["candidate_a"]["phase"] == "limited"
    assert current["candidate_b"]["phase"] == "shadow"


def test_current_rollouts_ignores_none_candidate_and_saved_at_values():
    history = [
        {"candidate_id": None, "phase": "shadow", "saved_at": "2026-04-12T00:00:00+00:00"},
        {"candidate_id": "candidate_a", "phase": "shadow", "saved_at": None},
        {"candidate_id": "candidate_a", "phase": "limited", "saved_at": "2026-04-13T00:00:00+00:00"},
    ]

    current = current_rollouts(history)

    assert list(current) == ["candidate_a"]
    assert current["candidate_a"]["phase"] == "limited"


def test_list_rollouts_skips_malformed_jsonl_lines(tmp_path):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    record_rollout(candidate_id="candidate_a", phase="shadow", canary_report=_canary_report(), runtime_dir=str(runtime_dir))
    path = runtime_dir.parent / "evals" / "rollouts.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")
    record_rollout(candidate_id="candidate_b", phase="limited", canary_report=_canary_report(), runtime_dir=str(runtime_dir))

    history = list_rollouts(runtime_dir=str(runtime_dir))

    assert len(history) == 2
    assert [item["candidate_id"] for item in history] == ["candidate_a", "candidate_b"]
