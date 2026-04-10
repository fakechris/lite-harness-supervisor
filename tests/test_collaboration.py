"""Tests for collaboration plane: list, observe, note."""
import json
import os
import socket
import tempfile
import threading
import time

import pytest

from supervisor.daemon.server import DaemonServer
from supervisor.daemon.client import DaemonClient


@pytest.fixture
def collab_daemon(tmp_path):
    """Start daemon with temp paths for collaboration tests."""
    sock_path = tempfile.mktemp(prefix="sv_collab_", suffix=".sock", dir="/tmp")
    pid_path = str(tmp_path / "test.pid")
    runs_dir = str(tmp_path / "runs")

    server = DaemonServer(sock_path=sock_path, pid_path=pid_path, runs_dir=runs_dir)
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()

    client = DaemonClient(sock_path)
    for _ in range(20):
        time.sleep(0.1)
        if client.is_running():
            break

    yield server, client

    server._shutdown.set()
    thread.join(timeout=3)
    try:
        os.unlink(sock_path)
    except OSError:
        pass


class TestListRuns:
    def test_empty_list(self, collab_daemon):
        _, client = collab_daemon
        result = client.list_runs()
        assert result["ok"] is True
        assert result["runs"] == []

    def test_list_after_register(self, collab_daemon, tmp_path):
        _, client = collab_daemon
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\nid: test\ngoal: test\n"
            "steps:\n  - id: s1\n    type: task\n    objective: do\n"
            "    verify:\n      - type: command\n        run: echo ok\n        expect: pass\n"
        )
        reg = client.register(str(spec_path), "test-pane:0")
        assert reg["ok"]

        result = client.list_runs()
        assert len(result["runs"]) == 1
        assert result["runs"][0]["pane_target"] == "test-pane:0"
        assert result["runs"][0]["spec_id"] == "test"


class TestObserve:
    def test_observe_nonexistent(self, collab_daemon):
        _, client = collab_daemon
        result = client.observe("run_nonexistent")
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_observe_active_run(self, collab_daemon, tmp_path):
        _, client = collab_daemon
        spec_path = tmp_path / "test.yaml"
        spec_path.write_text(
            "kind: linear_plan\nid: observe_test\ngoal: test\n"
            "steps:\n  - id: s1\n    type: task\n    objective: do\n"
            "    verify:\n      - type: command\n        run: echo ok\n        expect: pass\n"
        )
        reg = client.register(str(spec_path), "obs-pane:0")
        run_id = reg["run_id"]
        time.sleep(0.3)

        result = client.observe(run_id)
        assert result["ok"] is True
        assert result["run_id"] == run_id
        assert "state" in result
        assert result["state"].get("spec_id") == "observe_test"


class TestNotes:
    def test_add_and_list_note(self, collab_daemon):
        _, client = collab_daemon

        # Add a note
        result = client.note_add("found auth bug in redis cache", note_type="finding")
        assert result["ok"] is True
        note_id = result["note_id"]
        assert note_id.startswith("note_")

        # List notes
        result = client.note_list()
        assert result["ok"] is True
        assert len(result["notes"]) == 1
        assert result["notes"][0]["content"] == "found auth bug in redis cache"
        assert result["notes"][0]["note_type"] == "finding"

    def test_filter_by_type(self, collab_daemon):
        _, client = collab_daemon
        client.note_add("context note", note_type="context")
        client.note_add("finding note", note_type="finding")
        client.note_add("warning note", note_type="warning")

        result = client.note_list(note_type="finding")
        assert len(result["notes"]) == 1
        assert result["notes"][0]["note_type"] == "finding"

    def test_filter_by_run(self, collab_daemon):
        _, client = collab_daemon
        client.note_add("from run A", author_run_id="run_aaa")
        client.note_add("from run B", author_run_id="run_bbb")

        result = client.note_list(run_id="run_aaa")
        assert len(result["notes"]) == 1
        assert result["notes"][0]["author_run_id"] == "run_aaa"

    def test_empty_content_rejected(self, collab_daemon):
        _, client = collab_daemon
        result = client.note_add("")
        assert result["ok"] is False

    def test_notes_persist_across_queries(self, collab_daemon):
        _, client = collab_daemon
        client.note_add("note 1")
        client.note_add("note 2")
        client.note_add("note 3")

        result = client.note_list()
        assert len(result["notes"]) == 3
