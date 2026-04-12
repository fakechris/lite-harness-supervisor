from __future__ import annotations

import json
from pathlib import Path
import hashlib

import pytest


def _build_history_workspace(tmp_path: Path) -> tuple[Path, str]:
    run_id = "run_demo123"
    spec_path = tmp_path / ".supervisor" / "specs" / "demo.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "kind: linear_plan\n"
        "id: demo\n"
        "goal: history test\n"
        "steps:\n"
        "  - id: write_test\n"
        "    type: task\n"
        "    objective: write tests\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
        "  - id: implement_feature\n"
        "    type: task\n"
        "    objective: implement\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
        "  - id: final_check\n"
        "    type: task\n"
        "    objective: final\n"
        "    verify:\n"
        "      - type: command\n"
        "        run: echo ok\n"
        "        expect: pass\n"
    )

    run_dir = tmp_path / ".supervisor" / "runtime" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "spec_id": "demo",
        "mode": "linear_plan",
        "top_state": "COMPLETED",
        "current_node_id": "final_check",
        "current_attempt": 0,
        "done_node_ids": ["write_test", "implement_feature", "final_check"],
        "branch_history": [],
        "human_escalations": [],
        "retry_budget": {"per_node": 3, "global_limit": 12, "used_global": 0},
        "last_agent_checkpoint": {
            "status": "workflow_done",
            "current_node": "final_check",
            "summary": "suite passed",
            "checkpoint_seq": 3,
        },
        "checkpoint_seq": 3,
        "verification": {"ok": True, "results": [{"type": "command", "ok": True}]},
        "last_event": {},
        "spec_path": str(spec_path),
        "spec_hash": "deadbeef",
        "pane_target": "%1",
        "surface_type": "tmux",
        "workspace_root": str(tmp_path),
        "completed_reviews": ["human"],
        "last_injected_node_id": "final_check",
        "last_injected_attempt": 0,
    }, ensure_ascii=False, indent=2))

    decisions = [
        {"decision_id": "dec_1", "decision": "VERIFY_STEP", "reason": "checkpoint says step_done", "confidence": 1.0, "needs_human": False, "timestamp": "2026-04-12T00:00:01Z", "gate_type": "checkpoint_status", "triggered_by_seq": 1, "triggered_by_checkpoint_id": "cp_1", "next_instruction": None, "selected_branch": None, "next_node_id": None},
        {"decision_id": "dec_2", "decision": "VERIFY_STEP", "reason": "checkpoint says step_done", "confidence": 1.0, "needs_human": False, "timestamp": "2026-04-12T00:00:02Z", "gate_type": "checkpoint_status", "triggered_by_seq": 2, "triggered_by_checkpoint_id": "cp_2", "next_instruction": None, "selected_branch": None, "next_node_id": None},
        {"decision_id": "dec_3", "decision": "VERIFY_STEP", "reason": "checkpoint says workflow_done", "confidence": 1.0, "needs_human": False, "timestamp": "2026-04-12T00:00:03Z", "gate_type": "checkpoint_status", "triggered_by_seq": 3, "triggered_by_checkpoint_id": "cp_3", "next_instruction": None, "selected_branch": None, "next_node_id": None},
    ]
    (run_dir / "decision_log.jsonl").write_text(
        "\n".join(json.dumps(item) for item in decisions) + "\n",
        encoding="utf-8",
    )

    session_events = [
        {"run_id": run_id, "seq": 1, "event_type": "checkpoint", "timestamp": "2026-04-12T00:00:01Z", "payload": {"checkpoint_id": "cp_1", "run_id": run_id, "checkpoint_seq": 1, "status": "step_done", "current_node": "write_test", "summary": "tests written"}},
        {"run_id": run_id, "seq": 2, "event_type": "gate_decision", "timestamp": "2026-04-12T00:00:01Z", "payload": decisions[0]},
        {"run_id": run_id, "seq": 3, "event_type": "verification", "timestamp": "2026-04-12T00:00:01Z", "payload": {"ok": True, "results": [{"type": "command", "ok": True, "command": "echo ok"}]}},
        {"run_id": run_id, "seq": 4, "event_type": "checkpoint", "timestamp": "2026-04-12T00:00:02Z", "payload": {"checkpoint_id": "cp_2", "run_id": run_id, "checkpoint_seq": 2, "status": "step_done", "current_node": "implement_feature", "summary": "implementation done"}},
        {"run_id": run_id, "seq": 5, "event_type": "gate_decision", "timestamp": "2026-04-12T00:00:02Z", "payload": decisions[1]},
        {"run_id": run_id, "seq": 6, "event_type": "verification", "timestamp": "2026-04-12T00:00:02Z", "payload": {"ok": True, "results": [{"type": "command", "ok": True, "command": "echo ok"}]}},
        {"run_id": run_id, "seq": 7, "event_type": "checkpoint", "timestamp": "2026-04-12T00:00:03Z", "payload": {"checkpoint_id": "cp_3", "run_id": run_id, "checkpoint_seq": 3, "status": "workflow_done", "current_node": "final_check", "summary": "suite passed"}},
        {"run_id": run_id, "seq": 8, "event_type": "gate_decision", "timestamp": "2026-04-12T00:00:03Z", "payload": decisions[2]},
        {"run_id": run_id, "seq": 9, "event_type": "verification", "timestamp": "2026-04-12T00:00:03Z", "payload": {"ok": True, "results": [{"type": "command", "ok": True, "command": "echo ok"}]}},
        {"run_id": run_id, "seq": 10, "event_type": "routing", "timestamp": "2026-04-12T00:00:04Z", "payload": {"routing_id": "rt_1", "target_type": "human", "scope": "single_question", "reason": "need review", "triggered_by_decision_id": "dec_3", "consultation_id": "oracle_123"}},
    ]
    (run_dir / "session_log.jsonl").write_text(
        "\n".join(json.dumps(item) for item in session_events) + "\n",
        encoding="utf-8",
    )

    shared = tmp_path / ".supervisor" / "runtime" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "notes.jsonl").write_text(
        "\n".join([
            json.dumps({
                "note_id": "note_1",
                "timestamp": "2026-04-12T00:00:01Z",
                "author_run_id": run_id,
                "note_type": "context",
                "title": "context",
                "content": "keep tests narrow",
                "metadata": {},
            }),
            json.dumps({
                "note_id": "note_2",
                "timestamp": "2026-04-12T00:00:02Z",
                "author_run_id": run_id,
                "note_type": "oracle",
                "title": "oracle",
                "content": "independent review",
                "metadata": {
                    "consultation_id": "oracle_123",
                    "provider": "openai",
                    "model_name": "o3",
                    "mode": "review",
                },
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    return tmp_path, run_id


def test_export_run_includes_state_logs_and_notes(tmp_path, monkeypatch):
    workspace, run_id = _build_history_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    from supervisor.history import export_run

    exported = export_run(run_id)

    assert exported["schema_version"] == "run_export.v1"
    assert exported["run_id"] == run_id
    assert exported["state"]["spec_id"] == "demo"
    assert len(exported["decision_log"]) == 3
    assert len(exported["session_log"]) == 10
    assert len(exported["notes"]) == 2
    assert "spec_snapshot" in exported
    assert exported["spec_snapshot"]["path"].endswith("demo.yaml")
    assert exported["spec_snapshot"]["hash"] == hashlib.sha256(
        exported["spec_snapshot"]["content"].encode("utf-8")
    ).hexdigest()[:16]


def test_summarize_run_reports_counts_and_oracle_links(tmp_path, monkeypatch):
    workspace, run_id = _build_history_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    from supervisor.history import export_run, summarize_run

    summary = summarize_run(export_run(run_id))

    assert summary["run_id"] == run_id
    assert summary["top_state"] == "COMPLETED"
    assert summary["counts"]["checkpoints"] == 3
    assert summary["counts"]["verifications_ok"] == 3
    assert summary["counts"]["routing_events"] == 1
    assert summary["counts"]["oracle_notes"] == 1
    assert summary["oracle_consultation_ids"] == ["oracle_123"]


def test_replay_run_recomputes_decisions_without_injection(tmp_path, monkeypatch):
    workspace, run_id = _build_history_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    from supervisor.history import export_run, replay_run

    replay = replay_run(export_run(run_id))

    assert replay["run_id"] == run_id
    assert replay["decision_count"] == 3
    assert replay["matched_count"] == 3
    assert replay["mismatches"] == []


def test_render_postmortem_generates_markdown(tmp_path, monkeypatch):
    workspace, run_id = _build_history_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    from supervisor.history import export_run, render_postmortem

    markdown = render_postmortem(export_run(run_id))

    assert f"# Run Postmortem: {run_id}" in markdown
    assert "Top state: `COMPLETED`" in markdown
    assert "Oracle consultations: `oracle_123`" in markdown
    assert "Routing events: 1" in markdown


def test_replay_run_uses_exported_spec_snapshot_when_worktree_spec_changes(tmp_path, monkeypatch):
    workspace, run_id = _build_history_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    from supervisor.history import export_run, replay_run

    exported = export_run(run_id)
    spec_path = Path(exported["state"]["spec_path"])
    spec_path.write_text(
        "kind: linear_plan\nid: demo\ngoal: changed\nsteps:\n"
        "  - id: other\n    type: task\n    objective: mismatch\n"
        "    verify:\n      - type: command\n        run: echo nope\n        expect: pass\n"
    )

    replay = replay_run(exported)

    assert replay["matched_count"] == 3


def test_replay_run_uses_exported_spec_snapshot_when_spec_file_is_missing(tmp_path, monkeypatch):
    workspace, run_id = _build_history_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    from supervisor.history import export_run, replay_run

    exported = export_run(run_id)
    Path(exported["state"]["spec_path"]).unlink()

    replay = replay_run(exported)

    assert replay["matched_count"] == 3


def test_replay_run_passes_runtime_root_to_routing_lookup(tmp_path, monkeypatch):
    workspace, run_id = _build_history_workspace(tmp_path)
    monkeypatch.chdir(workspace)

    exported = {
        "schema_version": "run_export.v1",
        "run_id": run_id,
        "paths": {
            "run_dir": str(workspace / ".supervisor" / "runtime" / "runs" / run_id),
            "runtime_dir": str(workspace / ".supervisor" / "runtime"),
        },
        "state": {
            "run_id": run_id,
            "spec_id": "demo",
            "mode": "linear_plan",
            "top_state": "PAUSED_FOR_HUMAN",
            "current_node_id": "write_test",
            "current_attempt": 0,
            "done_node_ids": [],
            "branch_history": [],
            "human_escalations": [],
            "retry_budget": {"per_node": 3, "global_limit": 12, "used_global": 0},
            "last_agent_checkpoint": {},
            "checkpoint_seq": 1,
            "verification": {},
            "last_event": {},
            "spec_path": str(workspace / ".supervisor" / "specs" / "demo.yaml"),
            "spec_hash": "",
            "pane_target": "%1",
            "surface_type": "tmux",
            "workspace_root": str(workspace),
            "completed_reviews": [],
            "last_injected_node_id": "",
            "last_injected_attempt": 0,
        },
        "spec_snapshot": {
            "path": str(workspace / ".supervisor" / "specs" / "demo.yaml"),
            "content": (
                "kind: linear_plan\nid: demo\ngoal: history test\n"
                "steps:\n  - id: write_test\n    type: task\n    objective: write tests\n"
                "    verify:\n      - type: command\n        run: echo ok\n        expect: pass\n"
            ),
        },
        "decision_log": [
            {"decision_id": "dec_1", "decision": "ESCALATE_TO_HUMAN", "reason": "checkpoint says blocked", "confidence": 1.0, "needs_human": True, "timestamp": "2026-04-12T00:00:01Z", "gate_type": "checkpoint_status", "triggered_by_seq": 1, "triggered_by_checkpoint_id": "cp_1"}
        ],
        "session_log": [
            {"run_id": run_id, "seq": 1, "event_type": "checkpoint", "timestamp": "2026-04-12T00:00:01Z", "payload": {"checkpoint_id": "cp_1", "run_id": run_id, "checkpoint_seq": 1, "status": "blocked", "current_node": "write_test", "summary": "need help"}},
            {"run_id": run_id, "seq": 2, "event_type": "gate_decision", "timestamp": "2026-04-12T00:00:01Z", "payload": {"decision_id": "dec_1", "decision": "ESCALATE_TO_HUMAN", "reason": "checkpoint says blocked", "confidence": 1.0, "needs_human": True, "timestamp": "2026-04-12T00:00:01Z", "gate_type": "checkpoint_status", "triggered_by_seq": 1, "triggered_by_checkpoint_id": "cp_1"}},
        ],
        "notes": [],
    }

    captured: dict[str, str] = {}

    def fake_lookup(run_id_arg: str, runtime_dir: str = ".supervisor/runtime") -> str:
        captured["run_id"] = run_id_arg
        captured["runtime_dir"] = runtime_dir
        return ""

    monkeypatch.setattr("supervisor.loop.latest_oracle_consultation_id_for_run", fake_lookup)

    from supervisor.history import replay_run

    replay_run(exported)

    assert captured["run_id"] == run_id
    assert captured["runtime_dir"] == str(workspace / ".supervisor" / "runtime")
