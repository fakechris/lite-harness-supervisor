from __future__ import annotations


def test_run_canary_eval_aggregates_replay_and_friction(monkeypatch):
    from supervisor.eval.canary import run_canary_eval

    monkeypatch.setattr(
        "supervisor.eval.canary.run_replay_eval",
        lambda run_id, runtime_dir=".supervisor/runtime": {
            "run_id": run_id,
            "summary": {
                "decision_count": 2,
                "matched_count": 2 if run_id == "run_good" else 1,
                "mismatch_count": 0 if run_id == "run_good" else 1,
                "mismatch_kinds": {} if run_id == "run_good" else {"safety_regression": 1},
                "friction": {
                    "total_events": 0 if run_id == "run_good" else 2,
                    "by_kind": {} if run_id == "run_good" else {"repeated_confirmation": 2},
                    "by_signal": {} if run_id == "run_good" else {"user_repeated_approval": 2},
                },
                "pass_rate": 1.0 if run_id == "run_good" else 0.5,
            },
            "replay": {},
        },
    )
    monkeypatch.setattr(
        "supervisor.eval.canary.export_run",
        lambda run_id, runtime_dir=".supervisor/runtime": {"run_id": run_id},
    )
    monkeypatch.setattr(
        "supervisor.eval.canary.summarize_run",
        lambda exported: {
            "run_id": exported["run_id"],
            "top_state": "COMPLETED",
            "counts": {"friction_events": 0 if exported["run_id"] == "run_good" else 2},
        },
    )

    report = run_canary_eval(
        ["run_good", "run_risky"],
        runtime_dir=".supervisor/runtime",
        max_mismatch_rate=0.2,
        max_friction_events=1,
    )

    assert report["summary"]["run_count"] == 2
    assert report["summary"]["avg_pass_rate"] == 0.75
    assert report["summary"]["mismatch_kinds"]["safety_regression"] == 1
    assert report["summary"]["friction"]["by_kind"]["repeated_confirmation"] == 2
    assert report["decision"] == "rollback"


def test_run_canary_eval_holds_when_runs_are_noisy_but_not_unsafe(monkeypatch):
    from supervisor.eval.canary import run_canary_eval

    monkeypatch.setattr(
        "supervisor.eval.canary.run_replay_eval",
        lambda run_id, runtime_dir=".supervisor/runtime": {
            "run_id": run_id,
            "summary": {
                "decision_count": 4,
                "matched_count": 3,
                "mismatch_count": 1,
                "mismatch_kinds": {"ux_only_divergence": 1},
                "friction": {
                    "total_events": 1,
                    "by_kind": {"repeated_confirmation": 1},
                    "by_signal": {"user_repeated_approval": 1},
                },
                "pass_rate": 0.75,
            },
            "replay": {},
        },
    )
    monkeypatch.setattr(
        "supervisor.eval.canary.export_run",
        lambda run_id, runtime_dir=".supervisor/runtime": {"run_id": run_id},
    )
    monkeypatch.setattr(
        "supervisor.eval.canary.summarize_run",
        lambda exported: {
            "run_id": exported["run_id"],
            "top_state": "COMPLETED",
            "counts": {"friction_events": 1},
        },
    )

    report = run_canary_eval(
        ["run_a", "run_b"],
        runtime_dir=".supervisor/runtime",
        max_mismatch_rate=0.4,
        max_friction_events=0,
    )

    assert report["decision"] == "hold"
