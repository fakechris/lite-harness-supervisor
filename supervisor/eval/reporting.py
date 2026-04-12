from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def default_report_dir(runtime_dir: str = ".supervisor/runtime") -> Path:
    return Path(runtime_dir).parent / "evals" / "reports"


def save_eval_report(
    payload: dict,
    *,
    report_kind: str,
    runtime_dir: str = ".supervisor/runtime",
    output_path: str = "",
) -> Path:
    path = Path(output_path) if output_path else _default_report_path(payload, report_kind, runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapped = {
        "report_kind": report_kind,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _default_report_path(payload: dict, report_kind: str, runtime_dir: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = (
        payload.get("suite")
        or payload.get("run_id")
        or payload.get("objective")
        or "report"
    )
    safe_stem = str(stem).replace("/", "-").replace(" ", "-")
    return default_report_dir(runtime_dir) / f"{timestamp}-{report_kind}-{safe_stem}.json"
