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

        # Delivery ack timeout: send-keys completed but no checkpoint arrived
        # within the ack window. Either the agent never saw the injection, or
        # it is already working but not emitting. A focused re-inject that
        # asks for a progress checkpoint is the cheapest recovery.
        if "no checkpoint received within delivery timeout" in reason:
            node = spec.get_node(state.current_node_id)
            return AutoIntervention(
                action_type="resume_with_instruction",
                reason="delivery ack timeout auto-recovery",
                instruction=(
                    f"Supervisor has not seen a checkpoint since the last injection "
                    f"for current_node: {state.current_node_id}. "
                    f"Objective: {node.objective}. "
                    "If you already received the instruction and are working, emit "
                    "a short progress checkpoint NOW with concrete evidence of work "
                    "on this node. "
                    "If you never saw the instruction, start the node's objective "
                    "now and emit a checkpoint after first meaningful progress."
                ),
            )

        # Agent idle: no pane activity or checkpoints for the idle window. Same
        # recipe as delivery timeout — prompt for a concrete progress signal.
        if "idle timeout" in reason:
            node = spec.get_node(state.current_node_id)
            return AutoIntervention(
                action_type="resume_with_instruction",
                reason="idle timeout auto-recovery",
                instruction=(
                    f"Supervisor sees no agent activity for current_node: "
                    f"{state.current_node_id}. Objective: {node.objective}. "
                    "Emit a checkpoint now with either: (a) concrete evidence of "
                    "work already in progress, or (b) the specific reason you are "
                    "stuck (tool failing, unclear instruction, missing input). "
                    "Do not stay silent."
                ),
            )

        # Inject failure: send-keys itself errored. One retry of the inject
        # (via the normal inject path) before falling through to a human.
        if reason.startswith("injection failed") or "inject failed" in reason:
            node = spec.get_node(state.current_node_id)
            return AutoIntervention(
                action_type="resume_with_instruction",
                reason="inject failure auto-recovery",
                instruction=(
                    f"Resuming current_node: {state.current_node_id} after a "
                    "transient injection error. "
                    f"Objective: {node.objective}. "
                    "Continue from wherever you were; if you had not yet started, "
                    "start now and emit a checkpoint on first meaningful progress."
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
