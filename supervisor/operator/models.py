"""Canonical operator-facing data models.

These are *projections* over existing runtime state (state.json,
session_log.jsonl) — they never become a separate source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunSnapshot:
    """Current state of a run for operator display.

    Built from SupervisorState (state.json) + derived summaries.
    """
    run_id: str
    spec_id: str
    worktree_root: str
    controller_mode: str
    surface_type: str
    surface_target: str           # pane_target
    top_state: str
    current_node: str
    current_attempt: int
    done_nodes: list[str]
    pause_reason: str
    status_reason: str
    next_action: str
    is_waiting_for_review: bool
    last_checkpoint_summary: str
    last_instruction_summary: str
    delivery_state: str
    updated_at: str               # timestamp from state or session log

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "spec_id": self.spec_id,
            "worktree_root": self.worktree_root,
            "controller_mode": self.controller_mode,
            "surface_type": self.surface_type,
            "surface_target": self.surface_target,
            "top_state": self.top_state,
            "current_node": self.current_node,
            "current_attempt": self.current_attempt,
            "done_nodes": list(self.done_nodes),
            "pause_reason": self.pause_reason,
            "status_reason": self.status_reason,
            "next_action": self.next_action,
            "is_waiting_for_review": self.is_waiting_for_review,
            "last_checkpoint_summary": self.last_checkpoint_summary,
            "last_instruction_summary": self.last_instruction_summary,
            "delivery_state": self.delivery_state,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class RunTimelineEvent:
    """Canonical timeline event for operator review.

    Projected from session_log.jsonl entries.
    """
    run_id: str
    seq: int
    event_type: str
    occurred_at: str              # timestamp
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    # Well-known event_type values (for documentation, not enforcement):
    #   checkpoint, instruction_injected, gate_decision,
    #   verification_started, verification_result,
    #   pause, resume, routing, notification,
    #   operator_note, clarification_request, clarification_response,
    #   explainer_answer

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "summary": self.summary,
            "payload": self.payload,
        }
