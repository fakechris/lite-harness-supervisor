"""Tests for TUI formatting logic (non-interactive parts)."""

from supervisor.operator.tui import (
    format_run_line,
    format_snapshot,
    format_timeline,
    format_explanation,
    format_exchange,
    collect_runs,
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


class TestCollectRunsLocal:
    def test_collect_with_disk_runs(self, tmp_path):
        """Verify collect_runs picks up on-disk state files."""
        import json
        import supervisor.operator.tui as tui_mod

        # Temporarily override runtime dir
        orig = tui_mod._RUNTIME_DIR
        tui_mod._RUNTIME_DIR = tmp_path

        runs_dir = tmp_path / "runs" / "run_test123"
        runs_dir.mkdir(parents=True)
        state = {
            "run_id": "run_test123",
            "top_state": "PAUSED_FOR_HUMAN",
            "current_node_id": "step_1",
            "pane_target": "%5",
            "workspace_root": "/tmp/ws",
        }
        (runs_dir / "state.json").write_text(json.dumps(state))

        try:
            items = collect_runs(daemons=[])
            assert len(items) == 1
            assert items[0]["run_id"] == "run_test123"
            assert items[0]["tag"] == "paused"
            assert items[0]["top_state"] == "PAUSED_FOR_HUMAN"
        finally:
            tui_mod._RUNTIME_DIR = orig

    def test_collect_completed_run(self, tmp_path):
        import json
        import supervisor.operator.tui as tui_mod

        orig = tui_mod._RUNTIME_DIR
        tui_mod._RUNTIME_DIR = tmp_path

        runs_dir = tmp_path / "runs" / "run_done999"
        runs_dir.mkdir(parents=True)
        state = {
            "run_id": "run_done999",
            "top_state": "COMPLETED",
            "current_node_id": "",
            "done_node_ids": ["step_1", "step_2"],
        }
        (runs_dir / "state.json").write_text(json.dumps(state))

        try:
            items = collect_runs(daemons=[])
            assert len(items) == 1
            assert items[0]["tag"] == "completed"
        finally:
            tui_mod._RUNTIME_DIR = orig
