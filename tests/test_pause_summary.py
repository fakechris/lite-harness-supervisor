from __future__ import annotations

from supervisor.pause_summary import summarize_state


def test_summarize_state_suggests_resume_for_general_human_pause():
    summary = summarize_state({
        "run_id": "run_123",
        "top_state": "PAUSED_FOR_HUMAN",
        "spec_path": "/tmp/spec.yaml",
        "pane_target": "%9",
        "surface_type": "tmux",
        "human_escalations": [
            {
                "reason": "node mismatch persisted for 5 checkpoints",
                "checkpoint_node": "step_3",
                "state_node": "step_2",
            }
        ],
    })

    assert summary["pause_reason"] == "node mismatch persisted for 5 checkpoints"
    assert summary["next_action"] == (
        "thin-supervisor run resume --spec /tmp/spec.yaml --pane %9 --surface tmux"
    )
    assert summary["is_waiting_for_review"] is False


def test_summarize_state_prefers_review_ack_when_finish_gate_requires_reviewer():
    summary = summarize_state({
        "run_id": "run_456",
        "top_state": "PAUSED_FOR_HUMAN",
        "spec_path": "/tmp/spec.yaml",
        "pane_target": "%1",
        "human_escalations": [
            {"reason": "requires review by: human"}
        ],
    })

    assert summary["pause_reason"] == "requires review by: human"
    assert summary["next_action"] == "thin-supervisor run review run_456 --by human"
    assert summary["is_waiting_for_review"] is True
