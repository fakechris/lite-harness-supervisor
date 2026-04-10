from __future__ import annotations

import logging
import signal
import time

from supervisor.domain.enums import TopState, DecisionType
from supervisor.domain.models import Checkpoint, SupervisorDecision, HandoffInstruction
from supervisor.domain.state_machine import FINAL_STATES
from supervisor.gates.continue_gate import ContinueGate
from supervisor.gates.branch_gate import BranchGate
from supervisor.gates.finish_gate import FinishGate
from supervisor.llm.judge_client import JudgeClient
from supervisor.verifiers.suite import VerifierSuite
from supervisor.adapters.transcript_adapter import TranscriptAdapter
from supervisor.instructions.composer import InstructionComposer
from supervisor.progress import write_progress

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

    def gate(self, spec, state, *, triggered_by_seq: int = 0) -> SupervisorDecision:
        node = spec.get_node(state.current_node_id)
        if node.type == "decision":
            return self.branch_gate.decide(spec, state, node, triggered_by_seq=triggered_by_seq)

        cp = state.last_agent_checkpoint or {}
        cp_status = cp.get("status", "")

        if cp_status == "blocked":
            return SupervisorDecision.make(
                decision=DecisionType.ESCALATE_TO_HUMAN.value,
                reason="checkpoint says blocked",
                gate_type="checkpoint_status",
                confidence=1.0,
                needs_human=True,
                triggered_by_seq=triggered_by_seq,
            )
        if cp_status == "step_done":
            return SupervisorDecision.make(
                decision=DecisionType.VERIFY_STEP.value,
                reason="checkpoint says step_done",
                gate_type="checkpoint_status",
                confidence=1.0,
                triggered_by_seq=triggered_by_seq,
            )
        if cp_status == "workflow_done":
            return SupervisorDecision.make(
                decision=DecisionType.VERIFY_STEP.value,
                reason="checkpoint says workflow_done",
                gate_type="checkpoint_status",
                confidence=1.0,
                triggered_by_seq=triggered_by_seq,
            )
        return self.continue_gate.decide(build_context(spec, state), triggered_by_seq=triggered_by_seq)

    def verify_current_node(self, spec, state, *, cwd: str | None = None) -> dict:
        node = spec.get_node(state.current_node_id)
        # Node is "done" if already in done list OR if checkpoint says step_done
        # (the node gets added to done_node_ids after verification passes)
        cp_status = (state.last_agent_checkpoint or {}).get("status", "")
        node_done = (
            state.current_node_id in state.done_node_ids
            or cp_status in ("step_done", "workflow_done")
        )
        context = {"current_node_done": node_done}
        return self.verifier_suite.run(node.verify, context, cwd=cwd)

    def apply_decision(self, spec, state, decision: SupervisorDecision | dict):
        if isinstance(decision, dict):
            state.last_decision = decision
            kind = decision["decision"].upper()
        else:
            state.last_decision = decision.to_dict()
            kind = decision.decision.upper()

        if kind == DecisionType.CONTINUE.value:
            state.top_state = TopState.RUNNING
            return
        if kind == DecisionType.VERIFY_STEP.value:
            state.top_state = TopState.VERIFYING
            return
        if kind == DecisionType.RETRY.value:
            state.current_attempt += 1
            state.retry_budget.used_global += 1
            if (state.current_attempt >= state.retry_budget.per_node
                    or state.retry_budget.used_global >= state.retry_budget.global_limit):
                state.top_state = TopState.PAUSED_FOR_HUMAN
            else:
                state.top_state = TopState.RUNNING
            return
        if kind == DecisionType.BRANCH.value:
            _get = decision.get if isinstance(decision, dict) else lambda k, d=None: getattr(decision, k, d)
            state.branch_history.append({
                "node_id": state.current_node_id,
                "selected_branch": _get("selected_branch"),
                "next_node_id": _get("next_node_id"),
                "reason": _get("reason"),
            })
            state.current_node_id = _get("next_node_id")
            state.current_attempt = 0
            state.top_state = TopState.RUNNING
            return
        if kind == DecisionType.ESCALATE_TO_HUMAN.value:
            state.top_state = TopState.PAUSED_FOR_HUMAN
            state.human_escalations.append(
                decision.to_dict() if hasattr(decision, "to_dict") else decision
            )
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
        if (state.current_attempt >= state.retry_budget.per_node
                or state.retry_budget.used_global >= state.retry_budget.global_limit):
            state.top_state = TopState.PAUSED_FOR_HUMAN
        else:
            state.top_state = TopState.RUNNING

    def is_final(self, state) -> bool:
        return state.top_state in FINAL_STATES

    # ------------------------------------------------------------------
    # Sidecar loop
    # ------------------------------------------------------------------

    def run_sidecar(self, spec, state, terminal, *, poll_interval: float = 2.0,
                    read_lines: int = 100, stop_event=None):
        """Main sidecar event loop with full causality chain.

        Checkpoint → SupervisorDecision → HandoffInstruction
        Each object carries IDs linking back to its trigger.

        Parameters
        ----------
        stop_event : threading.Event | None
            External stop signal (used by daemon to stop individual runs).
            If None, SIGTERM handler is installed (foreground mode).
        """
        adapter = TranscriptAdapter()
        pending_text = None
        last_injected_attempt = -1
        node_mismatch_count = 0
        max_node_mismatch = 5
        interrupted = False

        surface_id = ""
        if hasattr(terminal, "session_id"):
            try:
                surface_id = terminal.session_id()
            except Exception:
                pass

        # Interrupt mechanism: external stop_event OR SIGTERM handler
        if stop_event is not None:
            interrupted_ref = stop_event.is_set
        else:
            def _sigterm_handler(signum, frame):
                nonlocal interrupted
                interrupted = True
                logger.info("SIGTERM received, saving state and exiting")

            try:
                prev_handler = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGTERM, _sigterm_handler)
            except ValueError:
                prev_handler = None  # not main thread
            interrupted_ref = lambda: interrupted

        try:
            self._run_sidecar_inner(
                spec, state, terminal, adapter, surface_id,
                poll_interval=poll_interval, read_lines=read_lines,
                last_injected_attempt=last_injected_attempt,
                node_mismatch_count=node_mismatch_count,
                max_node_mismatch=max_node_mismatch,
                interrupted_ref=interrupted_ref,
            )
        except Exception:
            logger.exception("sidecar loop error")
            self.store.save(state)
            raise
        finally:
            if stop_event is None and prev_handler is not None:
                try:
                    signal.signal(signal.SIGTERM, prev_handler)
                except ValueError:
                    pass  # not main thread

        return state

    def _run_sidecar_inner(
        self, spec, state, terminal, adapter, surface_id, *,
        poll_interval, read_lines, last_injected_attempt,
        node_mismatch_count, max_node_mismatch, interrupted_ref,
    ):
        pending_text = None

        # READY → RUNNING: inject first instruction
        if state.top_state == TopState.READY:
            state.top_state = TopState.RUNNING
            self.store.save(state)
            pending_text = terminal.read(lines=read_lines)
            cp = adapter.parse_checkpoint(pending_text, run_id=state.run_id, surface_id=surface_id)
            # #2: validate run_id on startup to avoid stale pane content
            if cp and cp.run_id and cp.run_id != state.run_id:
                cp = None  # stale checkpoint from previous run
            if not cp:
                node = spec.get_node(state.current_node_id)
                instruction = self.composer.build(
                    node, state,
                    triggered_by_decision_id="",
                    trigger_type="init",
                )
                # #11: save state BEFORE inject to avoid replay on crash
                state.last_injected_node_id = state.current_node_id
                last_injected_attempt = 0
                self.store.save(state)
                if not self._inject_or_pause(state, terminal, instruction):
                    return
                pending_text = None

        while not self.is_final(state) and state.top_state != TopState.PAUSED_FOR_HUMAN:
            if interrupted_ref():
                self.store.save(state)
                return

            # 1. Read pane
            try:
                text = pending_text if pending_text is not None else terminal.read(lines=read_lines)
            except Exception as e:
                logger.warning("terminal read failed: %s", e)
                time.sleep(poll_interval)
                continue
            pending_text = None

            # 2. Parse checkpoint with identity
            checkpoint = adapter.parse_checkpoint(text, run_id=state.run_id, surface_id=surface_id)
            if checkpoint is None:
                time.sleep(poll_interval)
                continue

            # #2: reject checkpoints from wrong run
            if checkpoint.run_id and checkpoint.run_id != state.run_id:
                time.sleep(poll_interval)
                continue

            # #7: seq-based dedup with reset tolerance
            if checkpoint.checkpoint_seq > 0:
                if checkpoint.checkpoint_seq <= state.checkpoint_seq:
                    # Allow seq reset if gap is large (agent restarted)
                    if state.checkpoint_seq - checkpoint.checkpoint_seq < 100:
                        time.sleep(poll_interval)
                        continue
            # Content-based dedup
            last_cp = state.last_agent_checkpoint
            if (last_cp
                    and checkpoint.status == last_cp.get("status")
                    and checkpoint.current_node == last_cp.get("current_node")
                    and checkpoint.summary == last_cp.get("summary")
                    and checkpoint.checkpoint_seq == last_cp.get("checkpoint_seq", 0)):
                time.sleep(poll_interval)
                continue

            # #5: node mismatch — escalate after N consecutive
            if checkpoint.current_node != state.current_node_id:
                node_mismatch_count += 1
                logger.warning("checkpoint node mismatch (%d/%d): cp=%s state=%s",
                               node_mismatch_count, max_node_mismatch,
                               checkpoint.current_node, state.current_node_id)
                self.store.append_session_event(
                    state.run_id, "checkpoint_mismatch",
                    {"checkpoint_node": checkpoint.current_node, "state_node": state.current_node_id,
                     "count": node_mismatch_count},
                )
                if node_mismatch_count >= max_node_mismatch:
                    state.top_state = TopState.PAUSED_FOR_HUMAN
                    state.human_escalations.append({
                        "reason": f"node mismatch persisted for {node_mismatch_count} checkpoints",
                        "checkpoint_node": checkpoint.current_node,
                        "state_node": state.current_node_id,
                    })
                    self.store.save(state)
                    return
                time.sleep(poll_interval)
                continue
            node_mismatch_count = 0  # reset on match

            if checkpoint.checkpoint_seq > 0:
                state.checkpoint_seq = checkpoint.checkpoint_seq
            logger.info("checkpoint: %s (id=%s)", checkpoint.summary, checkpoint.checkpoint_id)

            # 3. Event
            cp_dict = checkpoint.to_dict()
            event = {"type": "agent_output", "payload": {"checkpoint": cp_dict}}
            self.store.append_event(event)
            self.store.append_session_event(state.run_id, "checkpoint", cp_dict)
            self.handle_event(state, event)

            # 4. Gate → SupervisorDecision
            decision: SupervisorDecision | None = None
            if state.top_state == TopState.GATING:
                decision = self.gate(spec, state, triggered_by_seq=checkpoint.checkpoint_seq)
                self.store.append_decision(decision.to_dict())
                self.store.append_session_event(state.run_id, "gate_decision", decision.to_dict())
                self.apply_decision(spec, state, decision)
                logger.info("decision: %s (id=%s)", decision.decision, decision.decision_id)

            # 5. Verify
            if state.top_state == TopState.VERIFYING:
                cwd = self._get_cwd(terminal)
                try:
                    verification = self.verify_current_node(spec, state, cwd=cwd)
                except Exception as e:
                    logger.error("verification error: %s", e)
                    verification = {"ok": False, "results": [{"type": "error", "ok": False, "reason": str(e)}]}
                self.store.append_event({"type": "verification_finished", "payload": verification})
                self.store.append_session_event(state.run_id, "verification", verification)
                self.apply_verification(spec, state, verification, cwd=cwd)
                logger.info("verification ok=%s, state=%s", verification.get("ok"), state.top_state.value)

            # 6. Inject — #11: save BEFORE inject
            if state.top_state == TopState.RUNNING:
                node_changed = state.current_node_id != state.last_injected_node_id
                new_retry = state.current_attempt > 0 and state.current_attempt != last_injected_attempt
                if node_changed or new_retry:
                    node = spec.get_node(state.current_node_id)
                    decision_id = decision.decision_id if decision else ""
                    trigger = "retry" if new_retry else ("branch" if decision and decision.decision.upper() == "BRANCH" else "node_advance")
                    instruction = self.composer.build(
                        node, state,
                        triggered_by_decision_id=decision_id,
                        trigger_type=trigger,
                    )
                    # Save state BEFORE inject to prevent replay on crash
                    state.last_injected_node_id = state.current_node_id
                    last_injected_attempt = state.current_attempt
                    self.store.save(state)
                    if not self._inject_or_pause(state, terminal, instruction):
                        return
                    logger.info("injected: %s (id=%s, trigger=%s)", node.id, instruction.instruction_id, trigger)
                    continue  # skip double save

            # 7. Persist + progress
            self.store.save(state)
            try:
                write_progress(state, spec, str(self.store.runtime_dir))
            except Exception:
                pass  # progress is best-effort

    def _get_cwd(self, terminal) -> str | None:
        if hasattr(terminal, "current_cwd"):
            try:
                return terminal.current_cwd()
            except Exception:
                pass
        return None

    def _inject_or_pause(self, state, terminal, instruction) -> bool:
        try:
            terminal.inject(instruction.content)
        except Exception as exc:
            state.top_state = TopState.PAUSED_FOR_HUMAN
            payload = {
                "instruction_id": instruction.instruction_id,
                "node_id": state.current_node_id,
                "error": str(exc),
            }
            self.store.append_session_event(state.run_id, "injection_failed", payload)
            state.human_escalations.append({
                "reason": str(exc),
                "node_id": state.current_node_id,
                "instruction_id": instruction.instruction_id,
            })
            self.store.save(state)
            return False

        self.store.append_session_event(
            state.run_id, "injection", instruction.to_dict()
        )
        return True
