"""Tests for status bucketing and interactive dashboard."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from supervisor import app


@pytest.fixture(autouse=True)
def _hermetic_session_index(monkeypatch):
    """Isolate status tests from real global registries (see test_app_cli.py)."""
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_known_worktrees", lambda: []
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_daemons", lambda: []
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_pane_owners", lambda: []
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index._discover_git_worktrees",
        lambda cwd: [],
    )


class _DaemonWithRuns:
    def is_running(self):
        return True

    def status(self):
        return {
            "ok": True,
            "runs": [
                {
                    "run_id": "run_d1",
                    "pane_target": "%3",
                    "top_state": "RUNNING",
                    "current_node": "step_1",
                    "status_reason": "working step_1",
                },
            ],
        }


class _DaemonStopped:
    def is_running(self):
        return False


def test_status_buckets_daemon_runs(tmp_path, monkeypatch, capsys):
    """cmd_status shows daemon runs under 'Active runs:' section.

    Daemon liveness now flows through `list_daemons()` + session_index,
    not a direct DaemonClient.status() call.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)

    run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_d1"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": "run_d1",
        "top_state": "RUNNING",
        "current_node_id": "step_1",
        "pane_target": "%3",
        "controller_mode": "daemon",
    }))
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_daemons",
        lambda: [{
            "pid": 1,
            "cwd": str(tmp_path.resolve()),
            "socket": "/tmp/a.sock",
            "active_runs": 1,
        }],
    )

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "Active runs:" in out
    assert "[daemon]" in out
    assert "run_d1" in out


def test_status_buckets_orphaned_state(tmp_path, monkeypatch, capsys):
    """cmd_status shows orphaned state under 'Orphaned local state:' section."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)

    # Create an orphaned daemon-owned run
    run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_orphan"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": "run_orphan",
        "spec_id": "test",
        "mode": "strict_verifier",
        "top_state": "RUNNING",
        "current_node_id": "step_2",
        "controller_mode": "daemon",
    }))

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "Orphaned" in out
    assert "[orphaned]" in out
    assert "run_orphan" in out


def test_ps_shows_idle_state(monkeypatch, capsys):
    """cmd_ps shows daemon STATE and IDLE columns."""
    monkeypatch.setattr(app, "_list_global_daemons", lambda: [
        {
            "pid": 111,
            "cwd": "/tmp/project-a",
            "socket": "/tmp/a.sock",
            "active_runs": 2,
            "state": "active",
            "idle_for_sec": 0,
        },
        {
            "pid": 222,
            "cwd": "/tmp/project-b",
            "socket": "/tmp/b.sock",
            "active_runs": 0,
            "state": "idle",
            "idle_for_sec": 480,
        },
    ], raising=False)

    result = app.cmd_ps(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "STATE" in out
    assert "active" in out
    assert "idle" in out
    assert "8m" in out


def test_status_alive_foreground_not_orphaned(tmp_path, monkeypatch, capsys):
    """Active foreground run registered as pane owner is foreground, not orphaned."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)

    run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_fg_alive"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": "run_fg_alive",
        "spec_id": "test",
        "mode": "strict_verifier",
        "top_state": "RUNNING",
        "current_node_id": "step_1",
        "controller_mode": "foreground",
    }))
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_pane_owners",
        lambda: [{
            "pid": 1,
            "cwd": str(tmp_path.resolve()),
            "run_id": "run_fg_alive",
            "pane_target": "%1",
            "controller_mode": "foreground",
        }],
    )

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "Debug foreground" in out
    assert "[foreground]" in out
    assert "run_fg_alive" in out
    assert "[orphaned]" not in out


def test_status_dead_foreground_is_orphaned(tmp_path, monkeypatch, capsys):
    """Dead foreground run (PID gone) is shown as orphaned."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)

    run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / "run_fg_dead"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": "run_fg_dead",
        "spec_id": "test",
        "mode": "strict_verifier",
        "top_state": "RUNNING",
        "current_node_id": "step_1",
        "controller_mode": "foreground",
        "_foreground_pid": 999999,  # dead PID
    }))

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "Orphaned" in out
    assert "[orphaned]" in out


def test_dashboard_shows_numbered_list(tmp_path, monkeypatch, capsys):
    """cmd_dashboard prints numbered run list from cross-worktree scan."""
    monkeypatch.chdir(tmp_path)

    # Provide a daemon with a socket that our mock client will connect to
    monkeypatch.setattr(app, "_list_global_daemons", lambda: [
        {"pid": 999, "cwd": "/tmp/project-a", "socket": "/tmp/test.sock", "active_runs": 1, "state": "active"},
    ])

    # Mock DaemonClient to return runs when connected to any socket
    mock_client = MagicMock()
    mock_client.is_running.return_value = True
    mock_client.status.return_value = {
        "ok": True,
        "runs": [
            {"run_id": "run_d1", "pane_target": "%3", "top_state": "RUNNING", "current_node": "step_1"},
        ],
    }
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", lambda sock_path="": mock_client)
    monkeypatch.setattr(app, "list_pane_owners", lambda: [])
    monkeypatch.setattr(app, "_find_local_run_summaries", lambda: [])

    # Simulate user pressing 'q' immediately
    monkeypatch.setattr("builtins.input", lambda prompt: "q")

    result = app.cmd_dashboard(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "1." in out
    assert "run_d1" in out
    assert "[daemon]" in out
    assert "inspect" in out
    assert "/tmp/project-a" in out  # worktree shown
