from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def rollout_registry_path(runtime_dir: str = ".supervisor/runtime") -> Path:
    return Path(runtime_dir).parent / "evals" / "rollouts.jsonl"


def record_rollout(
    *,
    candidate_id: str,
    phase: str,
    canary_report: dict,
    runtime_dir: str = ".supervisor/runtime",
) -> dict:
    candidate_id = str(candidate_id or "").strip()
    phase = str(phase or "").strip()
    if not candidate_id:
        raise ValueError("candidate_id is required")
    if not phase:
        raise ValueError("phase is required")

    record = {
        "candidate_id": candidate_id,
        "phase": phase,
        "decision": canary_report.get("decision", ""),
        "run_ids": list(canary_report.get("run_ids", []) or []),
        "summary": dict(canary_report.get("summary") or {}),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path = rollout_registry_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_rollouts(
    *,
    runtime_dir: str = ".supervisor/runtime",
    candidate_id: str = "",
) -> list[dict]:
    path = rollout_registry_path(runtime_dir)
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if candidate_id and item.get("candidate_id") != candidate_id:
                continue
            records.append(item)
    return records


def current_rollouts(history: list[dict]) -> dict[str, dict]:
    current: dict[str, dict] = {}
    for item in history:
        candidate_id = str(item.get("candidate_id", "")).strip()
        if not candidate_id:
            continue
        previous = current.get(candidate_id)
        if previous is None or str(item.get("saved_at", "")) >= str(previous.get("saved_at", "")):
            current[candidate_id] = item
    return current
