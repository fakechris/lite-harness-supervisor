"""Tests for RunContext, RunCapabilities, and the capability matrix."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from supervisor.operator.run_context import (
    ActionMode,
    ActionUnavailable,
    RunCapabilities,
    RunContext,
    _compute_capabilities,
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


# ── RunContext.from_run_dict ─────────────────────────────────────


class TestFromRunDict:
    def test_basic_fields(self):
        ctx = RunContext.from_run_dict(_make_run())
        assert ctx.run_id == "run_abc123def456"
        assert ctx.worktree == "/tmp/ws"
        assert ctx.tag == "daemon"
        assert ctx.top_state == "RUNNING"
        assert ctx.pane_target == "%5"
        assert ctx.socket == "/tmp/sock"

    def test_paths_resolved_from_worktree(self):
        ctx = RunContext.from_run_dict(_make_run(worktree="/opt/project"))
        assert ctx.state_dir == Path("/opt/project/.supervisor/runtime/runs/run_abc123def456")
        assert ctx.state_path == ctx.state_dir / "state.json"
        assert ctx.session_log_path == ctx.state_dir / "session_log.jsonl"
        assert ctx.config_path == "/opt/project/.supervisor/config.yaml"

    def test_empty_worktree_uses_cwd(self):
        ctx = RunContext.from_run_dict(_make_run(worktree=""))
        assert ctx.state_dir == Path(".") / ".supervisor" / "runtime" / "runs" / "run_abc123def456"

    def test_spec_path_loaded_from_state(self, tmp_path):
        """spec_path and pane_target are read from state.json when available."""
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_test"
        run_dir.mkdir(parents=True)
        state = {"run_id": "run_test", "spec_path": "/specs/a.yaml", "pane_target": "%9"}
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = RunContext.from_run_dict({
            "run_id": "run_test",
            "tag": "paused",
            "top_state": "PAUSED_FOR_HUMAN",
            "pane_target": "?",
            "worktree": str(tmp_path),
            "socket": "",
        })
        assert ctx.spec_path == "/specs/a.yaml"
        assert ctx.pane_target == "%9"

    def test_pane_target_not_overridden_if_valid(self, tmp_path):
        """If run dict already has a valid pane_target, don't overwrite from state."""
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_test"
        run_dir.mkdir(parents=True)
        state = {"run_id": "run_test", "pane_target": "%9"}
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = RunContext.from_run_dict({
            "run_id": "run_test",
            "tag": "daemon",
            "top_state": "RUNNING",
            "pane_target": "%5",
            "worktree": str(tmp_path),
            "socket": "/tmp/s",
        })
        assert ctx.pane_target == "%5"


class TestLoadState:
    def test_reads_state_json(self, tmp_path):
        run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_x"
        run_dir.mkdir(parents=True)
        state = {"run_id": "run_x", "top_state": "RUNNING"}
        (run_dir / "state.json").write_text(json.dumps(state))

        ctx = RunContext.from_run_dict({
            "run_id": "run_x", "tag": "daemon", "top_state": "RUNNING",
            "pane_target": "%1", "worktree": str(tmp_path), "socket": "",
        })
        loaded = ctx.load_state()
        assert loaded["run_id"] == "run_x"

    def test_missing_state_returns_empty(self, tmp_path):
        ctx = RunContext.from_run_dict({
            "run_id": "run_missing", "tag": "local", "top_state": "UNKNOWN",
            "pane_target": "", "worktree": str(tmp_path), "socket": "",
        })
        assert ctx.load_state() == {}


class TestLoadConfig:
    @patch("supervisor.config.RuntimeConfig.load")
    def test_passes_config_path(self, mock_load, tmp_path):
        cfg_path = tmp_path / ".supervisor" / "config.yaml"
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text("idle_timeout_sec: 999\n")

        ctx = RunContext.from_run_dict({
            "run_id": "run_c", "tag": "daemon", "top_state": "RUNNING",
            "pane_target": "%1", "worktree": str(tmp_path), "socket": "",
        })
        ctx.load_config()
        mock_load.assert_called_once_with(str(cfg_path))

    @patch("supervisor.config.RuntimeConfig.load")
    def test_no_config_file_passes_none(self, mock_load, tmp_path):
        ctx = RunContext.from_run_dict({
            "run_id": "run_c", "tag": "daemon", "top_state": "RUNNING",
            "pane_target": "%1", "worktree": str(tmp_path), "socket": "",
        })
        ctx.load_config()
        mock_load.assert_called_once_with(None)


class TestGetClient:
    def test_returns_client_for_socket(self):
        ctx = RunContext.from_run_dict(_make_run(socket="/tmp/test.sock"))
        client = ctx.get_client()
        assert client is not None
        assert client.sock_path == "/tmp/test.sock"

    @patch("supervisor.global_registry.list_daemons")
    def test_worktree_match_fallback(self, mock_daemons):
        """When socket is empty, finds daemon by worktree match."""
        mock_client = MagicMock()
        mock_client.is_running.return_value = True
        mock_daemons.return_value = [
            {"cwd": "/tmp/ws", "socket": "/tmp/found.sock"},
        ]
        with patch("supervisor.daemon.client.DaemonClient", return_value=mock_client) as MockDC:
            ctx = RunContext.from_run_dict(_make_run(socket="", worktree="/tmp/ws"))
            client = ctx.get_client()
            assert client is not None
            MockDC.assert_called_with(sock_path="/tmp/found.sock")

    @patch("supervisor.global_registry.list_daemons", return_value=[])
    def test_returns_none_when_no_daemon(self, _):
        ctx = RunContext.from_run_dict(_make_run(socket="", worktree="/tmp/ws"))
        assert ctx.get_client() is None


