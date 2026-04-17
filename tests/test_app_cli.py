"""CLI behavior tests for status/list user-facing output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pytest
import yaml

from supervisor import app
from supervisor.config import RuntimeConfig


@pytest.fixture(autouse=True)
def _hermetic_session_index(monkeypatch):
    """Isolate status/list tests from real global registries.

    `cmd_status` / `cmd_list` route through `collect_sessions()`, which in
    turn reads `list_known_worktrees()`, `list_daemons()`, and
    `list_pane_owners()`. Without stubbing these, tests inherit state from
    prior runs on the developer's machine. Tests that need specific
    registries patch these on top of the autouse defaults.
    """
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


class _DaemonWithNoRuns:
    def is_running(self) -> bool:
        return True

    def status(self) -> dict:
        return {"ok": True, "runs": []}

    def list_runs(self) -> dict:
        return {"ok": True, "runs": []}


class _DaemonStopped:
    def is_running(self) -> bool:
        return False


def test_parse_runtime_argv_supports_legacy_run_shim():
    args = app._parse_runtime_argv(["run", "plan.yaml", "--pane", "%0"])

    assert args.command == "run"
    assert args.run_action is None
    assert args.spec_path == "plan.yaml"
    assert args.pane == "%0"


def test_parse_runtime_argv_keeps_run_subcommands():
    args = app._parse_runtime_argv(["run", "register", "--spec", "plan.yaml", "--pane", "%0"])

    assert args.command == "run"
    assert args.run_action == "register"
    assert args.spec == "plan.yaml"
    assert args.pane == "%0"


def _write_completed_state(tmp_path, *, run_id: str = "run_completed") -> None:
    run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
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


def _write_running_state(tmp_path, *, run_id: str = "run_running") -> None:
    runtime_dir = tmp_path / ".supervisor" / "runtime" / "runs" / run_id
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "top_state": "RUNNING",
        "current_node_id": "step_1",
        "pane_target": "%9",
        "spec_path": "/tmp/spec.yaml",
        "surface_type": "tmux",
        "controller_mode": "daemon",
    }))


def _write_foreground_running_state(tmp_path, *, run_id: str = "run_foreground") -> None:
    runtime_dir = tmp_path / ".supervisor" / "runtime" / "runs" / run_id
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "top_state": "RUNNING",
        "current_node_id": "step_live",
        "pane_target": "%11",
        "spec_path": "/tmp/spec.yaml",
        "surface_type": "tmux",
        "controller_mode": "foreground",
    }))


def test_status_shows_recently_completed_runs(
    tmp_path, monkeypatch, capsys,
):
    """Completed runs shown in 'Recently completed' section with summarize hint."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithNoRuns)
    _write_completed_state(tmp_path)

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "Recently completed" in out
    assert "run_completed" in out
    assert "summarize" in out


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


def test_status_marks_local_running_state_as_orphaned_when_daemon_has_no_runs(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonWithNoRuns)
    _write_running_state(tmp_path)

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_running" in out
    assert "PAUSED_FOR_HUMAN" in out
    assert "persisted run was left in progress without an active daemon worker" in out
    assert "thin-supervisor run resume --spec /tmp/spec.yaml --pane %9 --surface tmux" in out


