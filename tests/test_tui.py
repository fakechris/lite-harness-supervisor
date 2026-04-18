"""Tests for TUI formatting logic (non-interactive parts)."""

import os
from pathlib import Path
from unittest.mock import patch

from supervisor.operator.tui import (
    format_run_line,
    format_snapshot,
    format_timeline,
    format_explanation,
    format_exchange,
    format_notes,
    format_clarification,
    collect_runs,
)
from supervisor.operator.tui import (
    format_system_banner,
    format_system_alerts,
    format_system_timeline,
    format_actionable_sessions,
)


def _make_run(**overrides):
    base = {
        "run_id": "run_abc123def456",
        "tag": "daemon",
        "top_state": "RUNNING",
        "current_node": "step_1",
        "pane_target": "%5",
        "worktree": "/tmp/ws",
        "socket": "/tmp/sock",
    }
    base.update(overrides)
    return base


class TestFormatRunLine:
    def test_basic(self):
        line = format_run_line(_make_run())
        assert "abc123def456" in line
        assert "RUNNING" in line
        assert "daemon" in line

    def test_selected(self):
        line = format_run_line(_make_run(), selected=True)
        assert line.startswith(">")

    def test_unselected(self):
        line = format_run_line(_make_run(), selected=False)
        assert line.startswith(" ")

    def test_long_state_truncated(self):
        line = format_run_line(_make_run(top_state="PAUSED_FOR_HUMAN"))
        assert "PAUSED_FOR" in line


class TestFormatSnapshot:
    def test_basic_fields(self):
        snap = {
            "run_id": "run_abc",
            "spec_id": "my-spec",
            "top_state": "RUNNING",
            "current_node": "step_1",
            "current_attempt": 1,
            "surface_type": "tmux",
            "surface_target": "%5",
            "worktree_root": "/tmp/ws",
            "delivery_state": "IDLE",
            "done_nodes": ["step_0"],
        }
        lines = format_snapshot(snap)
        text = "\n".join(lines)

        assert "run_abc" in text
        assert "RUNNING" in text
        assert "step_1" in text
        assert "step_0" in text

    def test_pause_reason(self):
        snap = {
            "run_id": "r",
            "spec_id": "s",
            "top_state": "PAUSED_FOR_HUMAN",
            "current_node": "n",
            "current_attempt": 0,
            "surface_type": "tmux",
            "surface_target": "%0",
            "worktree_root": "/",
            "delivery_state": "IDLE",
            "done_nodes": [],
            "pause_reason": "verification failed",
        }
        lines = format_snapshot(snap)
        text = "\n".join(lines)
        assert "verification failed" in text

    def test_no_done_nodes(self):
        snap = {
            "run_id": "r",
            "spec_id": "s",
            "top_state": "READY",
            "current_node": "n",
            "current_attempt": 0,
            "surface_type": "tmux",
            "surface_target": "%0",
            "worktree_root": "/",
            "delivery_state": "IDLE",
            "done_nodes": [],
        }
        lines = format_snapshot(snap)
        text = "\n".join(lines)
        assert "(none)" in text


class TestFormatTimeline:
    def test_with_events(self):
        events = [
            {"occurred_at": "2026-04-15T10:00:00Z", "event_type": "checkpoint",
             "summary": "step 1 done"},
            {"occurred_at": "2026-04-15T10:01:00Z", "event_type": "gate_decision",
             "summary": "CONTINUE"},
        ]
        lines = format_timeline(events)
        text = "\n".join(lines)
        assert "Timeline:" in text
        assert "checkpoint" in text
        assert "step 1 done" in text

    def test_empty(self):
        lines = format_timeline([])
        text = "\n".join(lines)
        assert "(no events)" in text

    def test_max_15_events(self):
        events = [
            {"occurred_at": f"2026-04-15T10:{i:02d}:00Z",
             "event_type": "checkpoint", "summary": f"step {i}"}
            for i in range(20)
        ]
        lines = format_timeline(events)
        # Header + "(no events)" line count should be <= 17 (2 header + 15 events)
        event_lines = [l for l in lines if l.startswith("  2026")]
        assert len(event_lines) <= 15


class TestFormatExplanation:
    def test_explain_run_format(self):
        result = {
            "explanation": "The run is working on step_1",
            "current_activity": "Writing tests",
            "recent_progress": "Completed step_0",
            "next_expected": "Move to step_2",
            "confidence": 0.8,
        }
        lines = format_explanation(result)
        text = "\n".join(lines)
        assert "Explanation:" in text
        assert "Working on step_1" in text or "working on step_1" in text
        assert "Writing tests" in text

    def test_drift_format(self):
        result = {
            "status": "watch",
            "reasons": ["High retry count"],
            "recommended_action": "Monitor",
            "confidence": 0.5,
        }
        lines = format_explanation(result)
        text = "\n".join(lines)
        assert "watch" in text
        assert "High retry count" in text
        assert "Monitor" in text

    def test_empty(self):
        lines = format_explanation({})
        assert len(lines) == 2  # header + "(none)"


