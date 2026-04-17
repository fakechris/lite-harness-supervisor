from __future__ import annotations

from supervisor.pause_summary import pause_class, summarize_state


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


def test_summarize_state_recovery_class_suggests_inspect_not_resume():
    """Recovery pauses surface `inspect` — a blind resume on a recovery pause
    loops the run straight back into the same delivery/idle/inject fault."""
    summary = summarize_state({
        "run_id": "run_rec",
        "top_state": "PAUSED_FOR_HUMAN",
        "spec_path": "/tmp/spec.yaml",
        "pane_target": "%3",
        "surface_type": "tmux",
        "human_escalations": [
            {
                "reason": "no checkpoint received within delivery timeout after injection",
                "pause_class": "recovery",
            }
        ],
    })

    assert summary["pause_class"] == "recovery"
    assert summary["next_action"] == "thin-supervisor inspect run_rec"
    assert summary["is_waiting_for_review"] is False


def test_summarize_state_business_class_keeps_resume_hint():
    summary = summarize_state({
        "run_id": "run_biz",
        "top_state": "PAUSED_FOR_HUMAN",
        "spec_path": "/tmp/spec.yaml",
        "pane_target": "%4",
        "surface_type": "tmux",
        "human_escalations": [
            {"reason": "missing API key", "pause_class": "business"}
        ],
    })

    assert summary["pause_class"] == "business"
    assert "--pane %4" in summary["next_action"]
    assert summary["next_action"].startswith("thin-supervisor run resume ")


def test_pause_class_helper_rejects_unknown_values():
    state = {
        "top_state": "PAUSED_FOR_HUMAN",
        "human_escalations": [{"reason": "x", "pause_class": "bogus"}],
    }
    # Unknown class must not leak out as authoritative — surfaces would
    # otherwise render undefined tags.
    assert pause_class(state) == ""


def test_pause_class_helper_returns_empty_when_not_paused():
    state = {
        "top_state": "RUNNING",
        "human_escalations": [{"reason": "old", "pause_class": "business"}],
    }
    assert pause_class(state) == ""
