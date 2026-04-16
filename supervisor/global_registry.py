"""Global registry for cross-worktree daemon discovery and pane ownership."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

GLOBAL_DIR_ENV = "THIN_SUPERVISOR_GLOBAL_DIR"


def _global_root() -> Path:
    configured = os.environ.get(GLOBAL_DIR_ENV, "").strip()
    if configured:
        root = Path(configured)
    else:
        root = Path.home() / ".local" / "state" / "thin-supervisor"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _daemons_dir() -> Path:
    path = _global_root() / "daemons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _panes_dir() -> Path:
    path = _global_root() / "panes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _daemon_path(socket_path: str) -> Path:
    digest = hashlib.sha1(socket_path.encode("utf-8")).hexdigest()[:12]
    return _daemons_dir() / f"{digest}.json"


def _pane_path(pane_target: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", pane_target).strip("_") or "pane"
    digest = hashlib.sha1(pane_target.encode("utf-8")).hexdigest()[:8]
    return _panes_dir() / f"{safe}-{digest}.json"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def register_daemon(metadata: dict) -> None:
    record = dict(metadata)
    record.setdefault("started_at", datetime.now(timezone.utc).isoformat())
    record.setdefault("active_runs", 0)
    _write_json(_daemon_path(record["socket"]), record)


def update_daemon(socket_path: str, **fields) -> None:
    path = _daemon_path(socket_path)
    record = _read_json(path) or {"socket": socket_path}
    record.update(fields)
    _write_json(path, record)


def unregister_daemon(socket_path: str) -> None:
    _daemon_path(socket_path).unlink(missing_ok=True)


def list_daemons() -> list[dict]:
    records: list[dict] = []
    for path in sorted(_daemons_dir().glob("*.json")):
        record = _read_json(path)
        if not record:
            path.unlink(missing_ok=True)
            continue
        if not _pid_alive(record.get("pid")):
            path.unlink(missing_ok=True)
            continue
        records.append(record)
    return sorted(records, key=lambda item: (item.get("cwd", ""), item.get("socket", "")))


def list_pane_owners() -> list[dict]:
    """List all active pane locks (stale locks with dead PIDs are cleaned up)."""
    records: list[dict] = []
    panes_dir = _panes_dir()
    for path in sorted(panes_dir.glob("*.json")):
        record = _read_json(path)
        if not record:
            path.unlink(missing_ok=True)
            continue
        if not _pid_alive(record.get("pid")):
            path.unlink(missing_ok=True)
            continue
        records.append(record)
    return records


def find_pane_owner(pane_target: str) -> dict | None:
    path = _pane_path(pane_target)
    owner = _read_json(path)
    if not owner:
        path.unlink(missing_ok=True)
        return None
    if not _pid_alive(owner.get("pid")):
        path.unlink(missing_ok=True)
        return None
    return owner


def acquire_pane_lock(pane_target: str, owner: dict) -> tuple[bool, dict | None]:
    path = _pane_path(pane_target)
    existing = find_pane_owner(pane_target)
    if existing:
        return False, existing

    record = dict(owner)
    record["pane_target"] = pane_target
    record.setdefault("acquired_at", datetime.now(timezone.utc).isoformat())

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False, find_pane_owner(pane_target)

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    return True, record


def release_pane_lock(pane_target: str, run_id: str) -> None:
    path = _pane_path(pane_target)
    owner = _read_json(path)
    if not owner:
        path.unlink(missing_ok=True)
        return
    if owner.get("run_id") != run_id:
        return
    path.unlink(missing_ok=True)


# ------------------------------------------------------------------
# Known worktrees — persists across daemon/pane lifecycle
# ------------------------------------------------------------------

def _worktrees_path() -> Path:
    return _global_root() / "known_worktrees.json"


def register_worktree(worktree_path: str) -> None:
    """Record a worktree path so the TUI can discover its runs later."""
    if not worktree_path:
        return
    resolved = str(Path(worktree_path).resolve())
    path = _worktrees_path()
    known = _read_json(path) or {"worktrees": []}
    wts = known.get("worktrees", [])
    if resolved not in wts:
        wts.append(resolved)
        known["worktrees"] = wts
        _write_json(path, known)


def list_known_worktrees() -> list[str]:
    """Return all known worktree paths (does not check liveness)."""
    path = _worktrees_path()
    data = _read_json(path) or {}
    return data.get("worktrees", [])
