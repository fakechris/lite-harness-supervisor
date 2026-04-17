"""Composes HandoffInstruction objects for injection into the agent pane."""
from __future__ import annotations

from supervisor.domain.models import HandoffInstruction, SupervisionPolicy
from supervisor.protocol.checkpoints import checkpoint_example_block


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
              policy: SupervisionPolicy | None = None,
              first_node_delivery: bool = False) -> HandoffInstruction:

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

        parts.append(self._checkpoint_protocol_suffix(
            node.id, first_node_delivery=first_node_delivery,
        ))

        content = "\n\n".join(parts)

        return HandoffInstruction.make(
            content=content,
            node_id=node.id,
            current_attempt=state.current_attempt,
            triggered_by_decision_id=triggered_by_decision_id,
            trigger_type=trigger_type,
        )

    @staticmethod
    def _checkpoint_protocol_suffix(node_id: str, *, first_node_delivery: bool = False) -> str:
        base = (
            f"Stay on current_node: {node_id}.\n"
            "After meaningful progress, output a checkpoint block exactly like:\n"
            f"{checkpoint_example_block(node_id)}"
        )
        if not first_node_delivery:
            return base
        return (
            base
            + "\n\nThis is the FIRST checkpoint for this node. Its `evidence:` "
            f"must cite concrete work on node {node_id} — a command you ran, "
            "a file you modified, or a verifier result on this node's objective. "
            "Clarify, plan, spec, attach, or baseline-check artifacts from earlier "
            "phases are NOT evidence of progress on this node and must not be listed. "
            "If you have not yet produced any work on this node, start the work "
            "first and emit the checkpoint after."
        )
