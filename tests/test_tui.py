"""Tests for TUI formatting logic (non-interactive parts)."""

from pathlib import Path
from unittest.mock import patch

from supervisor.operator.tui import (
    format_run_line,
    format_snapshot,
    format_timeline,
    format_explanation,
    format_exchange,
    collect_runs,
    _resolve_run_dir,
    _local_explain_run,
    _local_assess_drift,
    _local_explain_exchange,
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
    @patch("supervisor.operator.tui.list_pane_owners", return_value=[])
    @patch("supervisor.operator.tui.list_daemons", return_value=[])
    def test_collect_with_disk_runs(self, _mock_daemons, _mock_owners, tmp_path):
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

    @patch("supervisor.operator.tui.list_pane_owners", return_value=[])
    @patch("supervisor.operator.tui.list_daemons", return_value=[])
    def test_collect_completed_run(self, _mock_daemons, _mock_owners, tmp_path):
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


class TestResolveRunDir:
    def test_with_worktree(self):
        run = {"run_id": "run_abc", "worktree": "/tmp/other-wt"}
        d = _resolve_run_dir(run)
        assert d == Path("/tmp/other-wt/.supervisor/runtime/runs/run_abc")

    def test_without_worktree(self):
        run = {"run_id": "run_abc", "worktree": ""}
        d = _resolve_run_dir(run)
        assert "runs/run_abc" in str(d)

    def test_no_worktree_key(self):
        run = {"run_id": "run_abc"}
        d = _resolve_run_dir(run)
        assert "runs/run_abc" in str(d)


class TestLocalExplainFallback:
    def test_explain_run_from_disk(self, tmp_path):
        import json
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_fb1"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_fb1",
            "top_state": "RUNNING",
            "current_node_id": "step_1",
            "done_node_ids": [],
            "last_agent_checkpoint": {"summary": "working"},
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        run = {"run_id": "run_fb1", "worktree": str(tmp_path)}
        lines = _local_explain_run(run)
        text = "\n".join(lines)
        assert "RUNNING" in text
        assert "step_1" in text

    def test_explain_run_zh(self, tmp_path):
        import json
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_fb2"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_fb2",
            "top_state": "RUNNING",
            "current_node_id": "step_1",
            "done_node_ids": [],
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        run = {"run_id": "run_fb2", "worktree": str(tmp_path)}
        lines = _local_explain_run(run, language="zh")
        text = "\n".join(lines)
        assert "状态" in text or "节点" in text

    def test_drift_from_disk(self, tmp_path):
        import json
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_fb3"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_fb3",
            "top_state": "RUNNING",
            "current_node_id": "step_1",
            "retry_budget": {"used_global": 5},
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        run = {"run_id": "run_fb3", "worktree": str(tmp_path)}
        lines = _local_assess_drift(run)
        text = "\n".join(lines)
        # High retry count should trigger a warning
        assert "retry" in text.lower() or "watch" in text or "drifting" in text

    def test_exchange_from_disk(self, tmp_path):
        import json
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_fb4"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_fb4",
            "top_state": "RUNNING",
            "current_node_id": "step_1",
            "last_agent_checkpoint": {"summary": "wrote tests"},
            "last_decision": {"next_instruction": "continue"},
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        run = {"run_id": "run_fb4", "worktree": str(tmp_path)}
        lines = _local_explain_exchange(run)
        text = "\n".join(lines)
        assert "Explanation:" in text

    def test_missing_state(self):
        run = {"run_id": "run_ghost", "worktree": "/nonexistent"}
        lines = _local_explain_run(run)
        assert any("no local state" in l for l in lines)

    def test_load_local_detail_cross_worktree(self, tmp_path):
        """Verify _load_local_detail reads from worktree, not cwd."""
        import json
        import supervisor.operator.tui as tui_mod
        from supervisor.operator.tui import _load_local_detail

        # Point _RUNTIME_DIR to an empty dir (simulates wrong cwd)
        orig = tui_mod._RUNTIME_DIR
        tui_mod._RUNTIME_DIR = tmp_path / "empty_rt"

        # Create state in a different "worktree"
        other_wt = tmp_path / "other_worktree"
        run_dir = other_wt / ".supervisor" / "runtime" / "runs" / "run_xwt"
        run_dir.mkdir(parents=True)
        state = {
            "run_id": "run_xwt",
            "top_state": "PAUSED_FOR_HUMAN",
            "current_node_id": "step_2",
            "spec_id": "my-spec",
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        try:
            run = {"run_id": "run_xwt", "worktree": str(other_wt)}
            lines = _load_local_detail(run)
            text = "\n".join(lines)
            assert "PAUSED_FOR_HUMAN" in text
            assert "run_xwt" in text
        finally:
            tui_mod._RUNTIME_DIR = orig
