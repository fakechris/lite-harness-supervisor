from __future__ import annotations

from supervisor.domain.enums import TopState, DecisionType
from supervisor.domain.state_machine import FINAL_STATES
from supervisor.gates.continue_gate import ContinueGate
from supervisor.llm.judge_client import JudgeClient
from supervisor.verifiers.suite import VerifierSuite

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
    def __init__(self, store):
        self.store = store
        self.judge_client = JudgeClient()
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
        kind = decision["decision"]

        if kind == DecisionType.CONTINUE.value:
            state.top_state = TopState.RUNNING
            return

        if kind == DecisionType.VERIFY_STEP.value:
            state.top_state = TopState.VERIFYING
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
