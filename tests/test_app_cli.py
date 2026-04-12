"""CLI behavior tests for status/list user-facing output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from supervisor import app


class _DaemonWithNoRuns:
    def is_running(self) -> bool:
        return True

    def status(self) -> dict:
        return {"ok": True, "runs": []}

    def list_runs(self) -> dict:
        return {"ok": True, "runs": []}


def _write_completed_state(tmp_path, *, run_id: str = "run_completed") -> None:
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "top_state": "COMPLETED",
        "current_node_id": "verify",
        "pane_target": "%1",
    }))


def test_status_mentions_local_completed_state_when_daemon_has_no_runs(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithNoRuns)
    _write_completed_state(tmp_path)

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "Daemon running, no active runs." in out
    assert "Local state found:" in out
    assert "run_completed" in out
    assert "COMPLETED" in out


def test_list_mentions_local_completed_state_when_daemon_has_no_runs(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithNoRuns)
    _write_completed_state(tmp_path, run_id="run_from_foreground")

    result = app.cmd_list(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "No active runs." in out
    assert "Local state found:" in out
    assert "run_from_foreground" in out
    assert "COMPLETED" in out


def test_legacy_run_requires_explicit_register_or_foreground(capsys):
    result = app.cmd_run_legacy(argparse.Namespace(
        spec_path="plan.yaml",
        pane="%0",
        config=None,
        event_file=None,
        dry_run=False,
        daemon=False,
    ))

    assert result == 1
    out = capsys.readouterr().out
    assert "Legacy run syntax has been removed." in out
    assert "thin-supervisor run register" in out
    assert "thin-supervisor run foreground" in out


def test_ps_lists_registered_daemons(monkeypatch, capsys):
    monkeypatch.setattr(app, "_list_global_daemons", lambda: [
        {
            "pid": 111,
            "cwd": "/tmp/project-a",
            "socket": "/tmp/a.sock",
            "active_runs": 2,
            "started_at": "2026-04-10T10:00:00Z",
        },
        {
            "pid": 222,
            "cwd": "/tmp/project-b",
            "socket": "/tmp/b.sock",
            "active_runs": 0,
            "started_at": "2026-04-10T10:05:00Z",
        },
    ], raising=False)

    result = app.cmd_ps(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "PID" in out
    assert "/tmp/project-a" in out
    assert "/tmp/b.sock" in out
    assert "2" in out


def test_pane_owner_reports_global_lock(monkeypatch, capsys):
    monkeypatch.setattr(app, "_find_global_pane_owner", lambda pane: {
        "pane_target": pane,
        "pid": 333,
        "cwd": "/tmp/project-c",
        "run_id": "run_lock",
        "spec_path": "/tmp/project-c/.supervisor/specs/plan.yaml",
    }, raising=False)

    result = app.cmd_pane_owner(argparse.Namespace(pane="%7"))

    assert result == 0
    out = capsys.readouterr().out
    assert "%7" in out
    assert "run_lock" in out
    assert "/tmp/project-c" in out


def test_session_jsonl_prefers_current_session_path(monkeypatch, capsys):
    monkeypatch.setattr("supervisor.session_detect.detect_agent", lambda: "codex")
    monkeypatch.setattr("supervisor.session_detect.detect_session_id", lambda agent="": "thread-123")
    monkeypatch.setattr(
        "supervisor.session_detect.find_jsonl_for_session",
        lambda session_id, agent="": Path("/tmp/current.jsonl"),
    )
    monkeypatch.setattr(
        "supervisor.session_detect.find_latest_jsonl",
        lambda agent="": Path("/tmp/latest.jsonl"),
    )

    result = app.cmd_session(argparse.Namespace(session_action="jsonl"))

    assert result == 0
    out = capsys.readouterr().out.strip()
    assert out == "/tmp/current.jsonl"


class _FakeOracleClient:
    def consult(self, *, question, file_paths, mode, provider):
        return {
            "consultation_id": "oracle_123",
            "provider": "self-review",
            "model_name": "self-review",
            "mode": mode,
            "question": question,
            "files": file_paths,
            "response_text": "Advisory review",
            "source": "fallback",
            "timestamp": "2026-04-12T00:00:00Z",
        }


class _DaemonForOracle:
    def __init__(self, sock_path=None):
        self.saved = []

    def is_running(self) -> bool:
        return True

    def note_add(self, content: str, *, note_type: str = "context",
                 author_run_id: str = "human", title: str = "") -> dict:
        self.saved.append({
            "content": content,
            "note_type": note_type,
            "author_run_id": author_run_id,
            "title": title,
        })
        return {"ok": True, "note_id": "note_123"}


def test_oracle_consult_json_output(tmp_path, monkeypatch, capsys):
    target = tmp_path / "mod.py"
    target.write_text("print('hi')\n")
    monkeypatch.setattr("supervisor.oracle.client.OracleClient", lambda: _FakeOracleClient())

    result = app.cmd_oracle(argparse.Namespace(
        oracle_action="consult",
        question="Review this file",
        file=[str(target)],
        mode="review",
        provider="auto",
        run="",
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["consultation_id"] == "oracle_123"
    assert payload["files"] == [str(target)]


def test_oracle_consult_saves_note_for_run(tmp_path, monkeypatch, capsys):
    target = tmp_path / "mod.py"
    target.write_text("print('hi')\n")
    daemon = _DaemonForOracle()
    monkeypatch.setattr("supervisor.oracle.client.OracleClient", lambda: _FakeOracleClient())
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", lambda: daemon)

    result = app.cmd_oracle(argparse.Namespace(
        oracle_action="consult",
        question="Plan this change",
        file=[str(target)],
        mode="plan",
        provider="auto",
        run="run_abc",
        json=False,
    ))

    assert result == 0
    assert len(daemon.saved) == 1
    assert daemon.saved[0]["note_type"] == "oracle"
    assert daemon.saved[0]["author_run_id"] == "run_abc"
    assert "Advisory review" in daemon.saved[0]["content"]