class TestFormatExchange:
    def test_basic(self):
        exchange = {
            "run_id": "run_abc",
            "last_checkpoint_summary": "step 1 completed",
            "last_instruction_summary": "continue to step 2",
            "checkpoint_excerpt": "wrote files",
            "instruction_excerpt": "instruction to step_2",
            "recent_event_count": 5,
        }
        lines = format_exchange(exchange)
        text = "\n".join(lines)
        assert "Exchange:" in text
        assert "step 1 completed" in text
        assert "continue to step 2" in text

    def test_empty_exchange(self):
        lines = format_exchange({})
        text = "\n".join(lines)
        assert "(none)" in text


class TestFormatNotes:
    def test_with_notes(self):
        notes = [
            {"timestamp": "2026-04-15T10:00:00Z", "author_run_id": "human", "content": "check step 2"},
            {"timestamp": "2026-04-15T10:01:00Z", "author_run_id": "op_abc", "content": "looks good"},
        ]
        lines = format_notes(notes)
        text = "\n".join(lines)
        assert "Notes:" in text
        assert "check step 2" in text
        assert "looks good" in text

    def test_empty_notes(self):
        lines = format_notes([])
        assert "(no notes)" in "\n".join(lines)


class TestFormatClarification:
    def test_with_answer(self):
        result = {
            "answer": "The run paused because verification failed on step_2",
            "evidence": ["top_state=PAUSED_FOR_HUMAN", "last_event=verification_failed"],
            "confidence": 0.7,
            "follow_up": "Check the verification criteria",
        }
        lines = format_clarification(result)
        text = "\n".join(lines)
        assert "Answer:" in text
        assert "verification failed" in text
        assert "Evidence:" in text
        assert "Follow-up:" in text

    def test_empty_result(self):
        lines = format_clarification({})
        assert "(no answer)" in "\n".join(lines)


class TestCollectRunsLocal:
    def _patch(self, monkeypatch, *, known=(), daemons=(), panes=()):
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_known_worktrees",
            lambda: list(known),
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons",
            lambda: list(daemons),
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_pane_owners",
            lambda: list(panes),
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index._discover_git_worktrees",
            lambda cwd: [],
        )

    def test_collect_with_disk_runs(self, tmp_path, monkeypatch):
        """collect_runs surfaces a paused on-disk run (via session_index)."""
        import json

        monkeypatch.chdir(tmp_path)
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_test123"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_test123",
            "top_state": "PAUSED_FOR_HUMAN",
            "current_node_id": "step_1",
            "pane_target": "%5",
            "workspace_root": "/tmp/ws",
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        self._patch(monkeypatch)

        items = collect_runs()
        assert len(items) == 1
        assert items[0]["run_id"] == "run_test123"
        assert items[0]["tag"] == "paused"
        assert items[0]["top_state"] == "PAUSED_FOR_HUMAN"

    def test_collect_completed_run(self, tmp_path, monkeypatch):
        import json

        monkeypatch.chdir(tmp_path)
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_done999"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_done999",
            "top_state": "COMPLETED",
            "current_node_id": "",
            "done_node_ids": ["step_1", "step_2"],
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        self._patch(monkeypatch)

        items = collect_runs()
        assert len(items) == 1
        assert items[0]["tag"] == "completed"

    def test_collect_from_known_worktree(self, tmp_path, monkeypatch):
        """collect_runs discovers runs from known_worktrees registry."""
        import json

        empty_cwd = tmp_path / "empty"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)

        other_wt = tmp_path / "other_worktree"
        run_dir = other_wt / ".supervisor" / "runtime" / "runs" / "run_wt_only"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_wt_only",
            "top_state": "COMPLETED",
            "current_node_id": "",
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        self._patch(monkeypatch, known=[str(other_wt)])

        items = collect_runs()
        assert any(i["run_id"] == "run_wt_only" for i in items)


