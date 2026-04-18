"""Passive aggregation helpers for the layered system overview.

These folders build ``SystemSnapshot`` and its children out of already-
projected inputs:

- session records from ``session_index.collect_sessions``
- per-session event-plane summaries from
  ``supervisor.event_plane.surface.summarize_for_session`` (Task 3 will
  wire the actual collection call; this module stays pure)
- daemon registry counts

Keep this module passive — it must never read files or call out to
other subsystems directly. Inputs are always supplied by the caller.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from .models import (
    RunEventPlaneSummary,
    SystemAlert,
    SystemCounts,
    SystemSnapshot,
    SystemTimelineEvent,
)
from .session_index import SessionRecord


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

    paused = [s for s in sessions if not s.is_completed and not s.is_orphaned and s.pause_reason]
    if paused:
        alerts.append(SystemAlert(
            kind="paused_for_human",
            count=len(paused),
            summary=f"{len(paused)} run(s) paused waiting for operator input",
        ))

    orphaned = [s for s in sessions if s.is_orphaned]
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
