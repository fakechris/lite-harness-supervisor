"""Shared escalation mapping for the gate layers.

Both `SupervisorLoop.gate()` and `ContinueGate.decide()` need to turn a
checkpoint + question pair into an ESCALATE_TO_HUMAN decision when the
agent is asking for missing external input, flagging a dangerous action,
or reporting blocked status.  Before this module existed, each site
hand-coded its own (class → reason / confidence) mapping — which is how
the loop-level ATTACHED guard ended up classifying only the checkpoint
(not the question), and how the reasons drifted between layers.

Keeping the mapping in one place prevents that class of regression.
"""
from __future__ import annotations

from supervisor.domain.enums import DecisionType
from supervisor.domain.models import SupervisorDecision
from supervisor.gates.rules import classify_checkpoint, classify_text

ESCALATION_CLASSES: tuple[str, ...] = (
    "MISSING_EXTERNAL_INPUT",
    "DANGEROUS_ACTION",
    "BLOCKED",
)

# (class) → (reason, confidence).  Keep in sync with the patterns in
# `supervisor/gates/rules.py`; adding a new class means adding a row here.
_ESCALATION_REASON: dict[str, tuple[str, float]] = {
    "MISSING_EXTERNAL_INPUT": ("missing external input", 0.98),
    "DANGEROUS_ACTION": ("dangerous irreversible action", 0.99),
    "BLOCKED": ("agent reported blocked", 0.95),
}


def classify_for_escalation(checkpoint: dict | None, question: str) -> str | None:
    """Return the escalation class hit by either the question or the checkpoint,
    or None if neither carries an escalation signal.

    Question takes precedence when both layers hit escalation classes — matches
    the ordering inside `ContinueGate.decide()` so callers at either layer see
    the same result.
    """
    text_hit = classify_text(question or "")
    cp_hit = classify_checkpoint(checkpoint or {})
    if text_hit in ESCALATION_CLASSES:
        return text_hit
    if cp_hit in ESCALATION_CLASSES:
        return cp_hit
    return None


def escalation_decision(
    hit: str,
    *,
    gate_type: str,
    triggered_by_seq: int = 0,
    triggered_by_checkpoint_id: str = "",
) -> SupervisorDecision:
    """Build an ESCALATE_TO_HUMAN decision for an escalation-class hit.

    Raises KeyError if `hit` is not a known escalation class — callers should
    pre-check with `classify_for_escalation` or membership in
    `ESCALATION_CLASSES`.
    """
    reason, confidence = _ESCALATION_REASON[hit]
    return SupervisorDecision.make(
        decision=DecisionType.ESCALATE_TO_HUMAN.value,
        reason=reason,
        gate_type=gate_type,
        confidence=confidence,
        needs_human=True,
        triggered_by_seq=triggered_by_seq,
        triggered_by_checkpoint_id=triggered_by_checkpoint_id,
    )
