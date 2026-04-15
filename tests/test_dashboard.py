"""Tests for status bucketing and interactive dashboard."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from supervisor import app


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


def test_status_buckets_daemon_runs(monkeypatch, capsys):
    """cmd_status shows daemon runs under 'Active runs:' section."""
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithRuns)
    monkeypatch.setattr(app, "_find_local_run_summaries", lambda: [])

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
    assert "480s" in out


def test_dashboard_shows_numbered_list(tmp_path, monkeypatch, capsys):
    """cmd_dashboard prints numbered run list."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithRuns)
    monkeypatch.setattr(app, "_find_local_run_summaries", lambda: [])
    monkeypatch.setattr(app, "_list_global_daemons", lambda: [])

    # Simulate user pressing 'q' immediately
    monkeypatch.setattr("builtins.input", lambda prompt: "q")

    result = app.cmd_dashboard(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "1." in out
    assert "run_d1" in out
    assert "[daemon]" in out
    assert "inspect" in out