class TestGlobalRegistry:
    def test_register_and_list_worktrees(self, tmp_path):
        """Verify worktree registration persists and is retrievable."""
        import supervisor.global_registry as reg

        orig_env = os.environ.get("THIN_SUPERVISOR_GLOBAL_DIR")
        os.environ["THIN_SUPERVISOR_GLOBAL_DIR"] = str(tmp_path)

        try:
            reg.register_worktree("/tmp/wt1")
            reg.register_worktree("/tmp/wt2")
            reg.register_worktree("/tmp/wt1")  # duplicate — should not add twice
            wts = reg.list_known_worktrees()
            assert len(wts) == 2
            assert any("wt1" in w for w in wts)
            assert any("wt2" in w for w in wts)
        finally:
            if orig_env is not None:
                os.environ["THIN_SUPERVISOR_GLOBAL_DIR"] = orig_env
            else:
                os.environ.pop("THIN_SUPERVISOR_GLOBAL_DIR", None)


# ─────────────────────────────────────────────────────────────────
# Task 4: tui.collect_runs must be backed by the canonical session index
#
# Per docs/plans/2026-04-16-global-observability-plane-for-per-worktree-runtime.md:
#   - tui, dashboard, and status all see the same universe
#   - daemon shutdown does not hide persisted runs
#   - root cwd can drill into a child worktree orphaned run
# ─────────────────────────────────────────────────────────────────


class TestTuiCollectRunsParity:
    def _write_state(self, worktree, run_id, **fields):
        import json

        run_dir = worktree / ".supervisor" / "runtime" / "runs" / run_id
        run_dir.mkdir(parents=True)
        state = {
            "run_id": run_id,
            "top_state": fields.pop("top_state", "RUNNING"),
            "current_node_id": fields.pop("current_node_id", "step_x"),
            "pane_target": fields.pop("pane_target", "%0"),
            "controller_mode": fields.pop("controller_mode", "daemon"),
            "spec_path": fields.pop("spec_path", ""),
            "surface_type": "tmux",
        }
        state.update(fields)
        (run_dir / "state.json").write_text(json.dumps(state))

    def _patch_session_index(self, monkeypatch, *, known=(), daemons=(), panes=()):
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_known_worktrees",
            lambda: list(known),
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons",
            lambda: list(daemons),
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_pane_owners",
            lambda: list(panes),
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index._discover_git_worktrees",
            lambda cwd: [],
        )

    def test_collect_runs_matches_collect_sessions_universe(
        self, tmp_path, monkeypatch
    ):
        """tui.collect_runs() must cover the exact same run IDs as
        collect_sessions() — one canonical discovery source, one answer.
        """
        from supervisor.operator.session_index import collect_sessions
        from supervisor.operator.tui import collect_runs

        root = tmp_path / "root"
        child = tmp_path / "child"
        root.mkdir()
        child.mkdir()
        monkeypatch.chdir(root)

        self._write_state(root, "run_here", top_state="RUNNING")
        self._write_state(child, "run_there", top_state="PAUSED_FOR_HUMAN",
                          human_escalations=[{"reason": "test"}])

        self._patch_session_index(monkeypatch, known=[str(child)])

        tui_ids = {r["run_id"] for r in collect_runs()}
        sess_ids = {s.run_id for s in collect_sessions()}
        assert tui_ids == sess_ids
        assert {"run_here", "run_there"} <= tui_ids

    def test_collect_runs_surfaces_orphaned_child_worktree_from_root(
        self, tmp_path, monkeypatch
    ):
        """Root cwd must see the child worktree's orphaned run through tui."""
        from supervisor.operator.tui import collect_runs

        root = tmp_path / "root"
        child = tmp_path / "child"
        root.mkdir()
        child.mkdir()
        monkeypatch.chdir(root)

        self._write_state(child, "run_orphan", top_state="RUNNING")
        self._patch_session_index(monkeypatch, known=[str(child)])

        items = collect_runs()
        orphan = next((r for r in items if r["run_id"] == "run_orphan"), None)
        assert orphan is not None
        assert orphan["tag"] == "orphaned"
        assert str(child.resolve()) in orphan.get("worktree", "")

    def test_collect_runs_daemon_shutdown_keeps_persisted_run_visible(
        self, tmp_path, monkeypatch
    ):
        """Daemon process gone → persisted run must still surface in tui."""
        from supervisor.operator.tui import collect_runs

        wt = tmp_path / "wt"
        wt.mkdir()
        monkeypatch.chdir(wt)

        self._write_state(wt, "run_persist", top_state="PAUSED_FOR_HUMAN",
                          human_escalations=[{"reason": "test"}])
        # No daemon registered → persisted run becomes orphaned-but-visible
        self._patch_session_index(monkeypatch)

        items = collect_runs()
        persist = next((r for r in items if r["run_id"] == "run_persist"), None)
        assert persist is not None
        assert persist["tag"] in {"paused", "orphaned"}


# ── Task 6: global-mode TUI formatters ─────────────────────────────

