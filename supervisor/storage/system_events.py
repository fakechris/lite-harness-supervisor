"""Shared cross-session observability log: ``system_events.jsonl``.

Writers append high-signal events here *in addition to* whatever they
already log at the per-run level.  Readers (``overview``, ``tui``) fold
this file plus ``collect_sessions`` plus the live daemon registry into a
single ``SystemSnapshot``.

Two hard rules:

1. **Observability only.**  This file is never a source of truth.  Run
   state authority stays in ``state.json`` and per-run
   ``session_log.jsonl``; correlation authority stays in the event-plane
   logs.  A reader must never derive a decision from this file alone.
2. **Frozen v1 allowlist.**  The set of promoted kinds is spelled out
   below so the system-level view stays signal-dense from day one.
   Adding a new kind is a one-line change; silently expanding it is
   forbidden.  For ``state_transition`` the allowlist narrows further
   to a handful of high-signal ``to_state`` values — everyday
   RUNNING/GATING/VERIFYING churn must not bleed into the system
   timeline.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state_store import _atomic_append_line

ALLOWED_SYSTEM_EVENT_KINDS: frozenset[str] = frozenset({
    "daemon_started",
    "daemon_stopped",
    "state_transition",
    "session_wait_expired",
    "session_mailbox_item_created",
    "wake_decision_applied",
})

# ``state_transition`` is promoted only when it signals "something
# actionable happened".  Suppressing the RUNNING↔GATING↔VERIFYING
# churn keeps ``overview`` readable without hiding the event from the
# per-run ``session_log.jsonl``, which remains the authoritative record.
STATE_TRANSITION_ALLOWED_TO_STATES: frozenset[str] = frozenset({
    "PAUSED_FOR_HUMAN",
    "RECOVERY_NEEDED",
    "COMPLETED",
    "FAILED",
    "ABORTED",
})


def should_log_system_event(kind: str, payload: dict[str, Any]) -> bool:
    """Return True if this kind+payload passes the frozen v1 allowlist."""
    if kind not in ALLOWED_SYSTEM_EVENT_KINDS:
        return False
    if kind == "state_transition":
        to_state = payload.get("to_state", "")
        return to_state in STATE_TRANSITION_ALLOWED_TO_STATES
    return True


def system_events_path(runtime_dir: str | Path) -> Path:
    """Absolute path to the shared ``system_events.jsonl`` for this runtime."""
    return Path(runtime_dir) / "shared" / "system_events.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_system_event(
    runtime_dir: str | Path,
    kind: str,
    payload: dict[str, Any],
    *,
    occurred_at: str = "",
) -> bool:
    """Append one record to ``system_events.jsonl`` if allowlisted.

    Returns True when the event was persisted, False when it was
    dropped by the allowlist or the best-effort write failed.

    Observability must never block a production path: filesystem errors
    while writing the shared log (read-only mount, disk full, permission
    denied, lock contention) are caught here so callers in daemon
    startup, mailbox writes, and cleanup paths do not fail because of a
    downstream observability hiccup.  The failure mode is visible in
    ``overview``'s completeness, not in runtime behaviour.
    """
    if not should_log_system_event(kind, payload):
        return False
    record = {
        "event_type": kind,
        "occurred_at": occurred_at or _now_iso(),
        "payload": dict(payload),
    }
    try:
        path = system_events_path(runtime_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_append_line(path, json.dumps(record, ensure_ascii=False))
    except (OSError, ValueError):
        return False
    return True


def read_recent_system_events(
    runtime_dir: str | Path,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` system events, newest first.

    Missing file → empty list.  Corrupt records are skipped quietly so
    a single bad line never takes down the operator view.
    """
    path = system_events_path(runtime_dir)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    records.reverse()
    return records[:limit]
