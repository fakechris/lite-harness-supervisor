from __future__ import annotations
from supervisor.domain.enums import DecisionType
from supervisor.domain.models import SupervisorDecision
from supervisor.gates.escalation import classify_for_escalation, escalation_decision
from supervisor.gates.rules import classify_text, classify_checkpoint


class ContinueGate:
    def __init__(self, judge_client):
        self.judge_client = judge_client

    def decide(self, context: dict, *, triggered_by_seq: int = 0) -> SupervisorDecision:
        question = context.get("last_agent_question", "")
        checkpoint = context.get("last_agent_checkpoint", {}) or {}

        # Escalation classification must run FIRST. If the agent is asking
        # for missing credentials, flagging a dangerous action, or reporting
        # blocked status, that signal wins over any default-CONTINUE path.
        # The shared `escalation.classify_for_escalation` helper unifies
        # this ordering with `SupervisorLoop.gate()` so the two layers
        # cannot drift.
        #
        # The ATTACHED first-execution-evidence guard used to live here as
        # well, but `SupervisorLoop.gate()` now applies the identical check
        # across every allowed ``cp_status`` before delegating to this
        # gate — so any ATTACHED + admin-only payload is already routed by
        # the loop layer. A second copy here was unreachable and a drift
        # hazard; the guard is authoritative at the loop layer only.
        esc_hit = classify_for_escalation(checkpoint, question)

        # Soft confirmation: trust no-escalation affirmation ("要不要我继续",
        # etc.) and push the agent to continue instead of pausing.
        if esc_hit is None and (
            classify_text(question) == "SOFT_CONFIRMATION"
            or classify_checkpoint(checkpoint) == "SOFT_CONFIRMATION"
        ):
            return SupervisorDecision.make(
                decision=DecisionType.CONTINUE.value,
                reason="soft confirmation only",
                gate_type="continue",
                confidence=0.95,
                needs_human=False,
                triggered_by_seq=triggered_by_seq,
                next_instruction=(
                    "Continue with the highest-priority remaining action in the current node. "
                    "Do not ask the user for confirmation unless blocked by missing authority, "
                    "missing external input, or destructive irreversible action."
                ),
            )

        if esc_hit is not None:
            return escalation_decision(
                esc_hit, gate_type="continue", triggered_by_seq=triggered_by_seq,
            )

        # LLM judge fallback — returns dict, wrap it
        raw = self.judge_client.continue_or_escalate(context)
        if not isinstance(raw, dict):
            raw = {"decision": "continue", "reason": "judge returned non-dict, defaulting to continue"}
        return SupervisorDecision.make(
            decision=raw.get("decision", "continue").upper(),
            reason=raw.get("reason", ""),
            gate_type="continue",
            confidence=raw.get("confidence", 0.5),
            needs_human=raw.get("needs_human", False),
            triggered_by_seq=triggered_by_seq,
            next_instruction=raw.get("next_instruction"),
        )