class TestGlobalModeFormatters:
    """Task 6: the TUI global mode renders a SystemSnapshot through the
    same projection layer as the overview CLI.  The formatters are pure
    (input → list[str]) so they can be unit-tested without curses."""

    def _snapshot(self, **overrides):
        from supervisor.operator.models import (
            SystemAlert,
            SystemCounts,
            SystemSnapshot,
            SystemTimelineEvent,
        )
        from supervisor.operator.session_index import SessionRecord

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
        alerts = [
            SystemAlert(kind="paused_for_human", count=1,
                        summary="1 run paused"),
            SystemAlert(kind="mailbox_backlog", count=1,
                        summary="1 mailbox item"),
        ]
        timeline = [
            SystemTimelineEvent(
                event_type="state_transition",
                occurred_at="2026-04-18T12:00:00+00:00",
                scope="session", session_id="s1", run_id="r1",
                summary="RUNNING → PAUSED_FOR_HUMAN",
                payload={},
            ),
            SystemTimelineEvent(
                event_type="daemon_started",
                occurred_at="2026-04-18T11:00:00+00:00",
                scope="system", session_id="", run_id="",
                summary="daemon started (pid 42)",
                payload={},
            ),
        ]
        sessions = [
            SessionRecord(
                run_id="run_paused", worktree_root="/w",
                spec_path="s.yaml", controller_mode="daemon",
                top_state="PAUSED_FOR_HUMAN", current_node="n",
                pane_target="%1", daemon_socket="/tmp/d",
                is_live=True, is_orphaned=False, is_completed=False,
                pause_reason="needs review", next_action="",
                last_checkpoint_summary="",
                last_update_at="2026-04-18T09:00:00Z",
                surface_type="tmux", tag="paused", pause_class="",
                session_id="sess_paused",
                event_plane={"waits_open": 0, "mailbox_new": 1,
                             "mailbox_acknowledged": 0, "requests_total": 1,
                             "latest_mailbox_item_id": "mb_1",
                             "latest_wake_decision": "notify_operator"},
            ),
            SessionRecord(
                run_id="run_done", worktree_root="/w",
                spec_path="s.yaml", controller_mode="daemon",
                top_state="COMPLETED", current_node="n",
                pane_target="%2", daemon_socket="",
                is_live=False, is_orphaned=False, is_completed=True,
                pause_reason="", next_action="",
                last_checkpoint_summary="",
                last_update_at="2026-04-18T08:00:00Z",
                surface_type="tmux", tag="completed", pause_class="",
            ),
        ]
        snap = SystemSnapshot(
            counts=counts, alerts=alerts,
            recent_timeline=timeline, sessions=sessions,
        )
        return snap

    def test_banner_shows_counts(self):
        snap = self._snapshot()
        lines = format_system_banner(snap)
        text = "\n".join(lines)
        assert "daemons=2" in text
        assert "live=3" in text
        assert "orphaned=1" in text
        assert "mailbox_new=1" in text

    def test_banner_handles_zero_counts(self):
        from supervisor.operator.models import (
            SystemCounts, SystemSnapshot,
        )
        empty = SystemSnapshot(
            counts=SystemCounts(
                daemons=0, foreground_runs=0, live_sessions=0,
                orphaned_sessions=0, completed_sessions=0,
                waits_open=0, mailbox_new=0, mailbox_acknowledged=0,
            ),
            alerts=[], recent_timeline=[], sessions=[],
        )
        lines = format_system_banner(empty)
        assert any("daemons=0" in l for l in lines)

    def test_alerts_panel_lists_each_alert(self):
        snap = self._snapshot()
        lines = format_system_alerts(snap.alerts)
        text = "\n".join(lines)
        assert "paused_for_human" in text
        assert "1 run paused" in text
        assert "mailbox_backlog" in text

    def test_alerts_panel_when_quiet(self):
        lines = format_system_alerts([])
        text = "\n".join(lines)
        assert "No alerts" in text or "(none)" in text

    def test_system_timeline_renders_scope_and_summary(self):
        snap = self._snapshot()
        lines = format_system_timeline(snap.recent_timeline)
        text = "\n".join(lines)
        assert "PAUSED_FOR_HUMAN" in text
        assert "daemon started" in text

    def test_actionable_sessions_puts_paused_first(self):
        snap = self._snapshot()
        lines = format_actionable_sessions(snap)
        text = "\n".join(lines)
        # The paused session with a mailbox backlog must be visible, and
        # the completed one must not (it's not actionable).
        assert "run_paused" in text
        assert "run_done" not in text
        assert "mailbox:1" in text or "mailbox=1" in text
