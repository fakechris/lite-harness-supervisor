"""Tests for Task 1: observability models + passive folding helpers.

Covers:
- round-trip of the five new models
- deterministic counts aggregation from session records + per-session
  event-plane summaries
- alert derivation from counts / session signals
"""
from __future__ import annotations

from supervisor.operator.models import (
    RunEventPlaneSummary,
    SystemAlert,
    SystemCounts,
    SystemSnapshot,
    SystemTimelineEvent,
)
from supervisor.operator.session_index import SessionRecord
from supervisor.operator.system_overview import (
    build_alerts,
    build_recent_system_timeline,
    build_system_snapshot,
    fold_counts,
    load_system_snapshot,
)


# ─── model round-trips ─────────────────────────────────────────────────

def test_run_event_plane_summary_round_trip():
    summary = RunEventPlaneSummary(
        waits_open=2,
        mailbox_new=1,
        mailbox_acknowledged=3,
        requests_total=5,
        latest_mailbox_item_id="mb_7",
        latest_wake_decision="notify_operator",
    )
    assert summary.to_dict() == {
        "waits_open": 2,
        "mailbox_new": 1,
        "mailbox_acknowledged": 3,
        "requests_total": 5,
        "latest_mailbox_item_id": "mb_7",
        "latest_wake_decision": "notify_operator",
    }


def test_system_counts_round_trip():
    counts = SystemCounts(
        daemons=2,
        foreground_runs=1,
        live_sessions=3,
        orphaned_sessions=1,
        completed_sessions=4,
        waits_open=2,
        mailbox_new=1,
        mailbox_acknowledged=0,
    )
    d = counts.to_dict()
    assert d["daemons"] == 2
    assert d["live_sessions"] == 3
    assert d["mailbox_new"] == 1


def test_system_alert_round_trip():
    alert = SystemAlert(kind="paused_for_human", count=2, summary="2 runs need input")
    assert alert.to_dict() == {
        "kind": "paused_for_human",
        "count": 2,
        "summary": "2 runs need input",
    }


def test_system_timeline_event_round_trip():
    ev = SystemTimelineEvent(
        event_type="daemon_started",
        occurred_at="2026-04-18T10:00:00+00:00",
        scope="system",
        session_id="",
        run_id="",
        summary="daemon started",
        payload={"pid": 4321},
    )
    d = ev.to_dict()
    assert d["event_type"] == "daemon_started"
    assert d["scope"] == "system"
    assert d["payload"] == {"pid": 4321}


def test_system_snapshot_round_trip():
    snapshot = SystemSnapshot(
        counts=SystemCounts(
            daemons=1,
            foreground_runs=0,
            live_sessions=1,
            orphaned_sessions=0,
            completed_sessions=0,
            waits_open=0,
            mailbox_new=0,
            mailbox_acknowledged=0,
        ),
        alerts=[],
        recent_timeline=[],
        sessions=[],
    )
    d = snapshot.to_dict()
    assert d["counts"]["daemons"] == 1
    assert d["alerts"] == []
    assert d["recent_timeline"] == []
    assert d["sessions"] == []


# ─── folding ───────────────────────────────────────────────────────────

def _session(
    run_id: str,
    *,
    tag: str,
    is_live: bool = False,
    is_orphaned: bool = False,
    is_completed: bool = False,
    controller_mode: str = "daemon",
    top_state: str = "RUNNING",
    pause_reason: str = "",
) -> SessionRecord:
    return SessionRecord(
        run_id=run_id,
        worktree_root="/w",
        spec_path="spec.yaml",
        controller_mode=controller_mode,
        top_state=top_state,
        current_node="n1",
        pane_target="tmux:0",
        daemon_socket="/tmp/d.sock",
        is_live=is_live,
        is_orphaned=is_orphaned,
        is_completed=is_completed,
        pause_reason=pause_reason,
        next_action="",
        last_checkpoint_summary="",
        last_update_at="2026-04-18T10:00:00+00:00",
        surface_type="tmux",
        tag=tag,
        pause_class="",
    )


