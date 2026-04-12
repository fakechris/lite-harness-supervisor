from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _shared_dir(runtime_dir: str | Path = ".supervisor/runtime") -> Path:
    path = Path(runtime_dir) / "shared"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _friction_path(runtime_dir: str | Path = ".supervisor/runtime") -> Path:
    return _shared_dir(runtime_dir) / "friction_events.jsonl"


def _prefs_path(runtime_dir: str | Path = ".supervisor/runtime") -> Path:
    return _shared_dir(runtime_dir) / "user_preferences.json"


def append_friction_event(
    runtime_dir: str | Path,
    *,
    kind: str,
    message: str,
    run_id: str = "",
    user_id: str = "default",
    signals: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    event = {
        "event_id": f"friction_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "message": message,
        "run_id": run_id,
        "user_id": user_id,
        "signals": list(signals or []),
        "metadata": dict(metadata or {}),
    }
    path = _friction_path(runtime_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def list_friction_events(
    runtime_dir: str | Path,
    *,
    run_id: str = "",
    kind: str = "",
    user_id: str = "",
) -> list[dict]:
    path = _friction_path(runtime_dir)
    if not path.exists():
        return []

    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if run_id and item.get("run_id") != run_id:
            continue
        if kind and item.get("kind") != kind:
            continue
        if user_id and item.get("user_id") != user_id:
            continue
        events.append(item)
    return events


def load_user_preferences(runtime_dir: str | Path, *, user_id: str = "default") -> dict:
    path = _prefs_path(runtime_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    prefs = data.get(user_id, {})
    return prefs if isinstance(prefs, dict) else {}


def save_user_preferences(
    runtime_dir: str | Path,
    updates: dict,
    *,
    user_id: str = "default",
) -> dict:
    path = _prefs_path(runtime_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    current = data.get(user_id, {})
    if not isinstance(current, dict):
        current = {}
    current.update(updates)
    data[user_id] = current
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return current
