from __future__ import annotations

from collections import Counter

from supervisor.history import export_run, replay_run


def run_replay_eval(run_id: str, *, runtime_dir: str = ".supervisor/runtime") -> dict:
    exported = export_run(run_id, runtime_dir=runtime_dir)
    replay = replay_run(exported)
    decision_count = replay.get("decision_count", 0)
    matched_count = replay.get("matched_count", 0)
    mismatch_count = len(replay.get("mismatches", []))
    mismatch_kinds = Counter(
        item.get("mismatch_kind", "")
        for item in replay.get("mismatches", [])
        if item.get("mismatch_kind", "")
    )
    return {
        "run_id": run_id,
        "summary": {
            "decision_count": decision_count,
            "matched_count": matched_count,
            "mismatch_count": mismatch_count,
            "mismatch_kinds": dict(mismatch_kinds),
            "friction": exported.get("friction_summary") or {
                "total_events": len(exported.get("friction_events", [])),
                "by_kind": {},
                "by_signal": {},
            },
            "pass_rate": (matched_count / decision_count) if decision_count else 0.0,
        },
        "replay": replay,
    }