def test_fold_counts_aggregates_sessions_and_event_plane():
    sessions = [
        _session("r1", tag="daemon", is_live=True, controller_mode="daemon"),
        _session("r2", tag="foreground", is_live=True, controller_mode="foreground"),
        _session("r3", tag="orphaned", is_orphaned=True),
        _session("r4", tag="completed", is_completed=True),
    ]
    event_plane = {
        "r1": RunEventPlaneSummary(
            waits_open=1, mailbox_new=1, mailbox_acknowledged=0,
            requests_total=1, latest_mailbox_item_id="mb_1",
            latest_wake_decision="notify_operator",
        ),
        "r2": RunEventPlaneSummary(
            waits_open=0, mailbox_new=0, mailbox_acknowledged=2,
            requests_total=2, latest_mailbox_item_id="mb_2",
            latest_wake_decision="",
        ),
    }
    counts = fold_counts(sessions=sessions, event_plane=event_plane, daemons=2)
    assert counts.daemons == 2
    assert counts.foreground_runs == 1
    assert counts.live_sessions == 2
    assert counts.orphaned_sessions == 1
    assert counts.completed_sessions == 1
    assert counts.waits_open == 1
    assert counts.mailbox_new == 1
    assert counts.mailbox_acknowledged == 2


def test_build_alerts_surfaces_paused_and_backlog():
    sessions = [
        _session("r1", tag="paused-human", is_live=True, pause_reason="awaiting input"),
        _session("r2", tag="orphaned", is_orphaned=True),
    ]
    event_plane = {
        "r1": RunEventPlaneSummary(
            waits_open=0, mailbox_new=3, mailbox_acknowledged=0,
            requests_total=3, latest_mailbox_item_id="mb_9",
            latest_wake_decision="notify_operator",
        ),
    }
    alerts = build_alerts(sessions=sessions, event_plane=event_plane)
    kinds = {a.kind for a in alerts}
    assert "paused_for_human" in kinds
    assert "orphaned" in kinds
    assert "mailbox_backlog" in kinds
    paused = next(a for a in alerts if a.kind == "paused_for_human")
    assert paused.count == 1


def test_build_alerts_quiet_when_nothing_actionable():
    sessions = [
        _session("r1", tag="daemon", is_live=True),
    ]
    event_plane = {
        "r1": RunEventPlaneSummary(
            waits_open=0, mailbox_new=0, mailbox_acknowledged=0,
            requests_total=0, latest_mailbox_item_id="",
            latest_wake_decision="",
        ),
    }
    assert build_alerts(sessions=sessions, event_plane=event_plane) == []


def test_build_system_snapshot_end_to_end():
    sessions = [
        _session("r1", tag="daemon", is_live=True),
        _session("r2", tag="completed", is_completed=True),
    ]
    event_plane = {
        "r1": RunEventPlaneSummary(
            waits_open=1, mailbox_new=0, mailbox_acknowledged=0,
            requests_total=1, latest_mailbox_item_id="mb_a",
            latest_wake_decision="",
        ),
    }
    timeline = [
        SystemTimelineEvent(
            event_type="daemon_started",
            occurred_at="2026-04-18T09:00:00+00:00",
            scope="system",
            session_id="",
            run_id="",
            summary="daemon started",
            payload={},
        ),
    ]
    snapshot = build_system_snapshot(
        sessions=sessions,
        event_plane=event_plane,
        daemons=1,
        recent_timeline=timeline,
    )
    assert snapshot.counts.daemons == 1
    assert snapshot.counts.live_sessions == 1
    assert snapshot.counts.completed_sessions == 1
    assert snapshot.counts.waits_open == 1
    assert len(snapshot.sessions) == 2
    assert len(snapshot.recent_timeline) == 1
    # r1 has an open wait — no alert fires until the overdue-wait signal
    # is wired (Task 3). Keep that assertion explicit so future work
    # surfaces as a test change, not a silent drift.
    assert all(a.kind != "overdue_wait" for a in snapshot.alerts)


