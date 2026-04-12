"""CLI behavior tests for status/list user-facing output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import yaml

from supervisor import app
from supervisor.config import RuntimeConfig


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


def _write_paused_state(tmp_path, *, run_id: str = "run_paused") -> None:
    runtime_dir = tmp_path / ".supervisor" / "runtime" / "runs" / run_id
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "top_state": "PAUSED_FOR_HUMAN",
        "current_node_id": "step_2",
        "pane_target": "%7",
        "spec_path": "/tmp/spec.yaml",
        "surface_type": "tmux",
        "human_escalations": [
            {"reason": "node mismatch persisted for 5 checkpoints"}
        ],
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
    assert "workflow_done" in out
    assert "thin-supervisor run summarize run_completed" in out


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
    assert "workflow_done" in out
    assert "thin-supervisor run summarize run_from_foreground" in out


def test_status_prints_pause_reason_and_next_action_for_local_state(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithNoRuns)
    _write_paused_state(tmp_path)

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "PAUSED_FOR_HUMAN" in out
    assert "node mismatch persisted for 5 checkpoints" in out
    assert "thin-supervisor run resume --spec /tmp/spec.yaml --pane %7 --surface tmux" in out


def test_list_prints_pause_reason_and_next_action_for_local_state(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithNoRuns)
    _write_paused_state(tmp_path)

    result = app.cmd_list(argparse.Namespace())

    assert result == 0
    out = capsys.readouterr().out
    assert "run_paused" in out
    assert "node mismatch persisted for 5 checkpoints" in out
    assert "thin-supervisor run resume --spec /tmp/spec.yaml --pane %7 --surface tmux" in out


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


def test_init_repair_restores_missing_config_and_logs_event(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    partial = tmp_path / ".supervisor"
    (partial / "specs").mkdir(parents=True)

    result = app.cmd_init(argparse.Namespace(force=False, repair=True))

    assert result == 0
    assert (partial / "config.yaml").exists()
    ops_log = partial / "runtime" / "ops_log.jsonl"
    assert ops_log.exists()
    record = json.loads(ops_log.read_text().strip())
    assert record["event_type"] == "init_repair"
    assert record["payload"]["created_config"] is True
    assert record["payload"]["supervisor_dir_preexisted"] is True
    out = capsys.readouterr().out
    assert "Repaired .supervisor/" in out


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
                 author_run_id: str = "human", title: str = "",
                 metadata: dict | None = None) -> dict:
        self.saved.append({
            "content": content,
            "note_type": note_type,
            "author_run_id": author_run_id,
            "title": title,
            "metadata": metadata or {},
        })
        return {"ok": True, "note_id": "note_123"}


def _write_draft_spec(path: Path) -> None:
    path.write_text(
        "kind: linear_plan\n"
        "id: draft_plan\n"
        "goal: test approval gate\n"
        "approval:\n"
        "  required: true\n"
        "  status: draft\n"
        "steps:\n"
        "  - id: s1\n"
        "    type: task\n"
        "    objective: do something\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
    )


def test_run_register_rejects_draft_spec_before_starting_daemon(
    tmp_path, monkeypatch, capsys,
):
    spec_path = tmp_path / "draft.yaml"
    _write_draft_spec(spec_path)
    monkeypatch.setattr(app.RuntimeConfig, "load", lambda path: RuntimeConfig())
    monkeypatch.setattr(app, "_ensure_daemon", lambda *_: (_ for _ in ()).throw(AssertionError("daemon should not start")))

    result = app.cmd_run_register(argparse.Namespace(
        spec=str(spec_path),
        pane="%1",
        target=None,
        surface="tmux",
        config=None,
    ))

    assert result == 1
    out = capsys.readouterr().out
    assert "requires user approval" in out
    assert "thin-supervisor spec approve --spec" in out


def test_run_foreground_rejects_draft_spec(tmp_path, monkeypatch, capsys):
    spec_path = tmp_path / "draft.yaml"
    _write_draft_spec(spec_path)
    monkeypatch.setattr(app.RuntimeConfig, "load", lambda path: RuntimeConfig())

    result = app.cmd_run_foreground(argparse.Namespace(
        spec=str(spec_path),
        pane="%1",
        target=None,
        surface="tmux",
        config=None,
    ))

    assert result == 1
    out = capsys.readouterr().out
    assert "requires user approval" in out


def test_run_resume_rejects_draft_spec_before_starting_daemon(
    tmp_path, monkeypatch, capsys,
):
    spec_path = tmp_path / "draft.yaml"
    _write_draft_spec(spec_path)
    monkeypatch.setattr(app.RuntimeConfig, "load", lambda path: RuntimeConfig())
    monkeypatch.setattr(app, "_ensure_daemon", lambda *_: (_ for _ in ()).throw(AssertionError("daemon should not start")))

    result = app.cmd_run_resume(argparse.Namespace(
        spec=str(spec_path),
        pane="%1",
        target=None,
        surface="tmux",
        config=None,
    ))

    assert result == 1
    out = capsys.readouterr().out
    assert "requires user approval" in out
    assert "thin-supervisor spec approve --spec" in out


def test_spec_approve_updates_yaml_status(tmp_path, capsys):
    spec_path = tmp_path / "draft.yaml"
    _write_draft_spec(spec_path)

    result = app.cmd_spec(argparse.Namespace(
        spec_action="approve",
        spec=str(spec_path),
        by="human",
    ))

    assert result == 0
    data = yaml.safe_load(spec_path.read_text())
    assert data["approval"]["status"] == "approved"
    assert data["approval"]["approved_by"] == "human"
    assert data["approval"]["approved_at"]
    out = capsys.readouterr().out
    assert "Spec approved" in out


def test_spec_approve_rejects_non_mapping_approval(tmp_path, capsys):
    spec_path = tmp_path / "draft.yaml"
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: bad_approval\n"
        "goal: invalid approval shape\n"
        "approval: []\n"
        "steps:\n"
        "  - id: s1\n"
        "    type: task\n"
        "    objective: do something\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
    )

    result = app.cmd_spec(argparse.Namespace(
        spec_action="approve",
        spec=str(spec_path),
        by="human",
    ))

    assert result == 1
    out = capsys.readouterr().out
    assert "approval must be a YAML mapping" in out


def test_learn_friction_add_and_list_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    add_result = app.cmd_learn(argparse.Namespace(
        learn_action="friction",
        friction_action="add",
        prefs_action=None,
        kind="repeated_confirmation",
        message="user approved twice",
        run_id="run_123",
        user_id="default",
        signal=["user_repeated_approval"],
        json=False,
        key=None,
        value=None,
    ))

    assert add_result == 0
    add_out = capsys.readouterr().out
    assert "Friction event recorded:" in add_out

    list_result = app.cmd_learn(argparse.Namespace(
        learn_action="friction",
        friction_action="list",
        prefs_action=None,
        kind="",
        message=None,
        run_id="run_123",
        user_id="default",
        signal=[],
        json=True,
        key=None,
        value=None,
    ))

    assert list_result == 0
    events = json.loads(capsys.readouterr().out)
    assert len(events) == 1
    assert events[0]["kind"] == "repeated_confirmation"


def test_learn_prefs_set_and_show_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    set_result = app.cmd_learn(argparse.Namespace(
        learn_action="prefs",
        friction_action=None,
        prefs_action="set",
        kind=None,
        message=None,
        run_id=None,
        user_id="default",
        signal=[],
        json=False,
        key="approval_style",
        value="terse",
    ))

    assert set_result == 0
    set_out = capsys.readouterr().out
    assert "Preference saved:" in set_out

    show_result = app.cmd_learn(argparse.Namespace(
        learn_action="prefs",
        friction_action=None,
        prefs_action="show",
        kind=None,
        message=None,
        run_id=None,
        user_id="default",
        signal=[],
        json=True,
        key=None,
        value=None,
    ))

    assert show_result == 0
    prefs = json.loads(capsys.readouterr().out)
    assert prefs["approval_style"] == "terse"


def test_learn_friction_add_returns_controlled_error_on_store_failure(monkeypatch, capsys):
    monkeypatch.setattr(app, "append_friction_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = app.cmd_learn(argparse.Namespace(
        learn_action="friction",
        friction_action="add",
        prefs_action=None,
        kind="repeated_confirmation",
        message="user approved twice",
        run_id="run_123",
        user_id="default",
        signal=["user_repeated_approval"],
        json=False,
        key=None,
        value=None,
        config=None,
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "Error: boom" in err


def test_learn_prefs_show_returns_controlled_error_on_store_failure(monkeypatch, capsys):
    monkeypatch.setattr(app, "load_user_preferences", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("corrupt user preferences store")))

    result = app.cmd_learn(argparse.Namespace(
        learn_action="prefs",
        friction_action=None,
        prefs_action="show",
        kind=None,
        message=None,
        run_id=None,
        user_id="default",
        signal=[],
        json=True,
        key=None,
        value=None,
        config=None,
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "Error: corrupt user preferences store" in err


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
    assert daemon.saved[0]["metadata"]["consultation_id"] == "oracle_123"


def test_run_export_json_output(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.history.export_run", lambda run_id, runtime_dir=".supervisor/runtime": {
        "schema_version": "run_export.v1",
        "run_id": run_id,
        "state": {"spec_id": "demo"},
        "decision_log": [],
        "session_log": [],
        "notes": [],
    })

    result = app.cmd_run_export(argparse.Namespace(run_id="run_demo", output="", json=True, config=None))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run_demo"


def test_run_summarize_json_output(monkeypatch, capsys):
    monkeypatch.setattr("supervisor.history.export_run", lambda run_id, runtime_dir=".supervisor/runtime": {"run_id": run_id})
    monkeypatch.setattr("supervisor.history.summarize_run", lambda exported: {
        "run_id": exported["run_id"],
        "top_state": "COMPLETED",
        "counts": {"checkpoints": 3},
    })

    result = app.cmd_run_summarize(argparse.Namespace(run_id="run_demo", json=True, config=None))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["checkpoints"] == 3


def test_run_replay_json_output(monkeypatch, capsys):
    monkeypatch.setattr("supervisor.history.export_run", lambda run_id, runtime_dir=".supervisor/runtime": {"run_id": run_id})
    monkeypatch.setattr("supervisor.history.replay_run", lambda exported: {
        "run_id": exported["run_id"],
        "matched_count": 2,
        "decision_count": 2,
        "mismatches": [],
    })

    result = app.cmd_run_replay(argparse.Namespace(run_id="run_demo", json=True, config=None))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["matched_count"] == 2


def test_run_postmortem_writes_default_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.history.export_run", lambda run_id, runtime_dir=".supervisor/runtime": {"run_id": run_id})
    monkeypatch.setattr("supervisor.history.render_postmortem", lambda exported: f"# Run Postmortem: {exported['run_id']}\n")

    result = app.cmd_run_postmortem(argparse.Namespace(run_id="run_demo", output="", config=None))

    assert result == 0
    report = tmp_path / ".supervisor" / "reports" / "run_demo.md"
    assert report.exists()
    assert "# Run Postmortem: run_demo" in report.read_text()


def test_history_commands_use_configured_runtime_dir(monkeypatch, capsys):
    captured: dict[str, str] = {}

    class _Cfg:
        runtime_dir = "/tmp/custom-runtime"

    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: _Cfg())
    monkeypatch.setattr("supervisor.history.export_run", lambda run_id, runtime_dir=".supervisor/runtime": {
        "run_id": run_id,
        "runtime_dir": runtime_dir,
    } if not captured.setdefault("runtime_dir", runtime_dir) else {"run_id": run_id, "runtime_dir": runtime_dir})
    monkeypatch.setattr("supervisor.history.summarize_run", lambda exported: {
        "run_id": exported["run_id"],
        "top_state": "COMPLETED",
        "counts": {"checkpoints": 0, "verifications_ok": 0, "routing_events": 0},
        "oracle_consultation_ids": [],
    })

    result = app.cmd_run_summarize(argparse.Namespace(run_id="run_demo", json=True, config="alt.yaml"))

    assert result == 0
    assert captured["runtime_dir"] == "/tmp/custom-runtime"
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run_demo"


def test_history_commands_return_controlled_error(monkeypatch, capsys):
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: type("Cfg", (), {"runtime_dir": ".supervisor/runtime"})())
    monkeypatch.setattr("supervisor.history.export_run", lambda run_id, runtime_dir=".supervisor/runtime": (_ for _ in ()).throw(FileNotFoundError("missing run")))

    result = app.cmd_run_export(argparse.Namespace(run_id="run_demo", output="", json=True, config=None))

    assert result == 1
    assert "Error: missing run" in capsys.readouterr().out


def test_oracle_consult_returns_controlled_error_when_consult_raises(monkeypatch, capsys):
    class _BoomOracleClient:
        def consult(self, **kwargs):
            raise ValueError("OPENAI_API_KEY is not set")

    monkeypatch.setattr("supervisor.oracle.client.OracleClient", lambda: _BoomOracleClient())

    result = app.cmd_oracle(argparse.Namespace(
        oracle_action="consult",
        question="Review this file",
        file=[],
        mode="review",
        provider="openai",
        run="",
        json=False,
    ))

    assert result == 1
    out = capsys.readouterr().out
    assert "oracle consultation failed" in out.lower()


def test_oracle_consult_returns_controlled_error_when_note_persist_raises(tmp_path, monkeypatch, capsys):
    target = tmp_path / "mod.py"
    target.write_text("print('hi')\n")

    class _FailingDaemon:
        def is_running(self) -> bool:
            return True

        def note_add(self, *args, **kwargs):
            raise OSError("socket closed")

    monkeypatch.setattr("supervisor.oracle.client.OracleClient", lambda: _FakeOracleClient())
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", lambda: _FailingDaemon())

    result = app.cmd_oracle(argparse.Namespace(
        oracle_action="consult",
        question="Plan this change",
        file=[str(target)],
        mode="plan",
        provider="auto",
        run="run_abc",
        json=False,
    ))

    assert result == 1
    out = capsys.readouterr().out
    assert "failed to persist oracle note" in out.lower()
