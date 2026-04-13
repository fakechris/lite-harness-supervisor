from __future__ import annotations

import fcntl
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


def _shared_dir(runtime_dir: str | Path = ".supervisor/runtime") -> Path:
    path = Path(runtime_dir) / "shared"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _friction_path(runtime_dir: str | Path = ".supervisor/runtime") -> Path:
    return _shared_dir(runtime_dir) / "friction_events.jsonl"


def _prefs_path(runtime_dir: str | Path = ".supervisor/runtime") -> Path:
    return _shared_dir(runtime_dir) / "user_preferences.json"


def _prefs_lock_path(runtime_dir: str | Path = ".supervisor/runtime") -> Path:
    return _shared_dir(runtime_dir) / "user_preferences.lock"


def _quarantine_corrupt_prefs(path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    quarantined = path.with_name(f"{path.name}.corrupt-{timestamp}")
    path.replace(quarantined)
    return quarantined


def _load_all_preferences(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        quarantined = _quarantine_corrupt_prefs(path)
        raise ValueError(f"corrupt user preferences store quarantined at {quarantined}") from exc
    except OSError as exc:
        raise ValueError(f"failed to read user preferences store: {path}") from exc
    if not isinstance(data, dict):
        quarantined = _quarantine_corrupt_prefs(path)
        raise ValueError(f"corrupt user preferences store quarantined at {quarantined}")
    return data


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


def summarize_friction_events(
    runtime_dir: str | Path,
    *,
    run_id: str = "",
    kind: str = "",
    user_id: str = "",
) -> dict:
    events = list_friction_events(
        runtime_dir,
        run_id=run_id,
        kind=kind,
        user_id=user_id,
    )
    by_kind = Counter()
    by_signal = Counter()
    for event in events:
        event_kind = str(event.get("kind", "") or "")
        if event_kind:
            by_kind[event_kind] += 1
        for signal in event.get("signals", []) or []:
            if signal:
                by_signal[str(signal)] += 1
    return {
        "total_events": len(events),
        "by_kind": dict(by_kind),
        "by_signal": dict(by_signal),
    }


def load_user_preferences(runtime_dir: str | Path, *, user_id: str = "default") -> dict:
    path = _prefs_path(runtime_dir)
    data = _load_all_preferences(path)
    prefs = data.get(user_id, {})
    if not isinstance(prefs, dict):
        raise ValueError(f"user preferences entry for {user_id!r} must be a mapping")
    return prefs


def save_user_preferences(
    runtime_dir: str | Path,
    updates: dict,
    *,
    user_id: str = "default",
) -> dict:
    if not isinstance(updates, dict):
        raise ValueError("updates must be a mapping")
    path = _prefs_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _prefs_lock_path(runtime_dir)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            data = _load_all_preferences(path)
            current = data.get(user_id, {})
            if not isinstance(current, dict):
                raise ValueError(f"user preferences entry for {user_id!r} must be a mapping")
            merged = dict(current)
            merged.update(updates)
            data[user_id] = merged

            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=f"{path.name}.tmp-",
                delete=False,
            ) as tmp_handle:
                tmp_handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                tmp_handle.flush()
                os.fsync(tmp_handle.fileno())
                tmp_name = tmp_handle.name
            os.replace(tmp_name, path)
            return merged
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
