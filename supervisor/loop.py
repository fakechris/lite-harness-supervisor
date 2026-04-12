from __future__ import annotations

import logging
import signal
import time

from supervisor.domain.enums import TopState, DecisionType
from supervisor.domain.models import (
    Checkpoint, SupervisorDecision, HandoffInstruction,
    WorkerProfile, SupervisionPolicy, RoutingDecision, AcceptanceContract,
)
from supervisor.domain.state_machine import FINAL_STATES
from supervisor.gates.continue_gate import ContinueGate
from supervisor.gates.branch_gate import BranchGate
from supervisor.gates.finish_gate import FinishGate
from supervisor.llm.judge_client import JudgeClient
from supervisor.verifiers.suite import VerifierSuite
from supervisor.adapters.transcript_adapter import TranscriptAdapter
from supervisor.instructions.composer import InstructionComposer
from supervisor.gates.supervision_policy import SupervisionPolicyEngine
from supervisor.history import latest_oracle_consultation_id_for_run
from supervisor.interventions import AutoInterventionManager
from supervisor.notifications import NotificationEvent, NotificationManager
from supervisor.pause_summary import latest_human_escalation, summarize_state
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
                 judge_temperature: float = 0.1, judge_max_tokens: int = 512,
                 worker_profile: WorkerProfile | None = None,
                 notification_manager: NotificationManager | None = None,
                 auto_intervention_manager: AutoInterventionManager | None = None):
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
        self.policy_engine = SupervisionPolicyEngine()
        self.worker_profile = worker_profile or WorkerProfile()
        self.notification_manager = notification_manager or NotificationManager()
        self.auto_intervention_manager = auto_intervention_manager or AutoInterventionManager(mode="notify_only")

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

    def gate(self, spec, state, *, triggered_by_seq: int = 0,
             triggered_by_checkpoint_id: str = "") -> SupervisorDecision:
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
                triggered_by_checkpoint_id=triggered_by_checkpoint_id,
            )
        if cp_status == "step_done":
            return SupervisorDecision.make(
                decision=DecisionType.VERIFY_STEP.value,
                reason="checkpoint says step_done",
                gate_type="checkpoint_status",
                confidence=1.0,
                triggered_by_seq=triggered_by_seq,
                triggered_by_checkpoint_id=triggered_by_checkpoint_id,
            )
        if cp_status == "workflow_done":
            return SupervisorDecision.make(
                decision=DecisionType.VERIFY_STEP.value,
                reason="checkpoint says workflow_done",
                gate_type="checkpoint_status",
                confidence=1.0,
                triggered_by_seq=triggered_by_seq,
                triggered_by_checkpoint_id=triggered_by_checkpoint_id,
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
                self._pause_for_human(state, {
                    "reason": (
                        f"retry budget exhausted for node {state.current_node_id} "
                        f"(attempt {state.current_attempt}/{state.retry_budget.per_node}, "
                        f"global {state.retry_budget.used_global}/{state.retry_budget.global_limit})"
                    ),
                    "node_id": state.current_node_id,
                    "current_attempt": state.current_attempt,
                })
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
            self._pause_for_human(
                state,
                decision.to_dict() if hasattr(decision, "to_dict") else decision,
            )
            # Create RoutingDecision for audit trail
            decision_id = (
                decision.decision_id if hasattr(decision, "decision_id")
                else decision.get("decision_id", "") if isinstance(decision, dict)
                else ""
            )
            routing = RoutingDecision(
                target_type="human",
                scope="single_question",
                reason=decision.reason if hasattr(decision, "reason") else str(decision.get("reason", "")),
                triggered_by_decision_id=decision_id,
                consultation_id=latest_oracle_consultation_id_for_run(
                    state.run_id if hasattr(state, "run_id") else "",
                    str(getattr(self.store, "runtime_root", ".supervisor/runtime")),
                ),
            )
            self.store.append_session_event(
                state.run_id if hasattr(state, "run_id") else "",
                "routing", routing.to_dict(),
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
            completed_node_id = state.current_node_id
            if state.current_node_id not in state.done_node_ids:
                state.done_node_ids.append(state.current_node_id)
            next_id = spec.next_node_id(state.current_node_id)
            if next_id is None:
                finish = self.finish_gate.evaluate(spec, state, cwd=cwd)
                if finish["ok"]:
                    state.top_state = TopState.COMPLETED
                    self._notify_transition(
                        state,
                        event_type="run_completed",
                        reason="workflow completed",
                        next_action=f"thin-supervisor run summarize {state.run_id}",
                    )
                else:
                    self._pause_for_human(state, finish)
            else:
                state.current_node_id = next_id
                state.current_attempt = 0
                state.top_state = TopState.RUNNING
                self._notify_transition(
                    state,
                    event_type="step_verified",
                    reason=f"verified {completed_node_id}; advanced to {next_id}",
                    next_action=f"continue current_node {next_id}",
                )
            return
        state.current_attempt += 1
        state.retry_budget.used_global += 1
        if (state.current_attempt >= state.retry_budget.per_node
                or state.retry_budget.used_global >= state.retry_budget.global_limit):
            self._pause_for_human(state, {
                "reason": (
                    f"verification retry budget exhausted for node {state.current_node_id} "
                    f"(attempt {state.current_attempt}/{state.retry_budget.per_node}, "
                    f"global {state.retry_budget.used_global}/{state.retry_budget.global_limit})"
                ),
                "node_id": state.current_node_id,
                "verification": verification,
            })
        else:
            state.top_state = TopState.RUNNING

    def _pause_for_human(self, state, payload: dict | None = None) -> dict:
        details = dict(payload or {})
        state.top_state = TopState.PAUSED_FOR_HUMAN
        state.human_escalations.append(details)
        summary = summarize_state(state.to_dict())
        event_payload = dict(details)
        event_payload["pause_reason"] = summary.get("pause_reason", "")
        event_payload["next_action"] = summary.get("next_action", "")
        event_payload["is_waiting_for_review"] = summary.get("is_waiting_for_review", False)
        self.store.append_session_event(state.run_id, "human_pause", event_payload)
        self.notification_manager.notify(NotificationEvent(
            event_type="human_pause",
            run_id=state.run_id,
            top_state=state.top_state.value,
            reason=event_payload["pause_reason"],
            next_action=event_payload["next_action"],
            pane_target=state.pane_target,
            spec_path=state.spec_path,
            workspace_root=state.workspace_root,
            surface_type=state.surface_type,
        ))
        return event_payload

    def _notify_transition(self, state, *, event_type: str, reason: str, next_action: str) -> None:
        payload = {
            "reason": reason,
            "next_action": next_action,
            "top_state": state.top_state.value,
            "current_node": state.current_node_id,
        }
        self.store.append_session_event(state.run_id, event_type, payload)
        self.notification_manager.notify(NotificationEvent(
            event_type=event_type,
            run_id=state.run_id,
            top_state=state.top_state.value,
            reason=reason,
            next_action=next_action,
            pane_target=state.pane_target,
            spec_path=state.spec_path,
            workspace_root=state.workspace_root,
            surface_type=state.surface_type,
        ))

    def _attempt_auto_intervention(self, spec, state, terminal, payload: dict | None) -> bool:
        plan = self.auto_intervention_manager.maybe_plan(spec, state, payload or {}, terminal)
        if not plan:
            return False
        try:
            terminal.inject(plan.instruction)
        except Exception:
            logger.exception("auto intervention injection failed")
            return False

        state.auto_intervention_count += 1
        state.top_state = TopState.RUNNING
        record = {
            "reason": plan.reason,
            "instruction": plan.instruction,
            "count": state.auto_intervention_count,
            "resumed_node": state.current_node_id,
        }
        self.store.append_session_event(state.run_id, "auto_intervention", record)
        self.notification_manager.notify(NotificationEvent(
            event_type="auto_intervention",
            run_id=state.run_id,
            top_state=state.top_state.value,
            reason=plan.reason,
            next_action=f"continue current_node {state.current_node_id}",
            pane_target=state.pane_target,
            spec_path=state.spec_path,
            workspace_root=state.workspace_root,
            surface_type=state.surface_type,
        ))
        self.store.save(state)
        return True

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
        poll_interval, read_lines,
        node_mismatch_count, max_node_mismatch, interrupted_ref,
    ):
        pending_text = None

        # Compute supervision policy based on worker + contract + state
        contract = spec.acceptance or AcceptanceContract.from_finish_policy(spec.finish_policy, goal=spec.goal)
        policy = self.policy_engine.determine(self.worker_profile, contract, state)
        logger.info("supervision policy: %s (%s)", policy.mode, policy.reason)

        # READY → RUNNING: inject first instruction
        if state.top_state == TopState.READY:
            state.top_state = TopState.RUNNING
            self.store.save(state)
            pending_text = terminal.read(lines=read_lines)
            cp = adapter.parse_checkpoint(pending_text, run_id=state.run_id, surface_id=surface_id)
            # #2: validate run_id on startup to avoid stale pane content
            if cp and cp.run_id and cp.run_id != state.run_id:
                cp = None  # stale checkpoint from previous run
            if cp:
                # The pane/session is already emitting progress for the current
                # node, so avoid re-injecting the same instruction on the first
                # working checkpoint.
                state.last_injected_node_id = state.current_node_id
                state.last_injected_attempt = state.current_attempt
            if not cp:
                # Skip init inject for observation-only surfaces (agent already running)
                if not getattr(terminal, "is_observation_only", False):
                    node = spec.get_node(state.current_node_id)
                    instruction = self.composer.build(
                        node, state,
                        triggered_by_decision_id="",
                        trigger_type="init",
                        policy=policy,
                    )
                    state.last_injected_node_id = state.current_node_id
                    state.last_injected_attempt = 0
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
            checkpoints = adapter.parse_checkpoints(text, run_id=state.run_id, surface_id=surface_id)
            if not checkpoints:
                time.sleep(poll_interval)
                continue

            restart_loop = False
            for checkpoint in checkpoints:
                # #2: reject checkpoints from wrong run
                if checkpoint.run_id and checkpoint.run_id != state.run_id:
                    continue

                # #7: seq-based dedup with reset tolerance
                if checkpoint.checkpoint_seq > 0:
                    if checkpoint.checkpoint_seq <= state.checkpoint_seq:
                        # Allow seq reset if gap is large (agent restarted)
                        if state.checkpoint_seq - checkpoint.checkpoint_seq < 100:
                            continue
                # Content-based dedup
                last_cp = state.last_agent_checkpoint
                if (last_cp
                        and checkpoint.status == last_cp.get("status")
                        and checkpoint.current_node == last_cp.get("current_node")
                        and checkpoint.summary == last_cp.get("summary")
                        and checkpoint.checkpoint_seq == last_cp.get("checkpoint_seq", 0)):
                    continue

                # #5: node mismatch — observation-only surfaces cannot rely on
                # injected instructions, so bind unknown nodes to the current spec
                # node but escalate if the agent is still reporting an already-done
                # node (delivery clearly stalled).
                if checkpoint.current_node != state.current_node_id:
                    if getattr(terminal, "is_observation_only", False):
                        if checkpoint.current_node in state.done_node_ids:
                            reason = (
                                "observation-only surface is still reporting a completed node; "
                                "supervisor instruction was likely not delivered"
                            )
                            logger.warning("%s: cp=%s state=%s",
                                           reason, checkpoint.current_node, state.current_node_id)
                            payload = {
                                "reason": reason,
                                "checkpoint_node": checkpoint.current_node,
                                "state_node": state.current_node_id,
                            }
                            self.store.append_session_event(
                                state.run_id, "observation_delivery_stalled", payload
                            )
                            pause_payload = self._pause_for_human(state, payload)
                            self._attempt_auto_intervention(spec, state, terminal, pause_payload)
                            self.store.save(state)
                            return
                        logger.info(
                            "observation-only: rebinding checkpoint node cp=%s -> state=%s",
                            checkpoint.current_node, state.current_node_id,
                        )
                        checkpoint.current_node = state.current_node_id
                    else:
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
                            pause_payload = self._pause_for_human(state, {
                                "reason": f"node mismatch persisted for {node_mismatch_count} checkpoints",
                                "checkpoint_node": checkpoint.current_node,
                                "state_node": state.current_node_id,
                            })
                            if self._attempt_auto_intervention(spec, state, terminal, pause_payload):
                                node_mismatch_count = 0
                                restart_loop = True
                                break
                            self.store.save(state)
                            return
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
                    decision = self.gate(
                        spec, state,
                        triggered_by_seq=checkpoint.checkpoint_seq,
                        triggered_by_checkpoint_id=checkpoint.checkpoint_id,
                    )
                    self.store.append_decision(decision.to_dict())
                    self.store.append_session_event(state.run_id, "gate_decision", decision.to_dict())
                    self.apply_decision(spec, state, decision)
                    logger.info("decision: %s (id=%s)", decision.decision, decision.decision_id)
                    if state.top_state == TopState.PAUSED_FOR_HUMAN:
                        pause_payload = latest_human_escalation(state.to_dict())
                        if self._attempt_auto_intervention(spec, state, terminal, pause_payload):
                            restart_loop = True
                            break

                # 5. Verify
                if state.top_state == TopState.VERIFYING:
                    cwd = self._get_cwd(terminal, state)
                    try:
                        verification = self.verify_current_node(spec, state, cwd=cwd)
                    except Exception as e:
                        logger.error("verification error: %s", e)
                        verification = {"ok": False, "results": [{"type": "error", "ok": False, "reason": str(e)}]}
                    self.store.append_event({"type": "verification_finished", "payload": verification})
                    self.store.append_session_event(state.run_id, "verification", verification)
                    self.apply_verification(spec, state, verification, cwd=cwd)
                    logger.info("verification ok=%s, state=%s", verification.get("ok"), state.top_state.value)
                    if state.top_state == TopState.PAUSED_FOR_HUMAN:
                        pause_payload = latest_human_escalation(state.to_dict())
                        if self._attempt_auto_intervention(spec, state, terminal, pause_payload):
                            restart_loop = True
                            break

                # 6. Inject — #11: save BEFORE inject
                if state.top_state == TopState.RUNNING:
                    node_changed = state.current_node_id != state.last_injected_node_id
                    new_retry = state.current_attempt > 0 and state.current_attempt != state.last_injected_attempt
                    if node_changed or new_retry:
                        node = spec.get_node(state.current_node_id)
                        decision_id = decision.decision_id if decision else ""
                        trigger = "retry" if new_retry else ("branch" if decision and decision.decision.upper() == "BRANCH" else "node_advance")
                        # Re-evaluate policy (node advance resets attempt, failures escalate)
                        policy = self.policy_engine.determine(self.worker_profile, contract, state)
                        instruction = self.composer.build(
                            node, state,
                            triggered_by_decision_id=decision_id,
                            trigger_type=trigger,
                            policy=policy,
                        )
                        # Persist the selected next node before delivery so a
                        # crash cannot replay the previous instruction.
                        state.last_injected_node_id = state.current_node_id
                        state.last_injected_attempt = state.current_attempt
                        self.store.save(state)
                        if not self._inject_or_pause(state, terminal, instruction):
                            return
                        logger.info("injected: %s (id=%s, trigger=%s)", node.id, instruction.instruction_id, trigger)
                        if state.top_state == TopState.PAUSED_FOR_HUMAN:
                            return
                        restart_loop = True
                        break

            # 7. Persist + progress
            self.store.save(state)
            if hasattr(terminal, "consume_checkpoint"):
                try:
                    terminal.consume_checkpoint()
                except Exception as exc:
                    logger.debug("consume_checkpoint failed: %s", exc)
            try:
                write_progress(state, spec, str(self.store.runtime_dir))
            except Exception:
                pass  # progress is best-effort
            if restart_loop:
                continue

    def _get_cwd(self, terminal, state=None) -> str | None:
        if hasattr(terminal, "current_cwd"):
            try:
                cwd = terminal.current_cwd()
                if cwd:
                    return cwd
            except Exception:
                pass
        # Fallback to persisted workspace_root
        if state and state.workspace_root:
            return state.workspace_root
        return None

    def _inject_or_pause(self, state, terminal, instruction) -> bool:
        # Observation-only surfaces (e.g., JSONL) — write instruction but don't
        # expect it to be delivered. Log it and continue observing.
        if getattr(terminal, "is_observation_only", False):
            try:
                terminal.inject(instruction.content)  # writes file, logs warning
            except Exception as exc:
                logger.warning("observation-only inject failed: %s", exc)
            self.store.append_session_event(
                state.run_id, "injection_observation_only", instruction.to_dict()
            )
            return True  # don't pause — keep observing

        try:
            terminal.inject(instruction.content)
        except Exception as exc:
            payload = {
                "instruction_id": instruction.instruction_id,
                "node_id": state.current_node_id,
                "error": str(exc),
            }
            self.store.append_session_event(state.run_id, "injection_failed", payload)
            pause_payload = self._pause_for_human(state, {
                "reason": str(exc),
                "node_id": state.current_node_id,
                "instruction_id": instruction.instruction_id,
            })
            self._attempt_auto_intervention(None, state, terminal, pause_payload)
            self.store.save(state)
            return False

        self.store.append_session_event(
            state.run_id, "injection", instruction.to_dict()
        )
        return True
