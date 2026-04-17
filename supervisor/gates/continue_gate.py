from __future__ import annotations
from supervisor.domain.enums import DecisionType, TopState
from supervisor.domain.models import SupervisorDecision
from supervisor.gates.escalation import classify_for_escalation, escalation_decision
from supervisor.gates.rules import classify_text, classify_checkpoint, is_admin_only_evidence


class ContinueGate:
    def __init__(self, judge_client):
        self.judge_client = judge_client

    def decide(self, context: dict, *, triggered_by_seq: int = 0) -> SupervisorDecision:
        question = context.get("last_agent_question", "")
        checkpoint = context.get("last_agent_checkpoint", {}) or {}
        top_state = context.get("top_state", "")

        # Escalation classification must run FIRST.  If the agent is asking
        # for missing credentials, flagging a dangerous action, or reporting
        # blocked status, that signal wins over the attach-boundary
        # re-inject — a first checkpoint with admin-only evidence AND
        # "need API key" is a legitimate business pause, not a re-inject
        # candidate.  The shared `escalation.classify_for_escalation` helper
        # unifies this ordering with `SupervisorLoop.gate()` so the two
        # layers cannot drift.
        esc_hit = classify_for_escalation(checkpoint, question)

        # ATTACHED-boundary guard: a CONTINUE here would advance a run whose
        # first checkpoint cited only attach/clarify/plan artifacts — exactly
        # the Phase 17 failure pattern.  RE_INJECT instead, without charging
        # `current_attempt` or the global retry budget.  Placed AFTER the
        # escalation classification so escalations on the first checkpoint
        # still route to ESCALATE_TO_HUMAN below.
        if top_state == TopState.ATTACHED.value and esc_hit is None:
            cp_status = (checkpoint or {}).get("status", "")
            if cp_status == "working" and is_admin_only_evidence((checkpoint or {}).get("evidence")):
                return SupervisorDecision.make(
                    decision=DecisionType.RE_INJECT.value,
                    reason="attached: first checkpoint has no execution evidence on current_node",
                    gate_type="continue",
                    confidence=0.95,
                    needs_human=False,
                    triggered_by_seq=triggered_by_seq,
                )

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
