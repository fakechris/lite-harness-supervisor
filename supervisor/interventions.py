from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AutoIntervention:
    action_type: str
    instruction: str
    reason: str


class AutoInterventionManager:
    def __init__(self, *, mode: str = "notify_then_ai", max_auto_interventions: int = 2):
        self.mode = mode
        self.max_auto_interventions = max_auto_interventions

    def maybe_plan(self, spec, state, payload: dict, terminal) -> AutoIntervention | None:
        if self.mode != "notify_then_ai":
            return None
        if getattr(state, "auto_intervention_count", 0) >= self.max_auto_interventions:
            return None
        if getattr(terminal, "is_observation_only", False):
            return None
        if spec is None:
            return None

        reason = str(payload.get("reason", "")).strip()
        if not reason or reason.startswith("requires review by:"):
            return None

        # "blocked" checkpoints are genuine external blockers — do NOT auto-recover.
        # The contract says: "If blocked, emit status: blocked. Supervisor will escalate."
        # Auto-recovery only handles recoverable conditions below.

        if "node mismatch persisted" in reason:
            node = spec.get_node(state.current_node_id)
            return AutoIntervention(
                action_type="resume_with_instruction",
                reason="checkpoint node mismatch auto-recovery",
                instruction=(
                    f"Supervisor expected current_node: {state.current_node_id}. "
                    "You emitted checkpoints for a later node. "
                    f"Resume from current_node: {state.current_node_id}. "
                    f"Objective: {node.objective}. "
                    f"Your next checkpoint MUST use current_node: {state.current_node_id}. "
                    "Do not report later nodes until this node is verified."
                ),
            )

        if "retry budget exhausted" in reason:
            node = spec.get_node(state.current_node_id)
            return AutoIntervention(
                action_type="resume_with_instruction",
                reason="retry budget auto-recovery",
                instruction=(
                    "Testing auto-recovery mode is enabled after repeated failures. "
                    f"Re-focus on current_node: {state.current_node_id}. "
                    f"Objective: {node.objective}. "
                    "Perform a short self-review of the failed verification or prior attempt, "
                    "apply the smallest fix, rerun the relevant verifier, and then emit a fresh checkpoint."
                ),
            )

        return None
