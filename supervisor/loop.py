from __future__ import annotations

import logging
import time

from supervisor.domain.enums import TopState, DecisionType
from supervisor.domain.state_machine import FINAL_STATES
from supervisor.gates.continue_gate import ContinueGate
from supervisor.llm.judge_client import JudgeClient
from supervisor.verifiers.suite import VerifierSuite
from supervisor.adapters.transcript_adapter import TranscriptAdapter

logger = logging.getLogger(__name__)

def build_context(spec, state) -> dict:
    return {
        "spec_id": spec.id,
        "current_node_id": state.current_node_id,
        "last_agent_question": state.last_event.get("payload", {}).get("question", ""),
        "last_agent_checkpoint": state.last_agent_checkpoint,
        "done_node_ids": state.done_node_ids,
        "retry_budget": {
            "per_node": state.retry_budget.per_node,
            "global_limit": state.retry_budget.global_limit,
            "used_global": state.retry_budget.used_global,
        },
    }

class SupervisorLoop:
    def __init__(self, store, judge_model: str | None = None,
                 judge_temperature: float = 0.1, judge_max_tokens: int = 512):
        self.store = store
        self.judge_client = JudgeClient(
            model=judge_model,
            temperature=judge_temperature,
            max_tokens=judge_max_tokens,
        )
        self.continue_gate = ContinueGate(self.judge_client)
        self.verifier_suite = VerifierSuite()

    def handle_event(self, state, event):
        state.last_event = event
        if event["type"] == "agent_output":
            cp = event.get("payload", {}).get("checkpoint", {})
            if cp:
                state.last_agent_checkpoint = cp
                state.top_state = TopState.GATING
        elif event["type"] == "agent_ask":
            state.top_state = TopState.GATING
        elif event["type"] in {"agent_stop", "timeout"}:
            state.top_state = TopState.GATING

    def gate(self, spec, state) -> dict:
        cp = state.last_agent_checkpoint or {}
        if cp.get("status") == "step_done":
            return {"decision": DecisionType.VERIFY_STEP.value, "reason": "checkpoint says step_done"}
        if cp.get("status") == "workflow_done":
            return {"decision": DecisionType.VERIFY_STEP.value, "reason": "checkpoint says workflow_done"}
        return self.continue_gate.decide(build_context(spec, state))

    def verify_current_node(self, spec, state) -> dict:
        node = spec.get_node(state.current_node_id)
        context = {
            "current_node_done": state.current_node_id in state.done_node_ids
        }
        return self.verifier_suite.run(node.verify, context)

    def apply_decision(self, spec, state, decision: dict):
        state.last_decision = decision
        kind = decision["decision"].upper()

        if kind == DecisionType.CONTINUE.value:
            state.top_state = TopState.RUNNING
            return

        if kind == DecisionType.VERIFY_STEP.value:
            state.top_state = TopState.VERIFYING
            return

        if kind == DecisionType.RETRY.value:
            state.current_attempt += 1
            state.retry_budget.used_global += 1
            if (
                state.current_attempt >= state.retry_budget.per_node
                or state.retry_budget.used_global >= state.retry_budget.global_limit
            ):
                state.top_state = TopState.PAUSED_FOR_HUMAN
            else:
                state.top_state = TopState.RUNNING
            return

        if kind == DecisionType.ESCALATE_TO_HUMAN.value:
            state.top_state = TopState.PAUSED_FOR_HUMAN
            state.human_escalations.append(decision)
            return

        if kind == DecisionType.ABORT.value:
            state.top_state = TopState.ABORTED
            return

        if kind == DecisionType.FINISH.value:
            state.top_state = TopState.COMPLETED
            return

        raise ValueError(f"unsupported decision: {kind}")

    def apply_verification(self, spec, state, verification: dict):
        state.verification = verification
        if verification["ok"]:
            if state.current_node_id not in state.done_node_ids:
                state.done_node_ids.append(state.current_node_id)
            next_id = spec.next_node_id(state.current_node_id)
            if next_id is None:
                state.top_state = TopState.COMPLETED
            else:
                state.current_node_id = next_id
                state.current_attempt = 0
                state.top_state = TopState.RUNNING
            return

        state.current_attempt += 1
        state.retry_budget.used_global += 1
        if (
            state.current_attempt >= state.retry_budget.per_node
            or state.retry_budget.used_global >= state.retry_budget.global_limit
        ):
            state.top_state = TopState.PAUSED_FOR_HUMAN
        else:
            state.top_state = TopState.RUNNING

    def is_final(self, state) -> bool:
        return state.top_state in FINAL_STATES

    # ------------------------------------------------------------------
    # Sidecar loop (tmux-based)
    # ------------------------------------------------------------------

    def run_sidecar(self, spec, state, terminal, *, poll_interval: float = 2.0, read_lines: int = 100):
        """Main sidecar event loop.

        Reads the agent's tmux pane, parses checkpoints, gates, verifies,
        and injects the next instruction.  Runs until a final state or
        ``PAUSED_FOR_HUMAN``.

        Parameters
        ----------
        terminal : TerminalAdapter
            Provides ``read()``, ``type_text()``, ``send_keys()``.
        poll_interval : float
            Seconds between pane reads.
        read_lines : int
            Number of terminal lines to capture per read.
        """
        adapter = TranscriptAdapter()
        pending_text = None

        # If state is READY, move to RUNNING
        if state.top_state == TopState.READY:
            state.top_state = TopState.RUNNING
            self.store.save(state)
            pending_text = terminal.read(lines=read_lines)
            if not adapter.parse_checkpoint(pending_text):
                node = spec.get_node(state.current_node_id)
                terminal.inject(self._build_instruction(node, state))
                pending_text = None

        while not self.is_final(state) and state.top_state != TopState.PAUSED_FOR_HUMAN:
            # 1. Read pane output
            text = pending_text if pending_text is not None else terminal.read(lines=read_lines)
            pending_text = None

            # 2. Parse checkpoint (compare full dict to detect status changes)
            checkpoint = adapter.parse_checkpoint(text)
            if not checkpoint or checkpoint == state.last_agent_checkpoint:
                time.sleep(poll_interval)
                continue
            logger.info("checkpoint: %s", checkpoint.get("summary", ""))

            # 3. Build event
            event = {"type": "agent_output", "payload": {"checkpoint": checkpoint}}
            self.store.append_event(event)
            self.handle_event(state, event)

            # 4. Gate
            if state.top_state == TopState.GATING:
                decision = self.gate(spec, state)
                self.store.append_decision(decision)
                self.apply_decision(spec, state, decision)
                logger.info("gate decision: %s", decision.get("decision"))

            # 5. Verify
            if state.top_state == TopState.VERIFYING:
                verification = self.verify_current_node(spec, state)
                self.store.append_event({"type": "verification_finished", "payload": verification})
                self.apply_verification(spec, state, verification)
                logger.info("verification ok=%s, state=%s", verification.get("ok"), state.top_state.value)

            # 6. Inject next instruction if continuing
            if state.top_state == TopState.RUNNING:
                node = spec.get_node(state.current_node_id)
                instruction = self._build_instruction(node, state)
                terminal.inject(instruction)

            # 7. Persist
            self.store.save(state)

        return state

    def _build_instruction(self, node, state) -> str:
        """Compose instruction to inject into the agent pane.

        Always starts with the node objective.  Appends context only
        when the gate decision includes a meaningful next_instruction
        (e.g. retry guidance, verification failure details).
        Does NOT repeat the checkpoint protocol — the agent knows it
        from Skill/AGENTS.md.
        """
        parts = [node.objective]

        # Append gate context only if it adds real information
        next_inst = state.last_decision.get("next_instruction", "")
        if next_inst and next_inst != node.objective:
            # Skip generic "continue" boilerplate
            generic_phrases = ["Continue with the highest-priority", "Do not ask the user"]
            if not any(p in next_inst for p in generic_phrases):
                parts.append(next_inst)

        # Append verification failure details on retry
        verification = state.verification or {}
        if state.current_attempt > 0 and not verification.get("ok", True):
            failed = [r for r in verification.get("results", []) if not r.get("ok")]
            if failed:
                details = "; ".join(
                    f"{r.get('type', '?')}: {r.get('stderr', r.get('reason', ''))[:200]}"
                    for r in failed[:3]
                )
                parts.append(f"Previous verification failed: {details}")

        return " ".join(parts)
