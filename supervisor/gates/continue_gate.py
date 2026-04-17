from __future__ import annotations
from supervisor.domain.enums import DecisionType, TopState
from supervisor.domain.models import SupervisorDecision
from supervisor.gates.rules import classify_text, classify_checkpoint, is_admin_only_evidence


class ContinueGate:
    def __init__(self, judge_client):
        self.judge_client = judge_client

    def decide(self, context: dict, *, triggered_by_seq: int = 0) -> SupervisorDecision:
        question = context.get("last_agent_question", "")
        checkpoint = context.get("last_agent_checkpoint", {}) or {}

        # ATTACHED-boundary guard: a CONTINUE here would advance a run whose
        # first checkpoint cited only attach/clarify/plan artifacts — exactly
        # the Phase 17 failure pattern.  RE_INJECT instead, without charging
        # `current_attempt` or the global retry budget.
        #
        # Skip if the agent already flagged an escalation — those paths are
        # handled below.  Escalation on first checkpoint is still legitimate.
        top_state = context.get("top_state", "")
        if top_state == TopState.ATTACHED.value:
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

        text_hit = classify_text(question)
        cp_hit = classify_checkpoint(checkpoint)

        escalation_classes = {"MISSING_EXTERNAL_INPUT", "DANGEROUS_ACTION", "BLOCKED"}
        if text_hit in escalation_classes or cp_hit in escalation_classes:
            hit = text_hit if text_hit in escalation_classes else cp_hit
        else:
            hit = text_hit or cp_hit

        if hit == "SOFT_CONFIRMATION":
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

        if hit == "MISSING_EXTERNAL_INPUT":
            return SupervisorDecision.make(
                decision=DecisionType.ESCALATE_TO_HUMAN.value,
                reason="missing external input",
                gate_type="continue",
                confidence=0.98,
                needs_human=True,
                triggered_by_seq=triggered_by_seq,
            )

        if hit == "DANGEROUS_ACTION":
            return SupervisorDecision.make(
                decision=DecisionType.ESCALATE_TO_HUMAN.value,
                reason="dangerous irreversible action",
                gate_type="continue",
                confidence=0.99,
                needs_human=True,
                triggered_by_seq=triggered_by_seq,
            )

        if hit == "BLOCKED":
            return SupervisorDecision.make(
                decision=DecisionType.ESCALATE_TO_HUMAN.value,
                reason="agent reported blocked",
                gate_type="continue",
                confidence=0.95,
                needs_human=True,
                triggered_by_seq=triggered_by_seq,
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
