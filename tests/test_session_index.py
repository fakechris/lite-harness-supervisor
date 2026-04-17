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
import os
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

    def test_paused_daemon_owned_retains_daemon_socket(
        self, fake_worktrees, monkeypatch
    ):
        """Daemon-owned paused run keeps is_live and daemon_socket.

        Operator commands (inspect, resume) need the socket to reach the
        daemon. Losing the socket just because the top_state is
        PAUSED_FOR_HUMAN breaks remote control of exactly the most
        common command target.
        """
        root, child = fake_worktrees
        _write_state(child, "run_paused_live", top_state="PAUSED_FOR_HUMAN")
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons",
            lambda: [
                {
                    "pid": 1,
                    "cwd": str(child.resolve()),
                    "socket": "/tmp/paused.sock",
                    "active_runs": 1,
                }
            ],
        )
        records = collect_sessions()
        assert len(records) == 1
        rec = records[0]
        assert rec.is_live is True
        assert rec.is_orphaned is False
        assert rec.daemon_socket == "/tmp/paused.sock"
        # Tag should prioritise "paused" over "daemon" — paused needs
        # operator attention regardless of controller.
        assert rec.tag == "paused"

    def test_paused_without_daemon_surfaces_as_orphaned(self, fake_worktrees):
        """Paused runs without a live controller are actionable orphans.

        This is the plan's incident shape: child-worktree paused run,
        daemon idle-shutdown. Root cwd must see it as orphaned, not
        hidden, or the operator has no path to resume.
        """
        root, child = fake_worktrees
        _write_state(child, "run_paused_orphan", top_state="PAUSED_FOR_HUMAN")
        records = collect_sessions()
        assert len(records) == 1
        rec = records[0]
        assert rec.is_live is False
        assert rec.is_orphaned is True
        assert rec.is_completed is False


class TestGlobalRecencySort:
    def test_records_sorted_by_last_update_desc(self, fake_worktrees):
        """Most recently touched run appears first, across worktrees."""
        import time
        root, child = fake_worktrees
        # Older: in root
        _write_state(root, "run_old", top_state="RUNNING")
        time.sleep(0.02)
        # Newer: in child (bumping its mtime after root)
        _write_state(child, "run_new", top_state="RUNNING")
        records = collect_sessions()
        assert [r.run_id for r in records] == ["run_new", "run_old"]


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


# ── Task 2: worktree discovery union ───────────────────────────
#
# The collector must union all five discovery sources (cwd,
# known_worktrees, daemon cwds, pane owner cwds, git worktree list)
# and dedup by resolved path.  Each source alone is not sufficient;
# together they cover every path the user might reasonably expect.


class TestWorktreeDiscovery:
    def test_known_worktree_visible_after_daemon_shutdown(
        self, fake_worktrees
    ):
        """Plan Rule 4: daemon idle shutdown must not erase session visibility.

        Discovery from known_worktrees.json alone (no live daemon, no
        pane owner) must still surface the run.
        """
        root, child = fake_worktrees
        _write_state(child, "run_after_shutdown", top_state="PAUSED_FOR_HUMAN")
        # Default fixture: list_daemons=[], list_pane_owners=[], so the
        # child worktree is reachable ONLY via list_known_worktrees.
        records = collect_sessions()
        assert {r.run_id for r in records} == {"run_after_shutdown"}

    def test_git_worktree_list_discovers_missing_worktree(
        self, tmp_path, monkeypatch
    ):
        """When the registry is incomplete, `git worktree list` fills gaps.

        If a worktree was never registered (e.g., created outside the
        supervisor lifecycle) but the repo itself knows about it,
        discovery should still include it.
        """
        root = tmp_path / "root"
        git_linked = tmp_path / "linked"
        root.mkdir()
        git_linked.mkdir()
        _write_state(git_linked, "run_via_git", top_state="RUNNING")
        monkeypatch.chdir(root)

        # Registry is empty — known_worktrees does NOT list git_linked.
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons", lambda: []
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_pane_owners", lambda: []
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_known_worktrees",
            lambda: [],
        )
        # Only `git worktree list` knows about it.
        monkeypatch.setattr(
            "supervisor.operator.session_index._discover_git_worktrees",
            lambda cwd: [str(git_linked)],
        )

        records = collect_sessions()
        assert {r.run_id for r in records} == {"run_via_git"}

    def test_duplicate_paths_deduped_by_resolved_path(
        self, tmp_path, monkeypatch
    ):
        """Same worktree appearing in multiple sources scans once.

        cwd, known_worktrees, daemon cwd, pane owner cwd, and git
        worktree list can all report the same path in different string
        shapes (relative, absolute, trailing slash, symlink). Path
        resolution must dedupe so we don't double-scan or double-emit.
        """
        root = tmp_path / "root"
        child = tmp_path / "child"
        root.mkdir()
        child.mkdir()
        _write_state(child, "run_once", top_state="RUNNING")
        monkeypatch.chdir(root)

        child_abs = str(child.resolve())
        child_with_slash = child_abs + "/"
        child_as_dot = os.path.join(child_abs, ".")

        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons",
            lambda: [
                {"pid": 1, "cwd": child_abs, "socket": "/tmp/a.sock"},
            ],
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_pane_owners",
            lambda: [
                {
                    "pid": 2,
                    "cwd": child_with_slash,
                    "run_id": "other",
                    "pane_target": "0:0",
                    "controller_mode": "daemon",
                }
            ],
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_known_worktrees",
            lambda: [child_abs, child_with_slash],
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index._discover_git_worktrees",
            lambda cwd: [child_as_dot],
        )

        records = collect_sessions()
        # Only one record — dedup by resolved path, then by run_id.
        assert len(records) == 1
        assert records[0].run_id == "run_once"

    def test_local_only_skips_all_non_cwd_sources(
        self, fake_worktrees, monkeypatch
    ):
        """`local_only=True` must not touch known_worktrees / daemons /
        pane owners / git worktrees — it restricts strictly to cwd."""
        root, child = fake_worktrees
        _write_state(child, "run_elsewhere", top_state="RUNNING")
        # Even with rich registries, local_only should see nothing here.
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_daemons",
            lambda: [{"pid": 1, "cwd": str(child), "socket": "/tmp/x.sock"}],
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index.list_pane_owners",
            lambda: [
                {
                    "pid": 2,
                    "cwd": str(child),
                    "run_id": "run_elsewhere",
                    "pane_target": "0:0",
                    "controller_mode": "foreground",
                }
            ],
        )
        monkeypatch.setattr(
            "supervisor.operator.session_index._discover_git_worktrees",
            lambda cwd: [str(child)],
        )
        records = collect_sessions(local_only=True)
        assert records == []
