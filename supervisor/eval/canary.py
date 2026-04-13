from __future__ import annotations

from collections import Counter

from supervisor.eval.replay import run_replay_eval
from supervisor.history import export_run, summarize_run


def run_canary_eval(
    run_ids: list[str],
    *,
    runtime_dir: str = ".supervisor/runtime",
    max_mismatch_rate: float = 0.25,
    max_friction_events: int = 0,
) -> dict:
    if not run_ids:
        raise ValueError("at least one run_id is required")

    runs: list[dict] = []
    mismatch_kinds = Counter()
    friction_by_kind = Counter()
    friction_by_signal = Counter()
    total_decisions = 0
    total_mismatches = 0
    total_friction_events = 0
    pass_rates: list[float] = []

    for run_id in run_ids:
        replay_report = run_replay_eval(run_id, runtime_dir=runtime_dir)
        exported = export_run(run_id, runtime_dir=runtime_dir)
        run_summary = summarize_run(exported)
        replay_summary = replay_report["summary"]
        friction = replay_summary.get("friction", {})

        total_decisions += int(replay_summary.get("decision_count", 0))
        total_mismatches += int(replay_summary.get("mismatch_count", 0))
        total_friction_events += int(friction.get("total_events", 0))
        pass_rates.append(float(replay_summary.get("pass_rate", 0.0)))
        mismatch_kinds.update(replay_summary.get("mismatch_kinds", {}))
        friction_by_kind.update(friction.get("by_kind", {}))
        friction_by_signal.update(friction.get("by_signal", {}))

        runs.append(
            {
                "run_id": run_id,
                "top_state": run_summary.get("top_state", "UNKNOWN"),
                "pass_rate": replay_summary.get("pass_rate", 0.0),
                "mismatch_count": replay_summary.get("mismatch_count", 0),
                "mismatch_kinds": replay_summary.get("mismatch_kinds", {}),
                "friction": friction,
            }
        )

    mismatch_rate = (total_mismatches / total_decisions) if total_decisions else 0.0
    avg_pass_rate = (sum(pass_rates) / len(pass_rates)) if pass_rates else 0.0
    decision = _promotion_decision(
        mismatch_kinds=dict(mismatch_kinds),
        mismatch_rate=mismatch_rate,
        total_friction_events=total_friction_events,
        max_mismatch_rate=max_mismatch_rate,
        max_friction_events=max_friction_events,
    )

    return {
        "run_ids": run_ids,
        "thresholds": {
            "max_mismatch_rate": max_mismatch_rate,
            "max_friction_events": max_friction_events,
        },
        "decision": decision,
        "summary": {
            "run_count": len(run_ids),
            "decision_count": total_decisions,
            "mismatch_count": total_mismatches,
            "mismatch_rate": mismatch_rate,
            "avg_pass_rate": avg_pass_rate,
            "mismatch_kinds": dict(mismatch_kinds),
            "friction": {
                "total_events": total_friction_events,
                "by_kind": dict(friction_by_kind),
                "by_signal": dict(friction_by_signal),
            },
        },
        "runs": runs,
    }


def _promotion_decision(
    *,
    mismatch_kinds: dict,
    mismatch_rate: float,
    total_friction_events: int,
    max_mismatch_rate: float,
    max_friction_events: int,
) -> str:
    if mismatch_kinds.get("safety_regression", 0) > 0:
        return "rollback"
    if mismatch_rate > max_mismatch_rate:
        return "hold"
    if total_friction_events > max_friction_events:
        return "hold"
    return "promote"
