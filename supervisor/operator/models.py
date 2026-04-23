"""Canonical operator-facing data models.

These are *projections* over existing runtime state (state.json,
session_log.jsonl) — they never become a separate source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def coerce_confidence(value: Any) -> float | None:
    """Parse a confidence value from raw explainer/IPC payloads.

    Returns ``None`` for missing, non-numeric, or unparseable values.
    Shared by ``ExchangeView``, ``DriftAssessment``, and the clarification
    pipeline so every consumer applies the same coercion rule.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    # Event-plane backlog summary (Task 5). Optional: callers that do not
    # have an EventPlaneStore handy (history replay, tests that don't
    # exercise the event plane) leave it None.
    event_plane: "RunEventPlaneSummary | None" = None

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
            "event_plane": self.event_plane.to_dict() if self.event_plane else None,
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


@dataclass(frozen=True)
class RunEventPlaneSummary:
    """Per-session event-plane counters + latest pointers.

    Projected passively from the event-plane JSONL logs. Never a source
    of truth for correlation — callers must still read the underlying
    records when they need authoritative data.
    """
    waits_open: int
    mailbox_new: int
    mailbox_acknowledged: int
    requests_total: int
    latest_mailbox_item_id: str
    latest_wake_decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "waits_open": self.waits_open,
            "mailbox_new": self.mailbox_new,
            "mailbox_acknowledged": self.mailbox_acknowledged,
            "requests_total": self.requests_total,
            "latest_mailbox_item_id": self.latest_mailbox_item_id,
            "latest_wake_decision": self.latest_wake_decision,
        }


@dataclass(frozen=True)
class SystemCounts:
    """Top-level counters for the system overview view."""
    daemons: int
    foreground_runs: int
    live_sessions: int
    orphaned_sessions: int
    completed_sessions: int
    waits_open: int
    mailbox_new: int
    mailbox_acknowledged: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "daemons": self.daemons,
            "foreground_runs": self.foreground_runs,
            "live_sessions": self.live_sessions,
            "orphaned_sessions": self.orphaned_sessions,
            "completed_sessions": self.completed_sessions,
            "waits_open": self.waits_open,
            "mailbox_new": self.mailbox_new,
            "mailbox_acknowledged": self.mailbox_acknowledged,
        }


@dataclass(frozen=True)
class SystemAlert:
    """One actionable item for the operator, aggregated from sessions
    and event-plane state. Alert kinds are enumerated for stability:

      - paused_for_human: runs awaiting operator decision
      - overdue_wait: SessionWait past its deadline (wired in Task 3)
      - mailbox_backlog: unread mailbox items needing attention
      - orphaned: sessions with no live owner
    """
    kind: str
    count: int
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "count": self.count,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class SystemTimelineEvent:
    """One entry in the cross-run observability timeline.

    Source is the shared system_events.jsonl (Task 3) plus a selection
    of session-scoped events promoted through the allowlist.
    """
    event_type: str
    occurred_at: str
    scope: str                    # "system" | "session"
    session_id: str
    run_id: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "scope": self.scope,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "summary": self.summary,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ExchangeView:
    """A human-readable "what just happened between supervisor and worker" view.

    Built from the last checkpoint + last instruction in a run's timeline.
    Channel adapters (TUI, Telegram, Lark) consume this instead of
    stringifying raw dicts. The explainer-filled fields (``explanation_*``)
    are optional — they're populated only when the operator asks for a
    natural-language translation of the exchange.
    """
    run_id: str
    window_start: str                 # ISO timestamp of earliest event shown
    window_end: str                   # ISO timestamp of latest event shown
    worker_text_excerpt: str          # last checkpoint summary/content
    supervisor_instruction_excerpt: str
    checkpoint_excerpt: str           # raw checkpoint marker text, if any
    explanation_zh: str = ""
    explanation_en: str = ""
    confidence: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, run_id: str = "") -> "ExchangeView":
        """Wrap a raw exchange dict (from ``operator.api.recent_exchange``).

        Accepts both the pre-explainer shape (``last_checkpoint_summary`` +
        ``last_instruction_summary``) and the post-explainer shape
        (``explanation``, ``confidence``, …).
        """
        conf = coerce_confidence(data.get("confidence"))
        return cls(
            run_id=str(data.get("run_id") or run_id or ""),
            window_start=str(data.get("window_start", "")),
            window_end=str(data.get("window_end", "")),
            worker_text_excerpt=str(
                data.get("worker_text_excerpt")
                or data.get("last_checkpoint_summary", "")
            ),
            supervisor_instruction_excerpt=str(
                data.get("supervisor_instruction_excerpt")
                or data.get("last_instruction_summary", "")
            ),
            checkpoint_excerpt=str(data.get("checkpoint_excerpt", "")),
            explanation_zh=str(data.get("explanation_zh", "")),
            explanation_en=str(data.get("explanation_en", "")),
            confidence=conf,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "worker_text_excerpt": self.worker_text_excerpt,
            "supervisor_instruction_excerpt": self.supervisor_instruction_excerpt,
            "checkpoint_excerpt": self.checkpoint_excerpt,
            "explanation_zh": self.explanation_zh,
            "explanation_en": self.explanation_en,
            "confidence": self.confidence,
        }


_DRIFT_STATUSES = frozenset({"on_track", "watch", "drifting", "blocked"})


@dataclass(frozen=True)
class DriftAssessment:
    """Structured answer to "is this run still on track?"

    Built from ``ExplainerClient.assess_drift`` output. The raw dict is
    kept as the wire format across daemon IPC; this dataclass is for
    type-safe consumption by TUI / IM channels.
    """
    run_id: str
    status: str                       # on_track | watch | drifting | blocked
    reasons: list[str]
    evidence: list[str]
    codebase_signals: list[str]
    recommended_action: str
    confidence: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, run_id: str = "") -> "DriftAssessment":
        status = str(data.get("status", "") or "").strip().lower()
        if status not in _DRIFT_STATUSES:
            status = "watch"  # unknown → conservative bucket
        conf = coerce_confidence(data.get("confidence"))
        return cls(
            run_id=str(data.get("run_id") or run_id or ""),
            status=status,
            reasons=[str(x) for x in (data.get("reasons") or [])],
            evidence=[str(x) for x in (data.get("evidence") or [])],
            codebase_signals=[str(x) for x in (data.get("codebase_signals") or [])],
            recommended_action=str(
                data.get("recommended_action")
                or data.get("recommended_operator_action", "")
            ),
            confidence=conf,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
            "codebase_signals": list(self.codebase_signals),
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class SystemSnapshot:
    """Top-level projection consumed by `overview`, `status`, `tui`."""
    counts: SystemCounts
    alerts: list[SystemAlert]
    recent_timeline: list[SystemTimelineEvent]
    sessions: list[Any]           # list[SessionRecord]; Any avoids a cycle

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts.to_dict(),
            "alerts": [a.to_dict() for a in self.alerts],
            "recent_timeline": [e.to_dict() for e in self.recent_timeline],
            "sessions": [
                s.as_dict() if hasattr(s, "as_dict") else s.to_dict() if hasattr(s, "to_dict") else s
                for s in self.sessions
            ],
        }
