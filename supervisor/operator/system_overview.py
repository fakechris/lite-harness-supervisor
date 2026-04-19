"""Passive aggregation helpers for the layered system overview.

These folders build ``SystemSnapshot`` and its children out of already-
projected inputs:

- session records from ``session_index.collect_sessions``
- per-session event-plane summaries from
  ``supervisor.event_plane.surface.summarize_for_session``
- daemon registry counts

Keep this module passive — it must never read files or call out to
other subsystems directly. Inputs are always supplied by the caller.

High-level orchestration (``load_system_snapshot``) is a thin wrapper
that feeds ``collect_sessions`` + the global registry + the shared
``system_events.jsonl`` through this same passive core; tests exercise
both layers.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Mapping

from supervisor.global_registry import list_daemons
from supervisor.storage.system_events import read_recent_system_events

from .models import (
    RunEventPlaneSummary,
    SystemAlert,
    SystemCounts,
    SystemSnapshot,
    SystemTimelineEvent,
)
from .session_index import (
    SessionRecord,
    _find_enclosing_worktree_root,
    collect_sessions,
)


def fold_counts(
    *,
    sessions: Iterable[SessionRecord],
    event_plane: Mapping[str, RunEventPlaneSummary],
    daemons: int,
) -> SystemCounts:
    live = 0
    foreground = 0
    orphaned = 0
    completed = 0
    for s in sessions:
        if s.is_completed:
            completed += 1
            continue
        if s.is_orphaned:
            orphaned += 1
            continue
        if s.is_live:
            live += 1
            if s.controller_mode == "foreground":
                foreground += 1

    waits_open = sum(ep.waits_open for ep in event_plane.values())
    mailbox_new = sum(ep.mailbox_new for ep in event_plane.values())
    mailbox_acknowledged = sum(ep.mailbox_acknowledged for ep in event_plane.values())

    return SystemCounts(
        daemons=daemons,
        foreground_runs=foreground,
        live_sessions=live,
        orphaned_sessions=orphaned,
        completed_sessions=completed,
        waits_open=waits_open,
        mailbox_new=mailbox_new,
        mailbox_acknowledged=mailbox_acknowledged,
    )


def build_alerts(
    *,
    sessions: Iterable[SessionRecord],
    event_plane: Mapping[str, RunEventPlaneSummary],
) -> list[SystemAlert]:
    """Derive actionable alerts from session + event-plane signals.

    Kinds emitted here:

    - ``paused_for_human``: any non-completed, non-orphaned session with
      a pause_reason set. These need an operator decision.
    - ``orphaned``: sessions flagged is_orphaned (registry says no live
      owner).
    - ``mailbox_backlog``: at least one session has mailbox_new > 0.

    ``overdue_wait`` is reserved for Task 3 once the expiry sweep lands.
    """
    alerts: list[SystemAlert] = []

    # A paused run needs operator attention whether or not it currently
    # has a daemon.  Classify by ``top_state`` — the authoritative
    # signal — rather than ``pause_reason``, which can be empty on
    # legacy or malformed runs (and was the source of a miss flagged in
    # review: paused_for_human runs with no reason text were silently
    # dropping out of the alert list).  ``pause_reason`` is used only
    # for display downstream.
    paused = [
        s for s in sessions
        if not s.is_completed and s.top_state == "PAUSED_FOR_HUMAN"
    ]
    if paused:
        alerts.append(SystemAlert(
            kind="paused_for_human",
            count=len(paused),
            summary=f"{len(paused)} run(s) paused waiting for operator input",
        ))

    # Orphan alert covers sessions with no live owner that are *not*
    # already being flagged as paused_for_human, so the operator sees
    # each actionable condition once.
    orphaned = [
        s for s in sessions
        if s.is_orphaned and s.top_state != "PAUSED_FOR_HUMAN"
    ]
    if orphaned:
        alerts.append(SystemAlert(
            kind="orphaned",
            count=len(orphaned),
            summary=f"{len(orphaned)} session(s) with no live owner",
        ))

    backlog_total = sum(ep.mailbox_new for ep in event_plane.values())
    if backlog_total > 0:
        sessions_with_backlog = sum(1 for ep in event_plane.values() if ep.mailbox_new > 0)
        alerts.append(SystemAlert(
            kind="mailbox_backlog",
            count=backlog_total,
            summary=f"{backlog_total} new mailbox item(s) across {sessions_with_backlog} session(s)",
        ))

    return alerts


def build_system_snapshot(
    *,
    sessions: Iterable[SessionRecord],
    event_plane: Mapping[str, RunEventPlaneSummary],
    daemons: int,
    recent_timeline: Iterable[SystemTimelineEvent] = (),
) -> SystemSnapshot:
    session_list = list(sessions)
    counts = fold_counts(sessions=session_list, event_plane=event_plane, daemons=daemons)
    alerts = build_alerts(sessions=session_list, event_plane=event_plane)
    return SystemSnapshot(
        counts=counts,
        alerts=alerts,
        recent_timeline=list(recent_timeline),
        sessions=session_list,
    )


# ── timeline + snapshot orchestration ────────────────────────────────


def _timeline_summary(event_type: str, payload: dict) -> str:
    """One-line render for a system-scope event. Best-effort: unknown
    kinds fall back to the raw ``event_type`` humanized."""
    if event_type == "daemon_started":
        return f"daemon started (pid {payload.get('pid', '?')})"
    if event_type == "daemon_stopped":
        return f"daemon stopped (pid {payload.get('pid', '?')})"
    if event_type == "state_transition":
        frm = payload.get("from_state", "")
        to = payload.get("to_state", "")
        reason = payload.get("reason", "")
        tail = f" — {reason}" if reason else ""
        return f"{frm} → {to}{tail}"
    if event_type == "session_mailbox_item_created":
        return f"mailbox item arrived ({payload.get('source_kind', '')})"
    if event_type == "wake_decision_applied":
        return f"wake decision: {payload.get('decision', '')}"
    if event_type == "session_wait_expired":
        return f"wait expired ({payload.get('wait_kind', '')})"
    if event_type == "a2a_started":
        host = payload.get("host", "?")
        port = payload.get("port", "?")
        auth = "auth-required" if payload.get("auth_required") else "localhost-only"
        return f"A2A adapter listening on {host}:{port} ({auth})"
    if event_type == "a2a_stopped":
        return f"A2A adapter stopped ({payload.get('host', '?')}:{payload.get('port', '?')})"
    return event_type.replace("_", " ")


def build_recent_system_timeline(
    runtime_roots: Iterable[str | Path],
    *,
    limit: int = 20,
) -> list[SystemTimelineEvent]:
    """Read shared ``system_events.jsonl`` across every runtime root and
    return the newest ``limit`` events as ``SystemTimelineEvent``s.

    Duplicates are suppressed only by identity (same event_type +
    occurred_at + payload). Writers stamp a monotonic ISO timestamp per
    append, so this is enough to dedup a worktree that appears in more
    than one discovery source (cwd + known_worktrees + git).
    """
    seen: set[tuple[str, str, str]] = set()
    events: list[SystemTimelineEvent] = []
    for root in runtime_roots:
        records = read_recent_system_events(root, limit=limit * 4)
        for rec in records:
            event_type = rec.get("event_type", "")
            occurred_at = rec.get("occurred_at", "")
            payload = rec.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            # Use a stable key that stays the same across worktree
            # duplicates: event_type + occurred_at + a cheap payload
            # digest over the ordered items.
            key = (event_type, occurred_at, repr(sorted(payload.items())))
            if key in seen:
                continue
            seen.add(key)
            session_id = str(payload.get("session_id", "") or "")
            run_id = str(payload.get("run_id", "") or "")
            scope = "session" if session_id or run_id else "system"
            events.append(SystemTimelineEvent(
                event_type=event_type,
                occurred_at=occurred_at,
                scope=scope,
                session_id=session_id,
                run_id=run_id,
                summary=_timeline_summary(event_type, payload),
                payload=payload,
            ))
    events.sort(key=lambda e: e.occurred_at, reverse=True)
    return events[:limit]


def _session_event_plane_map(
    sessions: Iterable[SessionRecord],
) -> dict[str, RunEventPlaneSummary]:
    """Lift each session's persisted ``event_plane`` dict into a typed
    ``RunEventPlaneSummary`` keyed by ``session_id``.

    Event-plane state (waits, mailbox items, requests) is correlated by
    ``session_id`` — a single logical session can own several run_ids
    across resume/restart cycles, and every one of those runs folds the
    *same* event-plane log.  Keying by run_id here would cause
    ``fold_counts`` to sum the same backlog once per run, inflating
    counts (1 mailbox item + 2 runs → ``mailbox_new=2``).

    Sessions without a session_id fall back to the run_id so
    pre-session-first runs still contribute a single entry without
    colliding with each other.  Sessions without an event-plane block
    are skipped so ``fold_counts`` treats them as zero-backlog.
    """
    out: dict[str, RunEventPlaneSummary] = {}
    for s in sessions:
        ep = getattr(s, "event_plane", None)
        if not ep:
            continue
        key = s.session_id or s.run_id
        if not key or key in out:
            continue
        try:
            out[key] = RunEventPlaneSummary(
                waits_open=int(ep.get("waits_open", 0)),
                mailbox_new=int(ep.get("mailbox_new", 0)),
                mailbox_acknowledged=int(ep.get("mailbox_acknowledged", 0)),
                requests_total=int(ep.get("requests_total", 0)),
                latest_mailbox_item_id=str(ep.get("latest_mailbox_item_id", "") or ""),
                latest_wake_decision=str(ep.get("latest_wake_decision", "") or ""),
            )
        except (TypeError, ValueError):
            continue
    return out


def load_system_snapshot(
    *,
    local_only: bool = False,
    timeline_limit: int = 20,
) -> SystemSnapshot:
    """End-to-end passive build of a ``SystemSnapshot``.

    Stitches together the three inputs the overview needs:

    - ``collect_sessions`` (canonical cross-worktree session list with
      event-plane summaries already folded in)
    - ``list_daemons`` from the global registry
    - ``system_events.jsonl`` across every discovered runtime root

    Nothing here mutates state. This is the single entry point for
    ``overview``, ``tui`` (global mode), and any other surface that
    wants a coherent snapshot.
    """
    sessions = collect_sessions(local_only=local_only)
    event_plane = _session_event_plane_map(sessions)
    daemons = list_daemons()
    if local_only:
        # Narrow the daemon count to daemons whose cwd resolves to the
        # enclosing local worktree — otherwise ``--local`` would narrow
        # sessions but keep showing every daemon across the machine,
        # which is internally inconsistent and misreports local load.
        daemons = _filter_local_daemons(daemons)
    runtime_roots: list[str] = []
    seen_roots: set[str] = set()
    for s in sessions:
        if not s.worktree_root:
            continue
        root = str(Path(s.worktree_root) / ".supervisor" / "runtime")
        if root in seen_roots:
            continue
        seen_roots.add(root)
        runtime_roots.append(root)
    recent = build_recent_system_timeline(runtime_roots, limit=timeline_limit)
    return build_system_snapshot(
        sessions=sessions,
        event_plane=event_plane,
        daemons=len(daemons),
        recent_timeline=recent,
    )


def _filter_local_daemons(daemons: list[dict]) -> list[dict]:
    """Return the subset of daemons whose cwd resolves to the local
    enclosing worktree root.

    ``collect_sessions(local_only=True)`` uses the same enclosing-root
    derivation (``_find_enclosing_worktree_root``), so this keeps the
    daemon count and session list referring to the same scope.
    """
    try:
        local_root = Path(_find_enclosing_worktree_root(os.getcwd())).resolve()
    except (OSError, RuntimeError):
        return daemons
    filtered: list[dict] = []
    for d in daemons:
        raw = d.get("cwd", "")
        if not raw:
            continue
        try:
            if Path(raw).resolve() == local_root:
                filtered.append(d)
        except (OSError, RuntimeError):
            continue
    return filtered
