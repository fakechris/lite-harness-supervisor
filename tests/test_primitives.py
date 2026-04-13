"""Tests for first-class primitives: typed objects and causality chain."""
from supervisor.domain.models import (
    Checkpoint, SupervisorDecision, HandoffInstruction,
)
from supervisor.domain.session import SessionRun
from supervisor.domain.enums import TopState
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.gates.continue_gate import ContinueGate
from supervisor.llm.judge_client import JudgeClient
from supervisor.instructions.composer import InstructionComposer


class TestCheckpointIdentity:
    def test_auto_generates_id_and_timestamp(self):
        cp = Checkpoint(status="working", current_node="step1", summary="test")
        assert cp.checkpoint_id.startswith("cp_")
        assert cp.timestamp != ""

    def test_preserves_explicit_id(self):
        cp = Checkpoint(
            status="working", current_node="step1", summary="test",
            checkpoint_id="cp_custom123",
        )
        assert cp.checkpoint_id == "cp_custom123"

    def test_to_dict_round_trip(self):
        cp = Checkpoint(status="step_done", current_node="n1", summary="done", run_id="run_x", checkpoint_seq=5)
        d = cp.to_dict()
        assert d["status"] == "step_done"
        assert d["checkpoint_id"].startswith("cp_")
        assert d["run_id"] == "run_x"


class TestSupervisorDecision:
    def test_make_generates_id(self):
        dec = SupervisorDecision.make(
            decision="CONTINUE", reason="test", gate_type="continue",
        )
        assert dec.decision_id.startswith("dec_")
        assert dec.decision == "CONTINUE"
        assert dec.timestamp != ""

    def test_causality_link(self):
        dec = SupervisorDecision.make(
            decision="VERIFY_STEP", reason="step_done", gate_type="checkpoint_status",
            triggered_by_seq=7,
        )
        assert dec.triggered_by_seq == 7


class TestHandoffInstruction:
    def test_make_generates_id(self):
        inst = HandoffInstruction.make(
            content="implement feature",
            node_id="step2",
            current_attempt=0,
            triggered_by_decision_id="dec_abc",
            trigger_type="node_advance",
        )
        assert inst.instruction_id.startswith("ins_")
        assert inst.triggered_by_decision_id == "dec_abc"

    def test_composer_returns_instruction(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        composer = InstructionComposer()

        node = spec.get_node("write_test")
        inst = composer.build(
            node, state,
            triggered_by_decision_id="dec_xyz",
            trigger_type="init",
        )
        assert isinstance(inst, HandoffInstruction)
        assert inst.content.startswith("write a failing test")
        assert "current_node: write_test" in inst.content
        assert "<checkpoint>" in inst.content
        assert "run_id: <run_id>" in inst.content
        assert "checkpoint_seq: <incrementing integer>" in inst.content
        assert "status: <working | blocked | step_done | workflow_done>" in inst.content
        assert "\n\n" in inst.content
        assert inst.trigger_type == "init"
        assert inst.triggered_by_decision_id == "dec_xyz"

    def test_composer_preserves_continue_guidance_for_continue_trigger(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        state.last_decision = {
            "decision": "CONTINUE",
            "decision_id": "dec_continue",
            "next_instruction": (
                "Continue with the highest-priority remaining action in the current node. "
                "Do not ask the user for confirmation unless blocked by missing authority, "
                "missing external input, or destructive irreversible action."
            ),
        }
        composer = InstructionComposer()

        node = spec.get_node("write_test")
        inst = composer.build(
            node, state,
            triggered_by_decision_id="dec_continue",
            trigger_type="continue",
        )

        assert "Continue with the highest-priority remaining action" in inst.content


class TestCausalityChain:
    def test_full_chain(self, tmp_path):
        """Checkpoint → Decision → Instruction with linked IDs."""
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        loop = SupervisorLoop(store)

        # 1. Checkpoint
        cp = Checkpoint(
            status="step_done", current_node="write_test",
            summary="wrote test", run_id=state.run_id, checkpoint_seq=1,
        )

        # 2. Gate → Decision
        state.last_agent_checkpoint = cp.to_dict()
        state.top_state = TopState.GATING
        decision = loop.gate(spec, state, triggered_by_seq=cp.checkpoint_seq)
        assert decision.triggered_by_seq == 1
        assert decision.decision == "VERIFY_STEP"

        # 3. Compose → Instruction
        loop.apply_decision(spec, state, decision)
        loop.apply_verification(spec, state, {"ok": True, "results": []})

        node = spec.get_node(state.current_node_id)
        composer = InstructionComposer()
        instruction = composer.build(
            node, state,
            triggered_by_decision_id=decision.decision_id,
            trigger_type="node_advance",
        )

        # Verify chain: instruction → decision → checkpoint
        assert instruction.triggered_by_decision_id == decision.decision_id
        assert decision.triggered_by_seq == cp.checkpoint_seq


class TestSessionRun:
    def test_session_wraps_state(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        session = SessionRun(state, store)

        assert session.run_id == state.run_id
        assert session.is_active is True
        assert session.is_completed is False

    def test_session_events_since(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        session = SessionRun(state, store)

        session.append_session_event("test", {"foo": "bar"})
        session.append_session_event("test2", {"baz": 1})

        events = session.events_since(0)
        assert len(events) == 2
        events_after_1 = session.events_since(1)
        assert len(events_after_1) == 1


class TestGateReturnsTyped:
    def test_continue_gate_returns_decision(self):
        gate = ContinueGate(JudgeClient())
        decision = gate.decide({"last_agent_question": "hi", "last_agent_checkpoint": {}})
        assert isinstance(decision, SupervisorDecision)
        assert decision.gate_type == "continue"
