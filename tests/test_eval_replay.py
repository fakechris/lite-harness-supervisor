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
    assert report["summary"]["mismatch_kinds"] == {}
    assert report["summary"]["pass_rate"] == 1.0


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
