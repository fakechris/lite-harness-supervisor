"""Branch gate for conditional_workflow decision nodes."""
from __future__ import annotations

from supervisor.domain.enums import DecisionType
from supervisor.domain.models import SupervisorDecision


class BranchGate:
    def __init__(self, judge_client, confidence_threshold: float = 0.75):
        self.judge_client = judge_client
        self.confidence_threshold = confidence_threshold

    def decide(self, spec, state, node, *, triggered_by_seq: int = 0) -> SupervisorDecision:
        if not node.options:
            return SupervisorDecision.make(
                decision=DecisionType.ESCALATE_TO_HUMAN.value,
                reason="decision node has no options defined",
                gate_type="branch",
                confidence=0.0,
                needs_human=True,
                triggered_by_seq=triggered_by_seq,
            )

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

        option_map = {o.id: o for o in node.options}

        if selected_id in option_map and confidence >= self.confidence_threshold:
            selected = option_map[selected_id]
            return SupervisorDecision.make(
                decision=DecisionType.BRANCH.value,
                reason=result.get("reason", ""),
                gate_type="branch",
                confidence=confidence,
                needs_human=False,
                triggered_by_seq=triggered_by_seq,
                selected_branch=selected_id,
                next_node_id=selected.next,
            )

        return SupervisorDecision.make(
            decision=DecisionType.ESCALATE_TO_HUMAN.value,
            reason=f"branch confidence too low ({confidence}) or invalid selection ({selected_id})",
            gate_type="branch",
            confidence=confidence,
            needs_human=True,
            triggered_by_seq=triggered_by_seq,
        )
