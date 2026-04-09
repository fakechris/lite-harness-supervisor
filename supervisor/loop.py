from __future__ import annotations

import logging
import time

from supervisor.domain.enums import TopState, DecisionType
from supervisor.domain.models import Checkpoint
from supervisor.domain.state_machine import FINAL_STATES
from supervisor.gates.continue_gate import ContinueGate
from supervisor.gates.branch_gate import BranchGate
from supervisor.gates.finish_gate import FinishGate
from supervisor.llm.judge_client import JudgeClient
from supervisor.verifiers.suite import VerifierSuite
from supervisor.adapters.transcript_adapter import TranscriptAdapter
from supervisor.instructions.composer import InstructionComposer

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
        self.branch_gate = BranchGate(self.judge_client)
        self.finish_gate = FinishGate()
        self.verifier_suite = VerifierSuite()
        self.composer = InstructionComposer()

    def handle_event(self, state, event):
        state.last_event = event
        if event["type"] == "agent_output":
            cp = event.get("payload", {}).get("checkpoint")
            if cp:
                if isinstance(cp, Checkpoint):
                    state.last_agent_checkpoint = cp.to_dict()
                else:
                    state.last_agent_checkpoint = cp
                state.top_state = TopState.GATING
        elif event["type"] == "agent_ask":
            state.top_state = TopState.GATING
        elif event["type"] in {"agent_stop", "timeout"}:
            state.top_state = TopState.GATING

    def gate(self, spec, state) -> dict:
        # A: check for decision nodes first
        node = spec.get_node(state.current_node_id)
        if node.type == "decision":
            return self.branch_gate.decide(spec, state, node)

        cp = state.last_agent_checkpoint or {}
        if cp.get("status") == "step_done":
            return {"decision": DecisionType.VERIFY_STEP.value, "reason": "checkpoint says step_done"}
        if cp.get("status") == "workflow_done":
            return {"decision": DecisionType.VERIFY_STEP.value, "reason": "checkpoint says workflow_done"}
        return self.continue_gate.decide(build_context(spec, state))

    def verify_current_node(self, spec, state, *, cwd: str | None = None) -> dict:
        node = spec.get_node(state.current_node_id)
        context = {
            "current_node_done": state.current_node_id in state.done_node_ids
        }
        return self.verifier_suite.run(node.verify, context, cwd=cwd)

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

        # A: branch handling
        if kind == DecisionType.BRANCH.value:
            state.branch_history.append({
                "node_id": state.current_node_id,
                "selected_branch": decision.get("selected_branch"),
                "next_node_id": decision.get("next_node_id"),
                "reason": decision.get("reason"),
            })
            state.current_node_id = decision["next_node_id"]
            state.current_attempt = 0
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

    def apply_verification(self, spec, state, verification: dict, *, cwd: str | None = None):
        state.verification = verification
        if verification["ok"]:
            if state.current_node_id not in state.done_node_ids:
                state.done_node_ids.append(state.current_node_id)
            next_id = spec.next_node_id(state.current_node_id)
            if next_id is None:
                # F: enforce finish_policy before COMPLETED
                finish = self.finish_gate.evaluate(spec, state, cwd=cwd)
                if finish["ok"]:
                    state.top_state = TopState.COMPLETED
                else:
                    state.top_state = TopState.PAUSED_FOR_HUMAN
                    state.human_escalations.append(finish)
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
    # Sidecar loop
    # ------------------------------------------------------------------

    def run_sidecar(self, spec, state, terminal, *, poll_interval: float = 2.0, read_lines: int = 100):
        """Main sidecar event loop.

        Reads the agent's tmux pane, parses checkpoints, gates, verifies,
        and injects the next instruction.  Runs until a final state or
        ``PAUSED_FOR_HUMAN``.

        Parameters
        ----------
        terminal : SessionAdapter
            Provides ``read()``, ``inject()``, ``current_cwd()``, ``session_id()``.
        poll_interval : float
            Seconds between pane reads.
        read_lines : int
            Number of terminal lines to capture per read.
        """
        adapter = TranscriptAdapter()
        pending_text = None

        # If state is READY, move to RUNNING and inject first instruction
        if state.top_state == TopState.READY:
            state.top_state = TopState.RUNNING
            self.store.save(state)
            pending_text = terminal.read(lines=read_lines)
            if not adapter.parse_checkpoint(pending_text):
                node = spec.get_node(state.current_node_id)
                instruction = self.composer.build(node, state)
                terminal.inject(instruction)
                state.last_injected_node_id = state.current_node_id
                self.store.append_session_event(
                    state.run_id, "injection", {"node_id": node.id, "instruction": instruction}
                )
                pending_text = None

        while not self.is_final(state) and state.top_state != TopState.PAUSED_FOR_HUMAN:
            # 1. Read pane output
            text = pending_text if pending_text is not None else terminal.read(lines=read_lines)
            pending_text = None

            # 2. Parse checkpoint
            checkpoint = adapter.parse_checkpoint(text)
            if checkpoint is None:
                time.sleep(poll_interval)
                continue

            # C: seq-based dedup
            if checkpoint.checkpoint_seq > 0 and checkpoint.checkpoint_seq <= state.checkpoint_seq:
                time.sleep(poll_interval)
                continue
            # Fallback dedup: compare dict
            cp_dict = checkpoint.to_dict()
            if cp_dict == state.last_agent_checkpoint:
                time.sleep(poll_interval)
                continue

            # C: validate checkpoint.current_node matches state
            if checkpoint.current_node != state.current_node_id:
                logger.warning(
                    "checkpoint node mismatch: checkpoint=%s state=%s (skipping)",
                    checkpoint.current_node, state.current_node_id,
                )
                self.store.append_session_event(
                    state.run_id, "checkpoint_mismatch",
                    {"checkpoint_node": checkpoint.current_node, "state_node": state.current_node_id},
                )
                time.sleep(poll_interval)
                continue

            if checkpoint.checkpoint_seq > 0:
                state.checkpoint_seq = checkpoint.checkpoint_seq

            logger.info("checkpoint: %s", checkpoint.summary)

            # 3. Build event
            event = {"type": "agent_output", "payload": {"checkpoint": cp_dict}}
            self.store.append_event(event)
            self.store.append_session_event(state.run_id, "checkpoint", cp_dict)
            self.handle_event(state, event)

            # 4. Gate
            if state.top_state == TopState.GATING:
                decision = self.gate(spec, state)
                self.store.append_decision(decision)
                self.store.append_session_event(state.run_id, "gate_decision", decision)
                self.apply_decision(spec, state, decision)
                logger.info("gate decision: %s", decision.get("decision"))

            # 5. Verify (E: pass cwd from terminal)
            if state.top_state == TopState.VERIFYING:
                cwd = self._get_cwd(terminal)
                verification = self.verify_current_node(spec, state, cwd=cwd)
                self.store.append_event({"type": "verification_finished", "payload": verification})
                self.store.append_session_event(state.run_id, "verification", verification)
                self.apply_verification(spec, state, verification, cwd=cwd)
                logger.info("verification ok=%s, state=%s", verification.get("ok"), state.top_state.value)

            # 6. B: event-driven injection — only inject on node change or retry
            if state.top_state == TopState.RUNNING:
                should_inject = (
                    state.current_node_id != state.last_injected_node_id
                    or state.current_attempt > 0
                )
                if should_inject:
                    node = spec.get_node(state.current_node_id)
                    instruction = self.composer.build(node, state)
                    terminal.inject(instruction)
                    state.last_injected_node_id = state.current_node_id
                    self.store.append_session_event(
                        state.run_id, "injection", {"node_id": node.id, "instruction": instruction}
                    )

            # 7. Persist
            self.store.save(state)

        return state

    def _get_cwd(self, terminal) -> str | None:
        """Get pane cwd if terminal supports it."""
        if hasattr(terminal, "current_cwd"):
            try:
                return terminal.current_cwd()
            except Exception:
                pass
        return None
