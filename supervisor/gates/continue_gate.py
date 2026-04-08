from __future__ import annotations
from supervisor.domain.enums import DecisionType
from supervisor.gates.rules import classify_text, classify_checkpoint

class ContinueGate:
    def __init__(self, judge_client):
        self.judge_client = judge_client

    def decide(self, context: dict) -> dict:
        question = context.get("last_agent_question", "")
        checkpoint = context.get("last_agent_checkpoint", {}) or {}

        text_hit = classify_text(question)
        cp_hit = classify_checkpoint(checkpoint)

        # Prioritize escalation signals over soft confirmations.
        # A dangerous checkpoint must not be masked by a soft text hit.
        escalation_classes = {"MISSING_EXTERNAL_INPUT", "DANGEROUS_ACTION"}
        if text_hit in escalation_classes or cp_hit in escalation_classes:
            hit = text_hit if text_hit in escalation_classes else cp_hit
        else:
            hit = text_hit or cp_hit

        if hit == "SOFT_CONFIRMATION":
            return {
                "decision": DecisionType.CONTINUE.value,
                "reason": "soft confirmation only",
                "confidence": 0.95,
                "needs_human": False,
                "next_instruction": (
                    "Continue with the highest-priority remaining action in the current node. "
                    "Do not ask the user for confirmation unless blocked by missing authority, "
                    "missing external input, or destructive irreversible action."
                ),
            }

        if hit == "MISSING_EXTERNAL_INPUT":
            return {
                "decision": DecisionType.ESCALATE_TO_HUMAN.value,
                "reason": "missing external input",
                "confidence": 0.98,
                "needs_human": True,
            }

        if hit == "DANGEROUS_ACTION":
            return {
                "decision": DecisionType.ESCALATE_TO_HUMAN.value,
                "reason": "dangerous irreversible action",
                "confidence": 0.99,
                "needs_human": True,
            }

        return self.judge_client.continue_or_escalate(context)