def test_status_daemon_down_fallback_marks_daemon_owned_running_state_as_orphaned(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _write_running_state(tmp_path, run_id="run_orphaned")

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_orphaned" in out
    assert "PAUSED_FOR_HUMAN" in out
    assert "persisted run was left in progress without an active daemon worker" in out


def test_status_daemon_down_fallback_marks_dead_foreground_as_orphaned(
    tmp_path, monkeypatch, capsys,
):
    """Foreground RUNNING state with dead process is shown as orphaned."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _write_foreground_running_state(tmp_path)

    result = app.cmd_status(argparse.Namespace(config=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_foreground" in out
    # Dead foreground process is now detected as orphaned
    assert "orphaned" in out.lower() or "PAUSED_FOR_HUMAN" in out


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
    assert "/tmp/project-b" in out
    assert "active" in out


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


def test_pane_owner_shows_controller_mode(monkeypatch, capsys):
    """pane-owner output includes controller mode."""
    monkeypatch.setattr(app, "_find_global_pane_owner", lambda pane: {
        "pane_target": pane,
        "pid": 444,
        "cwd": "/tmp/project-d",
        "run_id": "run_mode",
        "spec_path": "/tmp/spec.yaml",
        "controller_mode": "foreground",
    }, raising=False)

    result = app.cmd_pane_owner(argparse.Namespace(pane="%8"))

    assert result == 0
    out = capsys.readouterr().out
    assert "Controller: foreground" in out
    assert "run_mode" in out


def test_pane_owner_shows_daemon_controller(monkeypatch, capsys):
    """pane-owner output shows daemon as controller mode."""
    monkeypatch.setattr(app, "_find_global_pane_owner", lambda pane: {
        "pane_target": pane,
        "pid": 555,
        "cwd": "/tmp/project-e",
        "run_id": "run_d",
        "spec_path": "/tmp/spec.yaml",
        "controller_mode": "daemon",
    }, raising=False)

    result = app.cmd_pane_owner(argparse.Namespace(pane="%9"))

    assert result == 0
    out = capsys.readouterr().out
    assert "Controller: daemon" in out


def test_foreground_help_text_says_debug():
    """Foreground subcommand help text indicates debug-only."""
    import io
    parser = app.build_runtime_parser()
    buf = io.StringIO()
    parser.print_help(buf)
    # Walk subparsers to find run->foreground help
    for action in parser._subparsers._actions:
        if not hasattr(action, "choices") or action.choices is None:
            continue
        run_parser = action.choices.get("run")
        if run_parser:
            run_parser.print_help(buf)
    full_help = buf.getvalue().lower()
    assert "debug" in full_help


def test_runtime_parser_does_not_expose_devtime_commands():
    parser = app.build_runtime_parser()

    help_text = parser.format_help()

    assert " eval " not in f" {help_text} "
    assert " learn " not in f" {help_text} "
    assert " oracle " not in f" {help_text} "
    assert " run " in f" {help_text} "
    assert " daemon " in f" {help_text} "


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


def test_learn_friction_summarize_json(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    config_path = tmp_path / ".supervisor" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"runtime_dir": str(runtime_dir)}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    app.cmd_learn(argparse.Namespace(
        learn_action="friction",
        friction_action="add",
        kind="repeated_confirmation",
        message="user approved twice",
        run_id="run_1",
        user_id="default",
        signal=["user_repeated_approval"],
        json=False,
        config=None,
    ))
    capsys.readouterr()

    result = app.cmd_learn(argparse.Namespace(
        learn_action="friction",
        friction_action="summarize",
        kind="",
        run_id="run_1",
        user_id="default",
        json=True,
        config=None,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_events"] == 1
    assert payload["by_kind"]["repeated_confirmation"] == 1


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


def test_eval_run_outputs_json_summary(capsys):
    result = app.cmd_eval(argparse.Namespace(
        eval_action="run",
        suite="approval-core",
        suite_file=None,
        policy="builtin-approval-v1",
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["suite"] == "approval-core"
    assert payload["counts"]["total"] >= 1
    assert "pass_rate" in payload["counts"]
    assert "weighted" in payload


def test_eval_replay_outputs_json_summary(tmp_path, monkeypatch, capsys):
    run_id = "run_cli_replay"
    spec_path = tmp_path / ".supervisor" / "specs" / "demo.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: demo\n"
        "goal: replay cli test\n"
        "steps:\n"
        "  - id: first\n"
        "    type: task\n"
        "    objective: first\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id,
        "spec_id": "demo",
        "mode": "linear_plan",
        "top_state": "COMPLETED",
        "current_node_id": "first",
        "current_attempt": 0,
        "done_node_ids": ["first"],
        "branch_history": [],
        "human_escalations": [],
        "retry_budget": {"per_node": 3, "global_limit": 12, "used_global": 0},
        "last_agent_checkpoint": {"status": "workflow_done", "current_node": "first", "summary": "done", "checkpoint_seq": 1},
        "checkpoint_seq": 1,
        "verification": {"ok": True, "results": [{"type": "command", "ok": True}]},
        "last_event": {},
        "spec_path": str(spec_path),
        "spec_hash": "",
        "pane_target": "%1",
        "surface_type": "tmux",
        "workspace_root": str(tmp_path),
        "completed_reviews": [],
        "last_injected_node_id": "first",
        "last_injected_attempt": 0,
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    decision = {
        "decision_id": "dec_1",
        "decision": "VERIFY_STEP",
        "reason": "checkpoint says workflow_done",
        "confidence": 1.0,
        "needs_human": False,
        "timestamp": "2026-04-12T00:00:01Z",
        "gate_type": "checkpoint_status",
        "triggered_by_seq": 1,
        "triggered_by_checkpoint_id": "cp_1",
        "next_instruction": None,
        "selected_branch": None,
        "next_node_id": None,
    }
    (run_dir / "decision_log.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")
    events = [
        {"run_id": run_id, "seq": 1, "event_type": "checkpoint", "timestamp": "2026-04-12T00:00:01Z", "payload": {"checkpoint_id": "cp_1", "run_id": run_id, "checkpoint_seq": 1, "status": "workflow_done", "current_node": "first", "summary": "done"}},
        {"run_id": run_id, "seq": 2, "event_type": "gate_decision", "timestamp": "2026-04-12T00:00:01Z", "payload": decision},
        {"run_id": run_id, "seq": 3, "event_type": "verification", "timestamp": "2026-04-12T00:00:01Z", "payload": {"ok": True, "results": [{"type": "command", "ok": True, "command": "echo ok"}]}},
    ]
    (run_dir / "session_log.jsonl").write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
    shared = tmp_path / ".supervisor" / "runtime" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "notes.jsonl").write_text("", encoding="utf-8")
    (shared / "friction_events.jsonl").write_text("", encoding="utf-8")
    (shared / "user_preferences.json").write_text(json.dumps({"default": {}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = app.cmd_eval(argparse.Namespace(
        eval_action="replay",
        run_id=run_id,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == run_id
    assert payload["summary"]["mismatch_count"] == 0


def test_eval_compare_outputs_json_summary(capsys):
    result = app.cmd_eval(argparse.Namespace(
        eval_action="compare",
        suite="approval-core",
        suite_file=None,
        baseline_policy="builtin-approval-v1",
        candidate_policy="builtin-approval-strict-v1",
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["suite"] == "approval-core"
    assert payload["summary"]["wins"]["baseline"] >= 1
    assert payload["summary"]["wins"]["candidate"] == 0


def test_eval_expand_writes_jsonl_output(tmp_path, capsys):
    output_path = tmp_path / "approval-expanded.jsonl"

    result = app.cmd_eval(argparse.Namespace(
        eval_action="expand",
        suite="approval-core",
        suite_file=None,
        output=str(output_path),
        variants_per_case=2,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(output_path)
    assert payload["generated_cases"] >= 1
    assert output_path.exists()


def test_eval_propose_outputs_json_summary(capsys):
    result = app.cmd_eval(argparse.Namespace(
        eval_action="propose",
        suite="approval-core",
        suite_file=None,
        baseline_policy="builtin-approval-v1",
        objective="reduce_false_approval",
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["suite"] == "approval-core"
    assert payload["recommended_candidate_policy"] == "builtin-approval-strict-v1"


def test_eval_list_includes_new_supervision_policy_suites(capsys):
    result = app.cmd_eval(argparse.Namespace(eval_action="list"))

    out = capsys.readouterr().out
    assert result == 0
    assert "approval-core" in out
    assert "routing-core" in out
    assert "escalation-core" in out
    assert "finish-gate-core" in out


def test_eval_run_can_save_report(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    result = app.cmd_eval(argparse.Namespace(
        eval_action="run",
        suite="approval-core",
        suite_file=None,
        policy="builtin-approval-v1",
        output="",
        save_report=True,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_path"]


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


def test_eval_canary_json_output(monkeypatch, capsys):
    monkeypatch.setattr("supervisor.eval.run_canary_eval", lambda run_ids, **kwargs: {
        "decision": "hold",
        "summary": {
            "run_count": len(run_ids),
            "avg_pass_rate": 0.75,
            "mismatch_kinds": {"ux_only_divergence": 1},
            "friction": {"total_events": 1, "by_kind": {"repeated_confirmation": 1}, "by_signal": {}},
        },
        "runs": [{"run_id": run_id} for run_id in run_ids],
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="canary",
        run_id=["run_a", "run_b"],
        max_mismatch_rate=0.25,
        max_friction_events=1,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "hold"
    assert payload["summary"]["run_count"] == 2


def test_eval_canary_allows_parser_default_shadow_without_candidate(monkeypatch, capsys):
    monkeypatch.setattr("supervisor.eval.run_canary_eval", lambda run_ids, **kwargs: {
        "decision": "hold",
        "summary": {
            "run_count": len(run_ids),
            "avg_pass_rate": 0.75,
            "mismatch_kinds": {"ux_only_divergence": 1},
            "friction": {"total_events": 1, "by_kind": {"repeated_confirmation": 1}, "by_signal": {}},
        },
        "runs": [{"run_id": run_id} for run_id in run_ids],
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="canary",
        run_id=["run_a"],
        candidate_id="",
        phase="shadow",
        max_mismatch_rate=0.25,
        max_friction_events=1,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "hold"
    assert payload["summary"]["run_count"] == 1


def test_eval_canary_json_output_can_record_rollout(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.run_canary_eval", lambda *args, **kwargs: {
        "run_ids": ["run_a"],
        "decision": "promote",
        "summary": {
            "run_count": 1,
            "decision_count": 4,
            "mismatch_count": 0,
            "mismatch_rate": 0.0,
            "avg_pass_rate": 1.0,
            "mismatch_kinds": {},
            "friction": {"total_events": 0, "by_kind": {}, "by_signal": {}},
        },
        "runs": [],
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="canary",
        run_id=["run_a"],
        candidate_id="candidate_demo",
        phase="shadow",
        max_mismatch_rate=0.25,
        max_friction_events=0,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rollout_record"]["candidate_id"] == "candidate_demo"
    assert payload["rollout_record"]["phase"] == "shadow"


def test_eval_canary_rejects_phase_without_candidate(monkeypatch, capsys):
    monkeypatch.setattr("supervisor.eval.run_canary_eval", lambda *args, **kwargs: {
        "run_ids": ["run_a"],
        "decision": "promote",
        "summary": {
            "run_count": 1,
            "decision_count": 4,
            "mismatch_count": 0,
            "mismatch_rate": 0.0,
            "avg_pass_rate": 1.0,
            "mismatch_kinds": {},
            "friction": {"total_events": 0, "by_kind": {}, "by_signal": {}},
        },
        "runs": [],
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="canary",
        run_id=["run_a"],
        candidate_id="",
        phase="limited",
        max_mismatch_rate=0.25,
        max_friction_events=0,
        output="",
        save_report=False,
        config=None,
        json=False,
    ))

    assert result == 1
    err = capsys.readouterr().err
    assert "--phase requires --candidate-id" in err


def test_eval_propose_saves_candidate_manifest_when_report_persisted(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()

    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.propose_candidate_policy", lambda *args, **kwargs: {
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "recommended_candidate_policy": "builtin-approval-strict-v1",
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 1},
        },
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="propose",
        suite="approval-core",
        suite_file=None,
        baseline_policy="builtin-approval-v1",
        objective="reduce_false_approval",
        output="",
        save_report=True,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_manifest_path"].endswith("candidate_demo.json")
    assert Path(payload["candidate_manifest_path"]).exists()


def test_eval_improve_dry_run_stops_after_proposal(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    manifest_path = tmp_path / ".supervisor" / "evals" / "candidates" / "candidate_demo.json"

    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.propose_candidate_policy", lambda *args, **kwargs: {
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "baseline_policy": "builtin-approval-v1",
        "recommended_candidate_policy": "builtin-approval-strict-v1",
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
        },
    })
    monkeypatch.setattr("supervisor.eval.save_candidate_manifest", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr("supervisor.eval.review_candidate_manifest", lambda manifest: {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "review_status": "needs_human_review",
        "next_action": "thin-supervisor-dev eval compare --suite approval-core --candidate-policy builtin-approval-strict-v1",
        "suite": "approval-core",
    })
    monkeypatch.setattr("supervisor.eval.build_candidate_dossier", lambda **kwargs: {
        "candidate": {"candidate_id": "candidate_demo", "candidate_policy": "builtin-approval-strict-v1"},
        "proposal": {"objective": "reduce_false_approval"},
        "review": {"review_status": "needs_human_review"},
        "next_action": "thin-supervisor-dev eval compare --suite approval-core --candidate-policy builtin-approval-strict-v1",
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="improve",
        suite="approval-core",
        suite_file=None,
        baseline_policy="builtin-approval-v1",
        objective="reduce_false_approval",
        run_id=[],
        approved_by="",
        force=False,
        dry_run=True,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "proposed"
    assert payload["candidate_id"] == "candidate_demo"
    assert payload["candidate_manifest_path"] == str(manifest_path)
    assert "gate" not in payload
    assert "promotion" not in payload


def test_eval_improve_stops_before_promote_without_approval(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    manifest_path = tmp_path / ".supervisor" / "evals" / "candidates" / "candidate_demo.json"

    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.propose_candidate_policy", lambda *args, **kwargs: {
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "baseline_policy": "builtin-approval-v1",
        "recommended_candidate_policy": "builtin-approval-strict-v1",
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
        },
    })
    monkeypatch.setattr("supervisor.eval.save_candidate_manifest", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr("supervisor.eval.review_candidate_manifest", lambda manifest: {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "review_status": "needs_human_review",
        "next_action": "thin-supervisor-dev eval compare --suite approval-core --candidate-policy builtin-approval-strict-v1",
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "touched_fragments": ["approval-boundary"],
    })
    monkeypatch.setattr("supervisor.eval.build_candidate_dossier", lambda **kwargs: {
        "candidate": {"candidate_id": "candidate_demo", "candidate_policy": "builtin-approval-strict-v1"},
        "proposal": {"objective": "reduce_false_approval"},
        "review": {"review_status": "needs_human_review"},
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })
    monkeypatch.setattr("supervisor.eval.evaluate_candidate_gate", lambda review, suite, canary_report=None: {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": "needs_human_review",
        "decision": "needs_canary",
        "compare": {},
        "canary": None,
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="improve",
        suite="approval-core",
        suite_file=None,
        baseline_policy="builtin-approval-v1",
        objective="reduce_false_approval",
        run_id=[],
        approved_by="",
        force=False,
        dry_run=False,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "gated"
    assert payload["gate"]["decision"] == "needs_canary"
    assert payload["next_action"] == "thin-supervisor-dev eval canary --run-id <recent_run>"
    assert "promotion" not in payload


def test_eval_improve_passes_canary_threshold_overrides(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    manifest_path = tmp_path / ".supervisor" / "evals" / "candidates" / "candidate_demo.json"
    captured: dict[str, object] = {}

    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.propose_candidate_policy", lambda *args, **kwargs: {
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "baseline_policy": "builtin-approval-v1",
        "recommended_candidate_policy": "builtin-approval-strict-v1",
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
        },
    })
    monkeypatch.setattr("supervisor.eval.save_candidate_manifest", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr("supervisor.eval.review_candidate_manifest", lambda manifest: {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "review_status": "needs_human_review",
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "touched_fragments": ["approval-boundary"],
    })
    monkeypatch.setattr("supervisor.eval.build_candidate_dossier", lambda **kwargs: {
        "candidate": {"candidate_id": "candidate_demo", "candidate_policy": "builtin-approval-strict-v1"},
        "proposal": {"objective": "reduce_false_approval"},
        "review": {"review_status": "needs_human_review"},
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })

    def _fake_canary(run_ids, *, runtime_dir, max_mismatch_rate, max_friction_events):
        captured["run_ids"] = list(run_ids)
        captured["runtime_dir"] = runtime_dir
        captured["max_mismatch_rate"] = max_mismatch_rate
        captured["max_friction_events"] = max_friction_events
        return {"decision": "needs_canary", "run_ids": list(run_ids)}

    monkeypatch.setattr("supervisor.eval.run_canary_eval", _fake_canary)
    monkeypatch.setattr("supervisor.eval.evaluate_candidate_gate", lambda review, suite, canary_report=None: {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": "needs_human_review",
        "decision": "needs_canary",
        "compare": {},
        "canary": canary_report,
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="improve",
        suite="approval-core",
        suite_file=None,
        baseline_policy="builtin-approval-v1",
        objective="reduce_false_approval",
        run_id=["run_1", "run_2"],
        max_mismatch_rate=0.1,
        max_friction_events=2,
        approved_by="",
        force=False,
        dry_run=False,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    assert captured == {
        "run_ids": ["run_1", "run_2"],
        "runtime_dir": str(runtime_dir),
        "max_mismatch_rate": 0.1,
        "max_friction_events": 2,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["gate"]["canary"]["run_ids"] == ["run_1", "run_2"]


def test_eval_improve_promotes_when_approved_and_gate_allows(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    manifest_path = tmp_path / ".supervisor" / "evals" / "candidates" / "candidate_demo.json"

    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.propose_candidate_policy", lambda *args, **kwargs: {
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "baseline_policy": "builtin-approval-v1",
        "recommended_candidate_policy": "builtin-approval-strict-v1",
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
        },
    })
    monkeypatch.setattr("supervisor.eval.save_candidate_manifest", lambda *args, **kwargs: manifest_path)
    monkeypatch.setattr("supervisor.eval.review_candidate_manifest", lambda manifest: {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "review_status": "needs_human_review",
        "next_action": "thin-supervisor-dev eval compare --suite approval-core --candidate-policy builtin-approval-strict-v1",
        "suite": "approval-core",
        "objective": "reduce_false_approval",
        "touched_fragments": ["approval-boundary"],
    })
    monkeypatch.setattr("supervisor.eval.build_candidate_dossier", lambda **kwargs: {
        "candidate": {"candidate_id": "candidate_demo", "candidate_policy": "builtin-approval-strict-v1"},
        "proposal": {"objective": "reduce_false_approval"},
        "review": {"review_status": "needs_human_review"},
        "next_action": "thin-supervisor-dev eval promote-candidate --candidate-id candidate_demo --approved-by human",
    })
    monkeypatch.setattr("supervisor.eval.run_canary_eval", lambda *args, **kwargs: {
        "decision": "promote",
        "summary": {
            "run_count": 1,
            "decision_count": 2,
            "mismatch_count": 0,
            "mismatch_rate": 0.0,
            "avg_pass_rate": 1.0,
            "mismatch_kinds": {},
            "friction": {"total_events": 0, "by_kind": {}, "by_signal": {}},
        },
        "runs": [{"run_id": "run_a"}],
    })
    monkeypatch.setattr("supervisor.eval.evaluate_candidate_gate", lambda review, suite, canary_report=None: {
        "candidate_id": "candidate_demo",
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": "needs_human_review",
        "decision": "promote",
        "compare": {},
        "canary": canary_report,
        "next_action": "thin-supervisor-dev eval promote-candidate --candidate-id candidate_demo --approved-by human",
    })
    monkeypatch.setattr("supervisor.eval.promote_candidate", lambda gate, **kwargs: {
        "candidate_id": gate["candidate_id"],
        "status": "promoted",
        "approved_by": kwargs["approved_by"],
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="improve",
        suite="approval-core",
        suite_file=None,
        baseline_policy="builtin-approval-v1",
        objective="reduce_false_approval",
        run_id=["run_a"],
        approved_by="human",
        force=False,
        dry_run=False,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "promoted"
    assert payload["promotion"]["status"] == "promoted"
    assert payload["promotion"]["approved_by"] == "human"


def test_eval_review_candidate_json_output(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    candidates_dir = tmp_path / ".supervisor" / "evals" / "candidates"
    candidates_dir.mkdir(parents=True)
    manifest = candidates_dir / "candidate_demo.json"
    manifest.write_text(json.dumps({
        "candidate_id": "candidate_demo",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": "builtin-approval-strict-v1",
            "rationale": "Conservative candidate for safety.",
        },
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [
                {
                    "fragment": "approval-boundary",
                    "path": "skills/thin-supervisor/strategy/approval-boundary.md",
                    "instructions": ["Require explicit execution verbs when prior context is weak."],
                }
            ],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }), encoding="utf-8")

    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)

    result = app.cmd_eval(argparse.Namespace(
        eval_action="review-candidate",
        candidate_id="candidate_demo",
        manifest="",
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_id"] == "candidate_demo"
    assert payload["review_status"] == "needs_human_review"
    assert payload["next_action"].startswith("thin-supervisor-dev eval compare")


def test_eval_candidate_status_json_output(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    candidates_dir = tmp_path / ".supervisor" / "evals" / "candidates"
    candidates_dir.mkdir(parents=True)
    (candidates_dir / "candidate_demo.json").write_text(json.dumps({
        "candidate_id": "candidate_demo",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": "builtin-approval-strict-v1",
            "rationale": "Conservative candidate for safety.",
        },
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }), encoding="utf-8")

    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.dossier.list_rollouts", lambda runtime_dir, candidate_id="": [
        {
            "candidate_id": "candidate_demo",
            "phase": "shadow",
            "decision": "promote",
            "saved_at": "2026-04-13T00:00:00+00:00",
            "run_ids": ["run_a"],
        }
    ])
    monkeypatch.setattr("supervisor.eval.dossier.current_rollouts", lambda history: {
        "candidate_demo": history[0]
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="candidate-status",
        candidate_id="candidate_demo",
        manifest="",
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate"]["candidate_id"] == "candidate_demo"
    assert payload["review"]["review_status"] == "needs_human_review"
    assert payload["rollouts"]["current"]["phase"] == "shadow"


def test_eval_gate_candidate_json_output(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    candidates_dir = tmp_path / ".supervisor" / "evals" / "candidates"
    candidates_dir.mkdir(parents=True)
    (candidates_dir / "candidate_demo.json").write_text(json.dumps({
        "candidate_id": "candidate_demo",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": "builtin-approval-strict-v1",
            "rationale": "Conservative candidate for safety.",
        },
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }), encoding="utf-8")

    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.evaluate_candidate_gate", lambda review, suite, canary_report=None: {
        "candidate_id": review["candidate_id"],
        "decision": "hold",
        "review_status": review["review_status"],
        "compare": {"summary": {"weighted_wins": {"baseline": 2.0, "candidate": 0.0, "tie": 0.0}}},
        "canary": None,
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="gate-candidate",
        candidate_id="candidate_demo",
        manifest="",
        run_id=[],
        max_mismatch_rate=0.25,
        max_friction_events=0,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_id"] == "candidate_demo"
    assert payload["decision"] == "hold"


def test_eval_promote_candidate_json_output(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    candidates_dir = tmp_path / ".supervisor" / "evals" / "candidates"
    candidates_dir.mkdir(parents=True)
    (candidates_dir / "candidate_demo.json").write_text(json.dumps({
        "candidate_id": "candidate_demo",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": "builtin-approval-strict-v1",
            "rationale": "Conservative candidate for safety.",
        },
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }), encoding="utf-8")

    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.evaluate_candidate_gate", lambda review, suite, canary_report=None: {
        "candidate_id": review["candidate_id"],
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": "needs_human_review",
        "decision": "needs_canary",
        "compare": {},
        "canary": None,
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="promote-candidate",
        candidate_id="candidate_demo",
        manifest="",
        approved_by="human",
        force=False,
        run_id=[],
        max_mismatch_rate=0.25,
        max_friction_events=0,
        output="",
        save_report=False,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "promoted"
    assert payload["approved_by"] == "human"


def test_eval_gate_candidate_can_save_report(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    candidates_dir = tmp_path / ".supervisor" / "evals" / "candidates"
    candidates_dir.mkdir(parents=True)
    (candidates_dir / "candidate_demo.json").write_text(json.dumps({
        "candidate_id": "candidate_demo",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": "builtin-approval-strict-v1",
            "rationale": "Conservative candidate for safety.",
        },
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }), encoding="utf-8")

    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.evaluate_candidate_gate", lambda review, suite, canary_report=None: {
        "candidate_id": review["candidate_id"],
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": review["review_status"],
        "decision": "needs_canary",
        "compare": {},
        "canary": None,
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="gate-candidate",
        candidate_id="candidate_demo",
        manifest="",
        run_id=[],
        max_mismatch_rate=0.25,
        max_friction_events=0,
        output="",
        save_report=True,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_path"].endswith(".json")
    assert Path(payload["report_path"]).exists()


def test_eval_promote_candidate_can_save_report(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    candidates_dir = tmp_path / ".supervisor" / "evals" / "candidates"
    candidates_dir.mkdir(parents=True)
    (candidates_dir / "candidate_demo.json").write_text(json.dumps({
        "candidate_id": "candidate_demo",
        "proposal": {
            "suite": "approval-core",
            "objective": "reduce_false_approval",
            "baseline_policy": "builtin-approval-v1",
            "recommended_candidate_policy": "builtin-approval-strict-v1",
            "rationale": "Conservative candidate for safety.",
        },
        "candidate": {
            "candidate_id": "candidate_demo",
            "candidate_policy": "builtin-approval-strict-v1",
            "parent_id": "builtin-approval-v1",
            "objective": "reduce_false_approval",
            "touched_fragments": ["approval-boundary"],
            "mutation_operator": "tighten_positive_boundary",
            "fragment_mutations": [],
            "originating_evidence": {"suite": "approval-core", "failure_case_count": 2},
        },
    }), encoding="utf-8")

    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.load_eval_suite", lambda ref: object())
    monkeypatch.setattr("supervisor.eval.evaluate_candidate_gate", lambda review, suite, canary_report=None: {
        "candidate_id": review["candidate_id"],
        "candidate_policy": "builtin-approval-strict-v1",
        "baseline_policy": "builtin-approval-v1",
        "suite": "approval-core",
        "review_status": "needs_human_review",
        "decision": "needs_canary",
        "compare": {},
        "canary": None,
        "next_action": "thin-supervisor-dev eval canary --run-id <recent_run>",
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="promote-candidate",
        candidate_id="candidate_demo",
        manifest="",
        approved_by="human",
        force=True,
        run_id=[],
        max_mismatch_rate=0.25,
        max_friction_events=0,
        output="",
        save_report=True,
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_path"].endswith(".json")
    assert Path(payload["report_path"]).exists()


def test_eval_promotion_history_json_output(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.list_promotions", lambda runtime_dir: [
        {"candidate_id": "candidate_a", "suite": "approval-core", "status": "promoted", "promoted_at": "2026-04-13T00:00:00+00:00"}
    ])
    monkeypatch.setattr("supervisor.eval.current_promotions", lambda history: {
        "approval-core": history[0]
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="promotion-history",
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["history"][0]["candidate_id"] == "candidate_a"
    assert payload["current"]["approval-core"]["status"] == "promoted"


def test_eval_rollout_history_json_output(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.list_rollouts", lambda runtime_dir, candidate_id="": [
        {
            "candidate_id": "candidate_demo",
            "phase": "shadow",
            "decision": "promote",
            "saved_at": "2026-04-13T00:00:00+00:00",
            "run_ids": ["run_a"],
        }
    ])
    monkeypatch.setattr("supervisor.eval.current_rollouts", lambda history: {
        "candidate_demo": history[0]
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="rollout-history",
        candidate_id="candidate_demo",
        config=None,
        json=True,
    ))

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["history"][0]["candidate_id"] == "candidate_demo"
    assert payload["current"]["candidate_demo"]["phase"] == "shadow"


def test_eval_promotion_history_plain_output_tolerates_sparse_records(tmp_path, monkeypatch, capsys):
    runtime_dir = tmp_path / ".supervisor" / "runtime"
    cfg = type("Cfg", (), {"runtime_dir": str(runtime_dir)})()
    monkeypatch.setattr("supervisor.app.RuntimeConfig.load", lambda path=None: cfg)
    monkeypatch.setattr("supervisor.eval.list_promotions", lambda runtime_dir: [
        {"suite": "approval-core", "candidate_id": "candidate_a", "status": "promoted"},
        {"suite": "routing-core"},
    ])
    monkeypatch.setattr("supervisor.eval.current_promotions", lambda history: {
        "approval-core": history[0],
        "routing-core": history[1],
    })

    result = app.cmd_eval(argparse.Namespace(
        eval_action="promotion-history",
        config=None,
        json=False,
    ))

    assert result == 0
    out = capsys.readouterr().out
    assert "approval-core: candidate_a (promoted)" in out
    assert "routing-core: ? (?)" in out


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


def test_skill_install_dedupes_shared_skill_root_and_removes_legacy_alias(
    tmp_path, monkeypatch, capsys,
):
    home = tmp_path / "home"
    shared_skills = home / ".skills"
    shared_skills.mkdir(parents=True)

    codex_home = home / ".codex"
    codex_home.mkdir()
    (codex_home / "skills").symlink_to(shared_skills, target_is_directory=True)

    claude_home = home / ".claude"
    claude_home.mkdir()
    (claude_home / "skills").symlink_to(shared_skills, target_is_directory=True)

    legacy = shared_skills / "lh-supervisor"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text(
        "---\nname: thin-supervisor\nuser-invocable: true\n---\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(app.Path, "home", staticmethod(lambda: home))

    result = app.cmd_skill_install(argparse.Namespace())

    assert result == 0
    assert (shared_skills / "thin-supervisor" / "SKILL.md").exists()
    assert not legacy.exists()

    visible_names = []
    for skill_file in shared_skills.glob("*/SKILL.md"):
        text = skill_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("name:"):
                visible_names.append(line.split(":", 1)[1].strip())
                break
    assert visible_names.count("thin-supervisor") == 1


# ─────────────────────────────────────────────────────────────────
# Task 3: status is global-first across worktrees
#
# Per docs/plans/2026-04-16-global-observability-plane-for-per-worktree-runtime.md:
#   - `status` must route through collect_sessions() so runs in OTHER
#     worktrees (known_worktrees, live daemon cwds, pane-owner cwds) show up
#   - `status --local` restricts to the current cwd only
#   - output must print the worktree root explicitly when the run is not in cwd
# ─────────────────────────────────────────────────────────────────


def _write_paused_state_in(worktree: Path, *, run_id: str) -> None:
    run_dir = worktree / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "top_state": "PAUSED_FOR_HUMAN",
        "current_node_id": "step_paused",
        "pane_target": "%22",
        "spec_path": "/tmp/spec.yaml",
        "surface_type": "tmux",
        "controller_mode": "daemon",
        "human_escalations": [{"reason": "needs human review"}],
    }))


def _write_running_state_in(worktree: Path, *, run_id: str,
                            controller_mode: str = "daemon") -> None:
    run_dir = worktree / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "top_state": "RUNNING",
        "current_node_id": "step_run",
        "pane_target": "%33",
        "spec_path": "/tmp/spec.yaml",
        "surface_type": "tmux",
        "controller_mode": controller_mode,
    }))


def _write_completed_state_in(worktree: Path, *, run_id: str) -> None:
    run_dir = worktree / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "top_state": "COMPLETED",
        "current_node_id": "verify",
        "pane_target": "%44",
        "spec_path": "/tmp/spec.yaml",
        "surface_type": "tmux",
    }))


def _patch_session_index_registries(monkeypatch, *,
                                     known_worktrees=(),
                                     daemons=(),
                                     pane_owners=()) -> None:
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_known_worktrees",
        lambda: list(known_worktrees),
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_daemons",
        lambda: list(daemons),
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index.list_pane_owners",
        lambda: list(pane_owners),
    )
    monkeypatch.setattr(
        "supervisor.operator.session_index._discover_git_worktrees",
        lambda cwd: [],
    )


def test_status_empty_with_live_daemon_elsewhere_does_not_claim_local_daemon_down(
    tmp_path, monkeypatch, capsys,
):
    """Empty-state fallback must not probe a cwd-local DaemonClient.

    If another worktree has a live daemon but our cwd has no local
    daemon and no runs exist anywhere, the old fallback printed
    "No runs found. Daemon not running." — which is false.  The
    global-first contract requires consulting the global daemon
    registry, not a cwd-local probe.
    """
    other = tmp_path / "other"
    cwd = tmp_path / "cwd"
    other.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    # Poison cwd-local DaemonClient so the old code path would have
    # reported "Daemon not running" — if cmd_status consults it, this
    # test fails loudly.
    class _BoomClient:
        def is_running(self):
            raise AssertionError(
                "cmd_status must not probe cwd-local DaemonClient in "
                "the empty-state branch"
            )

    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _BoomClient)

    # Global daemon registry shows a live daemon in a different worktree;
    # no runs anywhere (neither cwd nor `other` has state.json files).
    live_daemons = [{
        "pid": 42, "cwd": str(other),
        "socket": "/tmp/other.sock", "active_runs": 0,
    }]
    _patch_session_index_registries(monkeypatch, daemons=live_daemons)
    monkeypatch.setattr(app, "_list_global_daemons", lambda: list(live_daemons))

    result = app.cmd_status(argparse.Namespace(config=None, local=False))

    assert result == 0
    out = capsys.readouterr().out
    assert "Daemon not running" not in out
    assert "Daemons running" in out


def test_status_empty_with_no_daemons_anywhere_says_no_daemons(
    tmp_path, monkeypatch, capsys,
):
    """No runs + no live daemons anywhere → explicit 'No daemons running.'"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _patch_session_index_registries(monkeypatch)  # all empty

    result = app.cmd_status(argparse.Namespace(config=None, local=False))

    assert result == 0
    out = capsys.readouterr().out
    assert "No runs found" in out
    assert "No daemons running" in out


def test_status_shows_orphaned_run_from_child_worktree(
    tmp_path, monkeypatch, capsys,
):
    """Root cwd must see a child worktree's orphaned paused run."""
    root = tmp_path / "root"
    child = tmp_path / "child"
    root.mkdir()
    child.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _write_paused_state_in(child, run_id="run_child_paused")
    _patch_session_index_registries(
        monkeypatch, known_worktrees=[str(child)],
    )

    result = app.cmd_status(argparse.Namespace(config=None, local=False))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_child_paused" in out
    # Worktree root must be printed explicitly (not cwd)
    assert str(child.resolve()) in out


def test_status_local_flag_restricts_to_current_worktree(
    tmp_path, monkeypatch, capsys,
):
    """`status --local` must hide runs from other worktrees."""
    root = tmp_path / "root"
    child = tmp_path / "child"
    root.mkdir()
    child.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _write_paused_state_in(root, run_id="run_in_root")
    _write_paused_state_in(child, run_id="run_in_child")
    _patch_session_index_registries(
        monkeypatch, known_worktrees=[str(child)],
    )

    result = app.cmd_status(argparse.Namespace(config=None, local=True))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_in_root" in out
    assert "run_in_child" not in out


def test_status_buckets_remain_stable_across_worktrees(
    tmp_path, monkeypatch, capsys,
):
    """Daemon / orphaned / completed buckets must survive global scan."""
    root = tmp_path / "root"
    wt_a = tmp_path / "wt_a"
    wt_b = tmp_path / "wt_b"
    wt_c = tmp_path / "wt_c"
    for p in (root, wt_a, wt_b, wt_c):
        p.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)

    _write_running_state_in(wt_a, run_id="run_daemon_live")
    _write_paused_state_in(wt_b, run_id="run_orphan_paused")
    _write_completed_state_in(wt_c, run_id="run_done_elsewhere")

    _patch_session_index_registries(
        monkeypatch,
        known_worktrees=[str(wt_a), str(wt_b), str(wt_c)],
        daemons=[{
            "pid": 1,
            "cwd": str(wt_a.resolve()),
            "socket": "/tmp/a.sock",
            "active_runs": 1,
        }],
    )

    result = app.cmd_status(argparse.Namespace(config=None, local=False))

    assert result == 0
    out = capsys.readouterr().out
    # All three runs surface
    assert "run_daemon_live" in out
    assert "run_orphan_paused" in out
    assert "run_done_elsewhere" in out
    # Bucket labels still render
    assert "Active runs" in out
    assert "Orphaned" in out or "orphaned" in out.lower()
    assert "Recently completed" in out


