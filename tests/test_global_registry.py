"""Tests for global daemon registry and pane locking."""
from __future__ import annotations

import os
import time
from pathlib import Path

from supervisor.daemon.server import DaemonServer
from supervisor.global_registry import (
    acquire_pane_lock,
    find_pane_owner,
    list_daemons,
    register_daemon,
    release_pane_lock,
    unregister_daemon,
    update_daemon,
    _write_json,
)


def test_daemon_registry_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_DIR", str(tmp_path / "global"))

    meta = {
        "pid": os.getpid(),
        "cwd": "/tmp/project-a",
        "socket": "/tmp/project-a.sock",
        "active_runs": 0,
        "started_at": "2026-04-10T10:00:00Z",
    }
    register_daemon(meta)
    update_daemon(meta["socket"], active_runs=2)

    daemons = list_daemons()
    assert len(daemons) == 1
    assert daemons[0]["socket"] == "/tmp/project-a.sock"
    assert daemons[0]["active_runs"] == 2

    unregister_daemon(meta["socket"])
    assert list_daemons() == []


def test_stale_pane_lock_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_DIR", str(tmp_path / "global"))

    stale = {
        "pane_target": "%3",
        "pid": 999999,
        "cwd": "/tmp/stale",
        "run_id": "run_stale",
        "spec_path": "/tmp/stale/spec.yaml",
    }
    acquired, owner = acquire_pane_lock("%3", stale)
    assert acquired is True
    assert owner["run_id"] == "run_stale"

    fresh = {
        "pane_target": "%3",
        "pid": os.getpid(),
        "cwd": "/tmp/fresh",
        "run_id": "run_fresh",
        "spec_path": "/tmp/fresh/spec.yaml",
    }
    acquired, owner = acquire_pane_lock("%3", fresh)
    assert acquired is True
    assert owner["run_id"] == "run_fresh"
    assert find_pane_owner("%3")["cwd"] == "/tmp/fresh"


def test_release_pane_lock_is_owner_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_DIR", str(tmp_path / "global"))

    owner = {
        "pane_target": "%7",
        "pid": os.getpid(),
        "cwd": "/tmp/one",
        "run_id": "run_one",
        "spec_path": "/tmp/one/spec.yaml",
    }
    acquire_pane_lock("%7", owner)

    release_pane_lock("%7", "run_other")
    assert find_pane_owner("%7")["run_id"] == "run_one"

    release_pane_lock("%7", "run_one")
    assert find_pane_owner("%7") is None


def test_write_json_uses_atomic_replace(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    calls: list[tuple[str, str]] = []
    original_replace = os.replace

    def record_replace(src, dst):
        calls.append((src, str(dst)))
        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", record_replace)

    _write_json(target, {"ok": True})

    assert target.exists()
    assert calls
    assert calls[0][1] == str(target)
    tmp_written = Path(calls[0][0])
    assert tmp_written.exists() is False


def test_daemon_server_rejects_cross_daemon_pane_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("THIN_SUPERVISOR_GLOBAL_DIR", str(tmp_path / "global"))

    spec_path = tmp_path / "test.yaml"
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: test\n"
        "goal: test\n"
        "steps:\n"
        "  - id: s1\n"
        "    type: task\n"
        "    objective: do something\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
    )

    def fake_run_worker(self, entry, spec, state):
        entry.stop_event.wait(1)

    monkeypatch.setattr(DaemonServer, "_run_worker", fake_run_worker)

    server_a = DaemonServer(
        sock_path=str(tmp_path / "a.sock"),
        pid_path=str(tmp_path / "a.pid"),
        runs_dir=str(tmp_path / "runs-a"),
    )
    server_b = DaemonServer(
        sock_path=str(tmp_path / "b.sock"),
        pid_path=str(tmp_path / "b.pid"),
        runs_dir=str(tmp_path / "runs-b"),
    )

    req = {
        "spec_path": str(spec_path),
        "pane_target": "%0",
        "workspace_root": str(tmp_path),
    }
    first = server_a._do_register(req)
    assert first["ok"] is True

    second = server_b._do_register(req)
    assert second["ok"] is False
    assert "already owned" in second["error"]

    server_a._do_stop_all()
    time.sleep(0.05)
    server_a._reap_finished()
    server_b._do_stop_all()
    server_b._reap_finished()
