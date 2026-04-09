"""Branch gate for conditional_workflow decision nodes."""
from __future__ import annotations

from supervisor.domain.enums import DecisionType


class BranchGate:
    """Selects a branch for decision nodes.

    Uses the LLM judge to pick from allowed options based on
    the agent's checkpoint evidence. Falls back to escalation
    if confidence is too low.
    """

    def __init__(self, judge_client, confidence_threshold: float = 0.75):
        self.judge_client = judge_client
        self.confidence_threshold = confidence_threshold

    def decide(self, spec, state, node) -> dict:
        if not node.options:
            return {
                "decision": DecisionType.ESCALATE_TO_HUMAN.value,
                "reason": "decision node has no options defined",
                "needs_human": True,
            }

        context = {
            "spec_id": spec.id,
            "current_node_id": node.id,
            "objective": node.objective,
            "options": [{"id": o.id, "next": o.next, "label": o.label, "when_hint": o.when_hint} for o in node.options],
            "last_agent_checkpoint": state.last_agent_checkpoint,
            "done_node_ids": state.done_node_ids,
        }

        result = self.judge_client.choose_branch(context)
        selected_id = result.get("decision", "")
        confidence = result.get("confidence", 0)

        # Validate the selected branch exists
        option_map = {o.id: o for o in node.options}

        if selected_id in option_map and confidence >= self.confidence_threshold:
            selected = option_map[selected_id]
            return {
                "decision": DecisionType.BRANCH.value,
                "selected_branch": selected_id,
                "next_node_id": selected.next,
                "reason": result.get("reason", ""),
                "confidence": confidence,
                "needs_human": False,
            }

        # Low confidence or invalid selection → escalate
        return {
            "decision": DecisionType.ESCALATE_TO_HUMAN.value,
            "reason": f"branch confidence too low ({confidence}) or invalid selection ({selected_id})",
            "confidence": confidence,
            "needs_human": True,
            "options": [o.id for o in node.options],
        }