def test_status_default_includes_global_worktrees(
    tmp_path, monkeypatch, capsys,
):
    """Without --local, status scans all known worktrees."""
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _write_running_state_in(other, run_id="run_elsewhere")
    _patch_session_index_registries(
        monkeypatch, known_worktrees=[str(other)],
    )

    result = app.cmd_status(argparse.Namespace(config=None, local=False))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_elsewhere" in out
    assert str(other.resolve()) in out


# ─────────────────────────────────────────────────────────────────
# Task 5: observe works for orphaned runs without a live daemon
#
# Per docs/plans/2026-04-16-global-observability-plane-for-per-worktree-runtime.md:
#   - resolve run globally (find_session)
#   - use daemon RPC if live
#   - otherwise build response from local state + session log
# ─────────────────────────────────────────────────────────────────


def _write_observe_state_in(worktree: Path, *, run_id: str,
                            top_state: str = "PAUSED_FOR_HUMAN") -> None:
    run_dir = worktree / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "spec_id": "phase_x",
        "top_state": top_state,
        "current_node_id": "step_observed",
        "current_attempt": 2,
        "done_node_ids": ["step_1", "step_2"],
        "pane_target": "%11",
        "spec_path": "/tmp/spec.yaml",
        "surface_type": "tmux",
        "controller_mode": "daemon",
        "workspace_root": str(worktree),
        "human_escalations": [{"reason": "needs human review"}],
    }))
    # Session log with one event so timeline_from_session_log returns something
    (run_dir / "session_log.jsonl").write_text(json.dumps({
        "run_id": run_id,
        "seq": 1,
        "event_type": "checkpoint",
        "timestamp": "2026-04-16T10:00:00Z",
        "payload": {"note": "observed event"},
    }) + "\n")


