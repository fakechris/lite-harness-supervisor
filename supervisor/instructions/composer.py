"""Composes HandoffInstruction objects for injection into the agent pane."""
from __future__ import annotations

from supervisor.domain.models import HandoffInstruction, SupervisionPolicy


class InstructionComposer:
    """Builds HandoffInstruction from node + state + policy context.

    Instruction style adapts to supervision policy mode:
    - strict_verifier: just the objective (trust the worker)
    - collaborative_reviewer: ask for approach + risks first
    - directive_lead: detailed sub-steps, one action at a time
    """

    def build(self, node, state, *, triggered_by_decision_id: str = "",
              trigger_type: str = "node_advance",
              verification: dict | None = None,
              policy: SupervisionPolicy | None = None) -> HandoffInstruction:

        mode = policy.mode if policy else "strict_verifier"
        parts = []

        if mode == "directive_lead":
            parts.append(f"[DIRECTIVE] Execute exactly this: {node.objective}")
            parts.append("Do only this one action. Do not proceed to the next step.")
            parts.append("Report results immediately with a checkpoint.")
        elif mode == "collaborative_reviewer":
            parts.append(node.objective)
            parts.append("Before executing, briefly describe your approach and any risks you see.")
        else:
            # strict_verifier: minimal guidance
            parts.append(node.objective)

        # Append non-generic gate guidance
        next_inst = state.last_decision.get("next_instruction") if isinstance(state.last_decision, dict) else getattr(state.last_decision, "next_instruction", None)
        if next_inst and next_inst != node.objective:
            generic = ["Continue with the highest-priority", "Do not ask the user"]
            if trigger_type == "continue" or not any(p in next_inst for p in generic):
                parts.append(next_inst)

        # Append verification failure details on retry
        vf = verification or state.verification or {}
        if state.current_attempt > 0 and not vf.get("ok", True):
            failed = [r for r in vf.get("results", []) if not r.get("ok")]
            if failed:
                details = "; ".join(
                    f"{r.get('type', '?')}: {(r.get('stderr') or r.get('reason') or '')[:200]}"
                    for r in failed[:3]
                )
                parts.append(f"Previous verification failed: {details}")

        parts.append(self._checkpoint_protocol_suffix(node.id))

        content = " ".join(parts)

        return HandoffInstruction.make(
            content=content,
            node_id=node.id,
            current_attempt=state.current_attempt,
            triggered_by_decision_id=triggered_by_decision_id,
            trigger_type=trigger_type,
        )

    @staticmethod
    def _checkpoint_protocol_suffix(node_id: str) -> str:
        return (
            f"Stay on current_node: {node_id}. "
            "After meaningful progress, output a checkpoint block exactly like: "
            f"<checkpoint> status: working | blocked | step_done | workflow_done "
            f"current_node: {node_id} "
            "summary: <one-line description> evidence: ... candidate_next_actions: ... "
            "needs: - none question_for_supervisor: - none </checkpoint>"
        )
