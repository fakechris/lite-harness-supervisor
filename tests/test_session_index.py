"""Canonical session collector — contract tests.

Per docs/plans/2026-04-16-global-observability-plane-for-per-worktree-runtime.md
Task 1: the collector must produce a normalized SessionRecord for every
known session across every known worktree, regardless of whether a
daemon is currently alive.

Scenarios covered (from plan Task 1 Step 1):
  1. cwd root + child worktree orphaned run
  2. live daemon run in another worktree
  3. live foreground run in another worktree
  4. completed run in another worktree
  + local_only filter, dedup across discovery sources, and find_session().
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.operator.session_index import (
    SessionRecord,
    collect_sessions,
    find_session,
)


# ── Fixtures ────────────────────────────────────────────────────


def _write_state(
    worktree: Path,
    run_id: str,
    *,
    top_state: str = "RUNNING",
    current_node: str = "step_1",
    pane_target: str = "0:0.0",
    controller_mode: str = "daemon",
    spec_path: str = "",
    human_escalations=None,
    delivery_state: str = "IDLE",
) -> Path:
    """Write a minimal state.json under worktree/.supervisor/runtime/runs/{run_id}/."""
    run_dir = worktree / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id,
        "spec_id": "phase_x",
        "top_state": top_state,
        "current_node_id": current_node,
        "pane_target": pane_target,
        "spec_path": spec_path or str(worktree / "spec.yaml"),
        "workspace_root": str(worktree),
        "controller_mode": controller_mode,
        "human_escalations": human_escalations or [],
        "delivery_state": delivery_state,
        "surface_type": "tmux",
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


@pytest.fixture
def fake_worktrees(tmp_path, monkeypatch):
    """Create root + child worktree dirs and empty discovery registries.

    Tests override per-scenario. `git worktree list` is stubbed out so
    tests stay hermetic.
    """
    root = tmp_path / "root"
    child = tmp_path / "child"
    root.mkdir()
    child.mkdir()
    monkeypatch.chdir(root)

    monkeypatch.setattr(
        "supervisor.operator.session_index.list_daemons", lambda: []
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_pane_owners", lambda: []
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_known_worktrees",
        lambda: [str(child)],
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index._discover_git_worktrees",
        lambda cwd: [],
    )
    return root, child


# ── Core collector scenarios ────────────────────────────────────


class TestCollectSessions:
    def test_orphaned_run_in_child_worktree_visible_from_root(
        self, fake_worktrees
    ):
        """Plan incident shape: run persisted in child worktree, no live
        daemon, cwd is root. Must still surface the run with is_orphaned.
        """
        root, child = fake_worktrees
        _write_state(child, "run_abc", top_state="RUNNING")
        records = collect_sessions()
        assert len(records) == 1
        rec = records[0]
        assert isinstance(rec, SessionRecord)
        assert rec.run_id == "run_abc"
        assert Path(rec.worktree_root) == child.resolve()
        assert rec.top_state == "RUNNING"
        assert rec.is_orphaned is True
        assert rec.is_live is False
        assert rec.is_completed is False

    def test_live_daemon_run_in_another_worktree(
        self, fake_worktrees, monkeypatch
    ):
        root, child = fake_worktrees
        _write_state(
            child, "run_daemon", top_state="RUNNING", controller_mode="daemon"
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons",
            lambda: [
                {
                    "pid": 1,
                    "cwd": str(child.resolve()),
                    "socket": "/tmp/x.sock",
                    "active_runs": 1,
                }
            ],
        )
        records = collect_sessions()
        assert len(records) == 1
        rec = records[0]
        assert rec.run_id == "run_daemon"
        assert rec.controller_mode == "daemon"
        assert rec.is_live is True
        assert rec.is_orphaned is False
        assert rec.daemon_socket == "/tmp/x.sock"

    def test_live_foreground_run_in_another_worktree(
        self, fake_worktrees, monkeypatch
    ):
        root, child = fake_worktrees
        _write_state(
            child,
            "run_fg",
            top_state="RUNNING",
            controller_mode="foreground",
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_pane_owners",
            lambda: [
                {
                    "pid": 1,
                    "cwd": str(child.resolve()),
                    "run_id": "run_fg",
                    "pane_target": "0:0.0",
                    "controller_mode": "foreground",
                }
            ],
        )
        records = collect_sessions()
        assert len(records) == 1
        rec = records[0]
        assert rec.run_id == "run_fg"
        assert rec.controller_mode == "foreground"
        assert rec.is_live is True
        assert rec.is_orphaned is False

    def test_completed_run_in_another_worktree(self, fake_worktrees):
        root, child = fake_worktrees
        _write_state(child, "run_done", top_state="COMPLETED")
        records = collect_sessions()
        assert len(records) == 1
        rec = records[0]
        assert rec.run_id == "run_done"
        assert rec.is_completed is True
        assert rec.is_orphaned is False
        assert rec.is_live is False


# ── local_only filter ───────────────────────────────────────────


class TestLocalOnlyFilter:
    def test_local_only_excludes_other_worktrees(self, fake_worktrees):
        root, child = fake_worktrees
        _write_state(root, "run_here", top_state="RUNNING")
        _write_state(child, "run_there", top_state="RUNNING")
        records = collect_sessions(local_only=True)
        assert {r.run_id for r in records} == {"run_here"}

    def test_global_includes_both(self, fake_worktrees):
        root, child = fake_worktrees
        _write_state(root, "run_here", top_state="RUNNING")
        _write_state(child, "run_there", top_state="RUNNING")
        records = collect_sessions()
        assert {r.run_id for r in records} == {"run_here", "run_there"}


# ── Pause reason + next action propagation ──────────────────────


class TestPausedSessionFields:
    def test_paused_carries_reason_and_next_action(self, fake_worktrees):
        root, child = fake_worktrees
        _write_state(
            child,
            "run_paused",
            top_state="PAUSED_FOR_HUMAN",
            human_escalations=[{"reason": "need review"}],
            spec_path=str(child / "spec.yaml"),
            pane_target="0:0.0",
        )
        records = collect_sessions()
        assert len(records) == 1
        rec = records[0]
        assert rec.top_state == "PAUSED_FOR_HUMAN"
        assert rec.pause_reason == "need review"
        assert "resume" in rec.next_action


# ── Dedup across discovery sources ──────────────────────────────


class TestDedup:
    def test_same_run_counted_once_across_sources(
        self, fake_worktrees, monkeypatch
    ):
        """Child worktree appears via known_worktrees AND daemon.cwd.

        The collector must union the discovery sources but dedup by
        run_id so the same state.json is not emitted twice.
        """
        root, child = fake_worktrees
        _write_state(child, "run_dup", top_state="RUNNING")
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons",
            lambda: [
                {
                    "pid": 1,
                    "cwd": str(child),
                    "socket": "/tmp/s.sock",
                    "active_runs": 1,
                }
            ],
        )
        records = collect_sessions()
        assert len(records) == 1
        assert records[0].run_id == "run_dup"


# ── find_session ────────────────────────────────────────────────


class TestFindSession:
    def test_returns_matching_record(self, fake_worktrees):
        root, child = fake_worktrees
        _write_state(child, "run_find_me", top_state="RUNNING")
        rec = find_session("run_find_me")
        assert rec is not None
        assert rec.run_id == "run_find_me"

    def test_returns_none_for_missing_id(self, fake_worktrees):
        assert find_session("nonexistent") is None


# ── Worktree ownership is explicit ──────────────────────────────


class TestWorktreeOwnership:
    def test_every_record_carries_resolved_worktree_root(self, fake_worktrees):
        """Rule 5: every session view must carry worktree_root explicitly."""
        root, child = fake_worktrees
        _write_state(root, "run_root", top_state="RUNNING")
        _write_state(child, "run_child", top_state="RUNNING")
        records = collect_sessions()
        by_id = {r.run_id: r for r in records}
        assert Path(by_id["run_root"].worktree_root) == root.resolve()
        assert Path(by_id["run_child"].worktree_root) == child.resolve()
