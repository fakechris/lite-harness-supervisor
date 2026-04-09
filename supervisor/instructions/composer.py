"""Composes concise instructions for injection into the agent pane."""
from __future__ import annotations


class InstructionComposer:
    """Builds injection text from node + state context.

    Default: just the node objective.
    Adds context only when meaningful (retry failures, gate guidance).
    Never repeats the checkpoint protocol — agent knows it from Skill/AGENTS.md.
    """

    def build(self, node, state, *, verification: dict | None = None) -> str:
        parts = [node.objective]

        # Append non-generic gate guidance
        next_inst = state.last_decision.get("next_instruction", "")
        if next_inst and next_inst != node.objective:
            generic = ["Continue with the highest-priority", "Do not ask the user"]
            if not any(p in next_inst for p in generic):
                parts.append(next_inst)

        # Append verification failure details on retry
        vf = verification or state.verification or {}
        if state.current_attempt > 0 and not vf.get("ok", True):
            failed = [r for r in vf.get("results", []) if not r.get("ok")]
            if failed:
                details = "; ".join(
                    f"{r.get('type', '?')}: {r.get('stderr', r.get('reason', ''))[:200]}"
                    for r in failed[:3]
                )
                parts.append(f"Previous verification failed: {details}")

        return " ".join(parts)
