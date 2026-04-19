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


def test_a2a_events_render_with_listener_info():
    """``a2a_started`` events must tell the operator where the listener
    is and whether auth is enforced — the whole point of exposing it in
    ``overview`` is to surface that state at a glance."""
    from supervisor.operator.system_overview import _timeline_summary

    started_auth = _timeline_summary(
        "a2a_started",
        {"host": "127.0.0.1", "port": 8081, "auth_required": True},
    )
    assert "127.0.0.1:8081" in started_auth
    assert "auth-required" in started_auth

    started_local = _timeline_summary(
        "a2a_started",
        {"host": "0.0.0.0", "port": 9000, "auth_required": False},
    )
    assert "localhost-only" in started_local

    stopped = _timeline_summary("a2a_stopped", {"host": "127.0.0.1", "port": 8081})
    assert "stopped" in stopped.lower()
    assert "127.0.0.1:8081" in stopped


def test_a2a_events_pass_system_events_allowlist():
    """``a2a_started`` / ``a2a_stopped`` must be persisted by
    ``append_system_event`` (v1 allowlist), not silently dropped."""
    from supervisor.storage.system_events import should_log_system_event

    assert should_log_system_event("a2a_started", {"host": "127.0.0.1", "port": 8081})
    assert should_log_system_event("a2a_stopped", {"host": "127.0.0.1", "port": 8081})


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
    session_id: str = "",
    event_plane: dict | None = None,
) -> SessionRecord:
    return SessionRecord(
        run_id=run_id,
        worktree_root="/w",
        spec_path="spec.yaml",
        controller_mode=controller_mode,
        top_state=top_state,
        current_node="n1",
        pane_target="tmux:0",
        daemon_socket="",
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
        session_id=session_id,
        event_plane=event_plane,
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
        _session(
            "r1", tag="paused-human", is_live=True,
            top_state="PAUSED_FOR_HUMAN", pause_reason="awaiting input",
        ),
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


def test_build_alerts_paused_by_top_state_even_without_reason():
    """Legacy / malformed paused runs carry top_state=PAUSED_FOR_HUMAN
    but an empty pause_reason string.  The alert must still fire —
    review finding: classifying by pause_reason silently hid them."""
    sessions = [
        _session(
            "r_legacy", tag="paused", is_live=True,
            top_state="PAUSED_FOR_HUMAN", pause_reason="",
        ),
    ]
    alerts = build_alerts(sessions=sessions, event_plane={})
    kinds = {a.kind for a in alerts}
    assert "paused_for_human" in kinds
    paused = next(a for a in alerts if a.kind == "paused_for_human")
    assert paused.count == 1


def test_build_alerts_orphan_excludes_paused():
    """A paused + orphaned session should only appear once, under the
    paused bucket — matching the pre-existing intent now that we
    classify by top_state instead of pause_reason."""
    sessions = [
        _session(
            "r_paused_orphan", tag="orphaned", is_orphaned=True,
            top_state="PAUSED_FOR_HUMAN", pause_reason="",
        ),
        _session(
            "r_pure_orphan", tag="orphaned", is_orphaned=True,
            top_state="RUNNING",
        ),
    ]
    alerts = build_alerts(sessions=sessions, event_plane={})
    paused_count = sum(a.count for a in alerts if a.kind == "paused_for_human")
    orphan_count = sum(a.count for a in alerts if a.kind == "orphaned")
    assert paused_count == 1
    assert orphan_count == 1


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


def test_session_event_plane_map_dedupes_by_session_id():
    """When two runs share a session_id (e.g. resume/restart), the
    event-plane fold must collapse them onto one entry — event-plane
    state is session-scoped, so summing per-run double-counts the
    shared backlog.  Review finding: waits_open=1/mailbox_new=2 became
    2/4 because the map was keyed by run_id."""
    from supervisor.operator.system_overview import _session_event_plane_map

    shared_ep = {
        "waits_open": 1, "mailbox_new": 2, "mailbox_acknowledged": 0,
        "requests_total": 1, "latest_mailbox_item_id": "mb_shared",
        "latest_wake_decision": "notify_operator",
    }
    sessions = [
        _session(
            "run_a", tag="daemon", is_live=True,
            session_id="sess_shared", event_plane=shared_ep,
        ),
        _session(
            "run_b", tag="daemon", is_live=True,
            session_id="sess_shared", event_plane=shared_ep,
        ),
    ]
    ep_map = _session_event_plane_map(sessions)
    assert len(ep_map) == 1
    assert "sess_shared" in ep_map
    counts = fold_counts(sessions=sessions, event_plane=ep_map, daemons=0)
    # One logical session's backlog — not doubled.
    assert counts.waits_open == 1
    assert counts.mailbox_new == 2


def test_local_daemon_filter_narrows_to_enclosing_worktree(tmp_path, monkeypatch):
    """``--local`` narrows sessions to the enclosing worktree; the
    daemon count must narrow along with them or the overview reports
    inconsistent local vs. global state.  Review finding."""
    import json

    worktree = tmp_path / "wt"
    runtime = worktree / ".supervisor" / "runtime"
    (runtime / "runs" / "run_local").mkdir(parents=True)
    (runtime / "runs" / "run_local" / "state.json").write_text(json.dumps({
        "run_id": "run_local",
        "session_id": "sess_local",
        "spec_id": "phase_x",
        "top_state": "RUNNING",
        "current_node_id": "n1",
        "pane_target": "tmux:0",
        "spec_path": str(worktree / "spec.yaml"),
        "workspace_root": str(worktree),
        "controller_mode": "daemon",
        "human_escalations": [],
        "delivery_state": "IDLE",
        "surface_type": "tmux",
    }))
    monkeypatch.chdir(worktree)
    # Two daemons: one owns our worktree, the other is elsewhere.
    other = tmp_path / "other"
    other.mkdir()
    daemons = [
        {"cwd": str(worktree), "pid": 100, "socket": "/tmp/a.sock"},
        {"cwd": str(other), "pid": 200, "socket": "/tmp/b.sock"},
    ]
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_daemons", lambda: daemons,
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
        "supervisor.operator.system_overview.list_daemons", lambda: daemons,
    )

    local_snap = load_system_snapshot(local_only=True)
    assert local_snap.counts.daemons == 1, (
        "local should count only the daemon bound to the enclosing worktree"
    )
    global_snap = load_system_snapshot(local_only=False)
    assert global_snap.counts.daemons == 2
