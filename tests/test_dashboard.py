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
    """cmd_dashboard prints numbered run list from the canonical session index."""
    wt = tmp_path / "project-a"
    wt.mkdir()
    monkeypatch.chdir(tmp_path)

    # Write a run in a tracked worktree and register a matching daemon
    _write_state_in(wt, "run_d1", top_state="RUNNING",
                    current_node_id="step_1", pane_target="%3",
                    controller_mode="daemon")
    monkeypatch.setattr(app, "_list_global_daemons", lambda: [
        {"pid": 999, "cwd": str(wt.resolve()), "socket": "/tmp/test.sock",
         "active_runs": 1, "state": "active"},
    ])
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_daemons",
        lambda: [{
            "pid": 999,
            "cwd": str(wt.resolve()),
            "socket": "/tmp/test.sock",
            "active_runs": 1,
        }],
    )

    # Simulate user pressing 'q' immediately
    monkeypatch.setattr("builtins.input", lambda prompt: "q")

    result = app.cmd_dashboard(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "1." in out
    assert "run_d1" in out
    assert "[daemon]" in out
    assert "inspect" in out
    assert str(wt.resolve()) in out  # worktree shown


# ─────────────────────────────────────────────────────────────────
# Task 4: dashboard parity with session_index
#
# Same run universe as status / tui.collect_runs. Root cwd drills into
# child worktree runs. Daemon shutdown does not hide persisted runs.
# ─────────────────────────────────────────────────────────────────


def _write_state_in(worktree, run_id, *, top_state="RUNNING", **fields):
    run_dir = worktree / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True)
    state = {
        "run_id": run_id,
        "top_state": top_state,
        "current_node_id": fields.pop("current_node_id", "step_x"),
        "pane_target": fields.pop("pane_target", "%0"),
        "controller_mode": fields.pop("controller_mode", "daemon"),
        "spec_path": fields.pop("spec_path", ""),
        "surface_type": "tmux",
    }
    state.update(fields)
    (run_dir / "state.json").write_text(json.dumps(state))


def test_dashboard_shows_orphaned_run_from_child_worktree(
    tmp_path, monkeypatch, capsys,
):
    """Dashboard must surface a child worktree's orphaned run from root cwd."""
    root = tmp_path / "root"
    child = tmp_path / "child"
    root.mkdir()
    child.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(app, "_list_global_daemons", lambda: [])

    _write_state_in(child, "run_child_orphan", top_state="PAUSED_FOR_HUMAN",
                    human_escalations=[{"reason": "test"}])
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_known_worktrees",
        lambda: [str(child)],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "q")

    result = app.cmd_dashboard(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "run_child_orphan" in out
    assert str(child.resolve()) in out


def test_status_dashboard_tui_see_same_universe(tmp_path, monkeypatch, capsys):
    """status, dashboard, and tui.collect_runs must agree on the run set.

    This is the core parity contract of the global observability plane.
    """
    from supervisor.operator.tui import collect_runs

    root = tmp_path / "root"
    child = tmp_path / "child"
    root.mkdir()
    child.mkdir()
    monkeypatch.chdir(root)

    # Three runs in two worktrees
    _write_state_in(root, "run_root_running", top_state="RUNNING")
    _write_state_in(child, "run_child_paused", top_state="PAUSED_FOR_HUMAN",
                    human_escalations=[{"reason": "test"}])
    _write_state_in(child, "run_child_done", top_state="COMPLETED")

    monkeypatch.setattr(
        "supervisor.operator.session_index.list_known_worktrees",
        lambda: [str(child)],
    )
    monkeypatch.setattr(app, "_list_global_daemons", lambda: [])
    monkeypatch.setattr("builtins.input", lambda prompt: "q")

    # TUI
    tui_ids = {r["run_id"] for r in collect_runs()}

    # status
    monkeypatch.setattr(
        "supervisor.daemon.client.DaemonClient", _DaemonStopped
    )
    app.cmd_status(argparse.Namespace(config=None, local=False))
    status_out = capsys.readouterr().out

    # dashboard
    app.cmd_dashboard(argparse.Namespace())
    dash_out = capsys.readouterr().out

    expected = {"run_root_running", "run_child_paused", "run_child_done"}
    assert expected <= tui_ids
    for rid in expected:
        assert rid in status_out, f"status missing {rid}: {status_out}"
        assert rid in dash_out, f"dashboard missing {rid}: {dash_out}"