# ── Capability Matrix ────────────────────────────────────────────


class TestCapabilityMatrix:
    """Scenario-based tests for the capability matrix."""

    def test_daemon_active(self):
        caps = _compute_capabilities("daemon", "RUNNING", True)
        assert caps.inspect == ActionMode.SYNC_DAEMON
        assert caps.exchange == ActionMode.SYNC_DAEMON
        assert caps.explain == ActionMode.ASYNC_DAEMON
        assert caps.drift == ActionMode.ASYNC_DAEMON
        assert caps.pause == ActionMode.SYNC_DAEMON
        assert caps.resume == ActionMode.SYNC_DAEMON
        assert caps.note_add == ActionMode.SYNC_DAEMON
        assert caps.note_list == ActionMode.SYNC_DAEMON
        assert caps.unavailable_reasons == {}

    def test_foreground(self):
        caps = _compute_capabilities("foreground", "RUNNING", False)
        assert caps.inspect == ActionMode.SYNC_LOCAL
        assert caps.exchange == ActionMode.SYNC_LOCAL
        assert caps.explain == ActionMode.ASYNC_LOCAL
        assert caps.drift == ActionMode.ASYNC_LOCAL
        assert caps.pause == ActionMode.UNAVAILABLE
        assert caps.resume == ActionMode.UNAVAILABLE
        assert "foreground" in caps.unavailable_reasons["pause"]
        assert "foreground" in caps.unavailable_reasons["resume"]

    def test_orphaned_with_daemon(self):
        caps = _compute_capabilities("orphaned", "RUNNING", True)
        assert caps.inspect == ActionMode.SYNC_DAEMON
        assert caps.resume == ActionMode.SYNC_DAEMON
        assert caps.pause == ActionMode.SYNC_DAEMON
        assert caps.note_add == ActionMode.SYNC_DAEMON

    def test_orphaned_no_daemon(self):
        caps = _compute_capabilities("orphaned", "RUNNING", False)
        assert caps.inspect == ActionMode.SYNC_LOCAL
        assert caps.resume == ActionMode.AUTO_START
        assert caps.pause == ActionMode.UNAVAILABLE
        assert caps.note_add == ActionMode.UNAVAILABLE
        assert "no daemon" in caps.unavailable_reasons["pause"]

    def test_paused_with_daemon(self):
        caps = _compute_capabilities("paused", "PAUSED_FOR_HUMAN", True)
        assert caps.pause == ActionMode.UNAVAILABLE
        assert caps.resume == ActionMode.SYNC_DAEMON
        assert "already paused" in caps.unavailable_reasons["pause"]
        assert caps.explain == ActionMode.ASYNC_DAEMON

    def test_paused_no_daemon(self):
        caps = _compute_capabilities("paused", "PAUSED_FOR_HUMAN", False)
        assert caps.resume == ActionMode.AUTO_START
        assert caps.pause == ActionMode.UNAVAILABLE
        assert caps.explain == ActionMode.ASYNC_LOCAL

    def test_completed(self):
        caps = _compute_capabilities("completed", "COMPLETED", False)
        assert caps.inspect == ActionMode.SYNC_LOCAL
        assert caps.explain == ActionMode.ASYNC_LOCAL
        assert caps.pause == ActionMode.UNAVAILABLE
        assert caps.resume == ActionMode.UNAVAILABLE
        assert "completed" in caps.unavailable_reasons["pause"]
        assert "completed" in caps.unavailable_reasons["resume"]

    def test_local_unknown(self):
        caps = _compute_capabilities("local", "UNKNOWN", False)
        assert caps.inspect == ActionMode.SYNC_LOCAL
        assert caps.resume == ActionMode.UNAVAILABLE
        assert caps.note_add == ActionMode.UNAVAILABLE

    def test_all_run_types_have_inspect_and_exchange(self):
        """Every run type must support inspect and exchange."""
        for tag in ("daemon", "foreground", "orphaned", "paused", "completed", "local"):
            for has_daemon in (True, False):
                caps = _compute_capabilities(tag, "RUNNING", has_daemon)
                assert caps.inspect != ActionMode.UNAVAILABLE, f"{tag}/{has_daemon}: inspect unavailable"
                assert caps.exchange != ActionMode.UNAVAILABLE, f"{tag}/{has_daemon}: exchange unavailable"

    def test_all_run_types_have_explain_and_drift(self):
        """Every run type must support explain and drift (async)."""
        for tag in ("daemon", "foreground", "orphaned", "paused", "completed", "local"):
            for has_daemon in (True, False):
                caps = _compute_capabilities(tag, "RUNNING", has_daemon)
                assert caps.explain != ActionMode.UNAVAILABLE, f"{tag}/{has_daemon}: explain unavailable"
                assert caps.drift != ActionMode.UNAVAILABLE, f"{tag}/{has_daemon}: drift unavailable"
