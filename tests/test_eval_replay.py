from __future__ import annotations

import json
from pathlib import Path


def _build_replay_workspace(tmp_path: Path) -> tuple[Path, str]:
    run_id = "run_eval_replay"
    spec_path = tmp_path / ".supervisor" / "specs" / "demo.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: demo\n"
        "goal: replay test\n"
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
        "last_agent_checkpoint": {
            "status": "workflow_done",
            "current_node": "first",
            "summary": "done",
            "checkpoint_seq": 1,
        },
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
        {
            "run_id": run_id,
            "seq": 1,
            "event_type": "checkpoint",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": {
                "checkpoint_id": "cp_1",
                "run_id": run_id,
                "checkpoint_seq": 1,
                "status": "workflow_done",
                "current_node": "first",
                "summary": "done",
            },
        },
        {
            "run_id": run_id,
            "seq": 2,
            "event_type": "gate_decision",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": decision,
        },
        {
            "run_id": run_id,
            "seq": 3,
            "event_type": "verification",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": {"ok": True, "results": [{"type": "command", "ok": True, "command": "echo ok"}]},
        },
    ]
    (run_dir / "session_log.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    shared = tmp_path / ".supervisor" / "runtime" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "notes.jsonl").write_text("", encoding="utf-8")
    (shared / "friction_events.jsonl").write_text("", encoding="utf-8")
    (shared / "user_preferences.json").write_text(json.dumps({"default": {}}), encoding="utf-8")
    return tmp_path, run_id


def test_run_replay_eval_returns_non_regression_summary(tmp_path, monkeypatch):
    workspace, run_id = _build_replay_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    from supervisor.eval.replay import run_replay_eval

    report = run_replay_eval(run_id)

    assert report["run_id"] == run_id
    assert report["summary"]["decision_count"] == 1
    assert report["summary"]["matched_count"] == 1
    assert report["summary"]["mismatch_count"] == 0
    assert report["summary"]["pass_rate"] == 1.0
    assert report["summary"]["mismatch_kinds"] == {}
    assert report["summary"]["friction"]["total_events"] == 0


def test_run_replay_eval_classifies_mismatch_kinds(tmp_path, monkeypatch):
    workspace, run_id = _build_replay_workspace(tmp_path)
    run_dir = workspace / ".supervisor" / "runtime" / "runs" / run_id
    decision = {
        "decision_id": "dec_1",
        "decision": "ESCALATE_TO_HUMAN",
        "reason": "checkpoint says blocked",
        "confidence": 1.0,
        "needs_human": True,
        "timestamp": "2026-04-12T00:00:01Z",
        "gate_type": "checkpoint_status",
        "triggered_by_seq": 1,
        "triggered_by_checkpoint_id": "cp_1",
        "next_instruction": None,
        "selected_branch": None,
        "next_node_id": None,
    }
    events = [
        {
            "run_id": run_id,
            "seq": 1,
            "event_type": "checkpoint",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": {
                "checkpoint_id": "cp_1",
                "run_id": run_id,
                "checkpoint_seq": 1,
                "status": "workflow_done",
                "current_node": "first",
                "summary": "done",
            },
        },
        {
            "run_id": run_id,
            "seq": 2,
            "event_type": "gate_decision",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": decision,
        },
    ]
    (run_dir / "decision_log.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")
    (run_dir / "session_log.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    from supervisor.eval.replay import run_replay_eval

    report = run_replay_eval(run_id)

    assert report["summary"]["mismatch_count"] == 1
    assert report["summary"]["mismatch_kinds"]["safety_regression"] == 1


def test_run_replay_eval_includes_friction_summary(tmp_path, monkeypatch):
    workspace, run_id = _build_replay_workspace(tmp_path)
    shared = workspace / ".supervisor" / "runtime" / "shared"
    (shared / "friction_events.jsonl").write_text(
        json.dumps(
            {
                "event_id": "friction_1",
                "timestamp": "2026-04-12T00:00:02Z",
                "kind": "repeated_confirmation",
                "message": "user had to approve twice",
                "run_id": run_id,
                "user_id": "default",
                "signals": ["user_repeated_approval"],
                "metadata": {},
            }
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    from supervisor.eval.replay import run_replay_eval

    report = run_replay_eval(run_id)

    assert report["summary"]["friction"]["total_events"] == 1
    assert report["summary"]["friction"]["by_kind"]["repeated_confirmation"] == 1


def test_run_replay_eval_handles_pause_then_resume_history(tmp_path, monkeypatch):
    workspace, run_id = _build_replay_workspace(tmp_path)
    run_dir = workspace / ".supervisor" / "runtime" / "runs" / run_id
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["top_state"] = "RUNNING"
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    decision_1 = {
        "decision_id": "dec_1",
        "decision": "VERIFY_STEP",
        "reason": "checkpoint says step_done",
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
    decision_2 = {
        "decision_id": "dec_2",
        "decision": "VERIFY_STEP",
        "reason": "checkpoint says step_done",
        "confidence": 1.0,
        "needs_human": False,
        "timestamp": "2026-04-12T00:00:02Z",
        "gate_type": "checkpoint_status",
        "triggered_by_seq": 2,
        "triggered_by_checkpoint_id": "cp_2",
        "next_instruction": None,
        "selected_branch": None,
        "next_node_id": None,
    }
    decision_3 = {
        "decision_id": "dec_3",
        "decision": "VERIFY_STEP",
        "reason": "checkpoint says step_done",
        "confidence": 1.0,
        "needs_human": False,
        "timestamp": "2026-04-12T00:00:03Z",
        "gate_type": "checkpoint_status",
        "triggered_by_seq": 3,
        "triggered_by_checkpoint_id": "cp_3",
        "next_instruction": None,
        "selected_branch": None,
        "next_node_id": None,
    }
    decision_4 = {
        "decision_id": "dec_4",
        "decision": "VERIFY_STEP",
        "reason": "checkpoint says workflow_done",
        "confidence": 1.0,
        "needs_human": False,
        "timestamp": "2026-04-12T00:00:04Z",
        "gate_type": "checkpoint_status",
        "triggered_by_seq": 4,
        "triggered_by_checkpoint_id": "cp_4",
        "next_instruction": None,
        "selected_branch": None,
        "next_node_id": None,
    }
    events = [
        {
            "run_id": run_id,
            "seq": 1,
            "event_type": "checkpoint",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": {
                "checkpoint_id": "cp_1",
                "run_id": run_id,
                "checkpoint_seq": 1,
                "status": "step_done",
                "current_node": "first",
                "summary": "first retry",
            },
        },
        {
            "run_id": run_id,
            "seq": 2,
            "event_type": "gate_decision",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": decision_1,
        },
        {
            "run_id": run_id,
            "seq": 3,
            "event_type": "verification",
            "timestamp": "2026-04-12T00:00:01Z",
            "payload": {"ok": False, "results": [{"type": "command", "ok": False, "command": "echo nope"}]},
        },
        {
            "run_id": run_id,
            "seq": 4,
            "event_type": "checkpoint",
            "timestamp": "2026-04-12T00:00:02Z",
            "payload": {
                "checkpoint_id": "cp_2",
                "run_id": run_id,
                "checkpoint_seq": 2,
                "status": "step_done",
                "current_node": "first",
                "summary": "second retry",
            },
        },
        {
            "run_id": run_id,
            "seq": 5,
            "event_type": "gate_decision",
            "timestamp": "2026-04-12T00:00:02Z",
            "payload": decision_2,
        },
        {
            "run_id": run_id,
            "seq": 6,
            "event_type": "verification",
            "timestamp": "2026-04-12T00:00:02Z",
            "payload": {"ok": False, "results": [{"type": "command", "ok": False, "command": "echo nope"}]},
        },
        {
            "run_id": run_id,
            "seq": 7,
            "event_type": "checkpoint",
            "timestamp": "2026-04-12T00:00:03Z",
            "payload": {
                "checkpoint_id": "cp_3",
                "run_id": run_id,
                "checkpoint_seq": 3,
                "status": "step_done",
                "current_node": "first",
                "summary": "third retry",
            },
        },
        {
            "run_id": run_id,
            "seq": 8,
            "event_type": "gate_decision",
            "timestamp": "2026-04-12T00:00:03Z",
            "payload": decision_3,
        },
        {
            "run_id": run_id,
            "seq": 9,
            "event_type": "verification",
            "timestamp": "2026-04-12T00:00:03Z",
            "payload": {"ok": False, "results": [{"type": "command", "ok": False, "command": "echo nope"}]},
        },
        {
            "run_id": run_id,
            "seq": 10,
            "event_type": "human_pause",
            "timestamp": "2026-04-12T00:00:03Z",
            "payload": {"reason": "verification retry budget exhausted"},
        },
        {
            "run_id": run_id,
            "seq": 11,
            "event_type": "resume_requested",
            "timestamp": "2026-04-12T00:00:04Z",
            "payload": {"reason": "user resumed"},
        },
        {
            "run_id": run_id,
            "seq": 12,
            "event_type": "checkpoint",
            "timestamp": "2026-04-12T00:00:04Z",
            "payload": {
                "checkpoint_id": "cp_4",
                "run_id": run_id,
                "checkpoint_seq": 4,
                "status": "workflow_done",
                "current_node": "first",
                "summary": "done after resume",
            },
        },
        {
            "run_id": run_id,
            "seq": 13,
            "event_type": "gate_decision",
            "timestamp": "2026-04-12T00:00:04Z",
            "payload": decision_4,
        },
        {
            "run_id": run_id,
            "seq": 14,
            "event_type": "verification",
            "timestamp": "2026-04-12T00:00:04Z",
            "payload": {"ok": True, "results": [{"type": "command", "ok": True, "command": "echo ok"}]},
        },
    ]
    (run_dir / "decision_log.jsonl").write_text(
        "\n".join(json.dumps(item) for item in [decision_1, decision_2, decision_3, decision_4]) + "\n",
        encoding="utf-8",
    )
    (run_dir / "session_log.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    from supervisor.eval.replay import run_replay_eval

    report = run_replay_eval(run_id)

    assert report["summary"]["decision_count"] == 4
    assert report["summary"]["matched_count"] == 4
    assert report["summary"]["mismatch_count"] == 0
    assert report["summary"]["pass_rate"] == 1.0