def test_observe_works_for_orphaned_run_without_daemon(
    tmp_path, monkeypatch, capsys,
):
    """observe must read state+events from disk when no daemon is running."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _write_observe_state_in(tmp_path, run_id="run_orphan_observe")
    _patch_session_index_registries(monkeypatch)

    result = app.cmd_observe(argparse.Namespace(run_id="run_orphan_observe"))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_orphan_observe" in out
    assert "PAUSED_FOR_HUMAN" in out
    assert "step_observed" in out


def test_observe_resolves_run_in_child_worktree(
    tmp_path, monkeypatch, capsys,
):
    """observe must find a run by id across known_worktrees from root cwd."""
    root = tmp_path / "root"
    child = tmp_path / "child"
    root.mkdir()
    child.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _write_observe_state_in(child, run_id="run_in_child_wt")
    _patch_session_index_registries(
        monkeypatch, known_worktrees=[str(child)],
    )

    result = app.cmd_observe(argparse.Namespace(run_id="run_in_child_wt"))

    assert result == 0
    out = capsys.readouterr().out
    assert "run_in_child_wt" in out
    assert "step_observed" in out


def test_observe_returns_error_for_unknown_run(
    tmp_path, monkeypatch, capsys,
):
    """observe must fail cleanly when the run id does not resolve."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("supervisor.daemon.client.DaemonClient", _DaemonStopped)
    _patch_session_index_registries(monkeypatch)

    result = app.cmd_observe(argparse.Namespace(run_id="run_does_not_exist"))

    assert result == 1
    out = capsys.readouterr().out
    assert "run_does_not_exist" in out or "not found" in out.lower()