# ─── shared system_events.jsonl aggregation ───────────────────────────

def test_build_recent_system_timeline_merges_roots_newest_first(tmp_path):
    """Shared system_events can live under multiple runtime roots; the
    timeline folder merges them, dedups across discovery sources, and
    returns the newest first."""
    from supervisor.storage.system_events import append_system_event

    root_a = tmp_path / "a" / ".supervisor" / "runtime"
    root_b = tmp_path / "b" / ".supervisor" / "runtime"

    append_system_event(
        root_a, "daemon_started",
        {"pid": 111, "cwd": "/a"}, occurred_at="2026-04-18T10:00:00+00:00",
    )
    append_system_event(
        root_b, "daemon_started",
        {"pid": 222, "cwd": "/b"}, occurred_at="2026-04-18T11:00:00+00:00",
    )
    # Duplicate discovery of root_a (e.g. cwd + known_worktrees).  The
    # folder must not double-count the event.
    events = build_recent_system_timeline([root_a, root_b, root_a], limit=10)
    assert [e.payload.get("pid") for e in events] == [222, 111]
    # Newest first carries a system-scope label since there's no
    # session_id / run_id on daemon lifecycle events.
    assert all(e.scope == "system" for e in events)


def test_build_recent_system_timeline_state_transition_renders_scope_session(tmp_path):
    from supervisor.storage.system_events import append_system_event

    root = tmp_path / ".supervisor" / "runtime"
    append_system_event(
        root, "state_transition",
        {
            "from_state": "RUNNING",
            "to_state": "PAUSED_FOR_HUMAN",
            "reason": "awaiting input",
            "session_id": "sess_1",
            "run_id": "run_1",
        },
        occurred_at="2026-04-18T12:00:00+00:00",
    )
    events = build_recent_system_timeline([root], limit=10)
    assert len(events) == 1
    ev = events[0]
    assert ev.scope == "session"
    assert ev.session_id == "sess_1"
    assert ev.run_id == "run_1"
    assert "RUNNING → PAUSED_FOR_HUMAN" in ev.summary


def test_load_system_snapshot_end_to_end_reads_real_sessions(tmp_path, monkeypatch):
    """Full orchestration: write a state.json under a fake worktree,
    append a shared system_event, and ensure ``load_system_snapshot``
    returns a coherent SystemSnapshot that threads all three inputs."""
    import json

    from supervisor.storage.system_events import append_system_event

    worktree = tmp_path / "worktree"
    runtime = worktree / ".supervisor" / "runtime"
    run_dir = runtime / "runs" / "run_load"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": "run_load",
        "session_id": "sess_load",
        "spec_id": "phase_x",
        "top_state": "RUNNING",
        "current_node_id": "n1",
        "pane_target": "tmux:0",
        "spec_path": str(worktree / "spec.yaml"),
        "workspace_root": str(worktree),
        "controller_mode": "local",
        "human_escalations": [],
        "delivery_state": "IDLE",
        "surface_type": "tmux",
    }))
    append_system_event(
        runtime, "daemon_started",
        {"pid": 9, "cwd": str(worktree)},
        occurred_at="2026-04-18T09:00:00+00:00",
    )

    monkeypatch.chdir(worktree)
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_daemons", lambda: [],
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_pane_owners", lambda: [],
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_known_worktrees", lambda: [],
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index._discover_git_worktrees",
        lambda cwd: [],
    )
    monkeypatch.setattr(
        "supervisor.operator.system_overview.list_daemons", lambda: [],
    )

    snapshot = load_system_snapshot(local_only=True)
    assert snapshot.counts.daemons == 0
    assert len(snapshot.sessions) == 1
    assert snapshot.sessions[0].run_id == "run_load"
    assert any(e.event_type == "daemon_started" for e in snapshot.recent_timeline)
