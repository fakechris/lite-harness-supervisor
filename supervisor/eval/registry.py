from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def registry_path(runtime_dir: str = ".supervisor/runtime") -> Path:
    return Path(runtime_dir).parent / "evals" / "promotions.jsonl"


def promote_candidate(
    gate: dict,
    *,
    runtime_dir: str = ".supervisor/runtime",
    approved_by: str,
    force: bool = False,
) -> dict:
    decision = str(gate.get("decision", "")).strip()
    if decision in {"hold", "rollback"} and not force:
        raise ValueError(f"cannot promote candidate with gate decision={decision}")

    record = {
        "candidate_id": gate.get("candidate_id", ""),
        "candidate_policy": gate.get("candidate_policy", ""),
        "baseline_policy": gate.get("baseline_policy", ""),
        "suite": gate.get("suite", ""),
        "objective": gate.get("objective", ""),
        "touched_fragments": list(gate.get("touched_fragments", []) or []),
        "manifest_path": gate.get("manifest_path", ""),
        "status": "promoted",
        "gate_decision": decision,
        "approved_by": approved_by,
        "forced": bool(force),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }

    path = registry_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_promotions(runtime_dir: str = ".supervisor/runtime") -> list[dict]:
    path = registry_path(runtime_dir)
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def current_promotions(history: list[dict]) -> dict[str, dict]:
    current: dict[str, dict] = {}
    for item in history:
        suite = str(item.get("suite", "")).strip()
        if not suite or item.get("status") != "promoted":
            continue
        previous = current.get(suite)
        if previous is None or str(item.get("promoted_at", "")) >= str(previous.get("promoted_at", "")):
            current[suite] = item
    return current
