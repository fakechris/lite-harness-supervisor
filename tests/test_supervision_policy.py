"""Tests for SupervisionPolicy and SupervisionPolicyEngine."""
from supervisor.domain.models import (
    WorkerProfile, AcceptanceContract, SupervisionPolicy, RoutingDecision,
)
from supervisor.gates.supervision_policy import SupervisionPolicyEngine
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore


class TestSupervisionPolicyEngine:
    def setup_method(self):
        self.engine = SupervisionPolicyEngine()
        self.standard_contract = AcceptanceContract(goal="test", risk_class="standard")
        self.high_risk_contract = AcceptanceContract(goal="test", risk_class="high")
        self.critical_contract = AcceptanceContract(goal="test", risk_class="critical")

    def _make_state(self, tmp_path, current_attempt=0):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        state.current_attempt = current_attempt
        return state

    def test_strong_worker_standard_risk(self, tmp_path):
        worker = WorkerProfile(trust_level="high", model_name="claude-opus-4-6")
        state = self._make_state(tmp_path)
        policy = self.engine.determine(worker, self.standard_contract, state)
        assert policy.mode == "strict_verifier"

    def test_standard_worker_standard_risk(self, tmp_path):
        worker = WorkerProfile(trust_level="standard")
        state = self._make_state(tmp_path)
        policy = self.engine.determine(worker, self.standard_contract, state)
        assert policy.mode == "strict_verifier"

    def test_low_trust_worker(self, tmp_path):
        worker = WorkerProfile(trust_level="low")
        state = self._make_state(tmp_path)
        policy = self.engine.determine(worker, self.standard_contract, state)
        assert policy.mode == "collaborative_reviewer"

    def test_high_risk_elevates(self, tmp_path):
        worker = WorkerProfile(trust_level="high")
        state = self._make_state(tmp_path)
        policy = self.engine.determine(worker, self.high_risk_contract, state)
        assert policy.mode == "collaborative_reviewer"

    def test_critical_risk_low_trust(self, tmp_path):
        worker = WorkerProfile(trust_level="low")
        state = self._make_state(tmp_path)
        policy = self.engine.determine(worker, self.critical_contract, state)
        assert policy.mode == "directive_lead"

    def test_consecutive_failures_escalate(self, tmp_path):
        worker = WorkerProfile(trust_level="standard")
        state = self._make_state(tmp_path, current_attempt=3)
        policy = self.engine.determine(worker, self.standard_contract, state)
        assert policy.mode == "directive_lead"

    def test_two_failures_high_risk(self, tmp_path):
        worker = WorkerProfile(trust_level="standard")
        state = self._make_state(tmp_path, current_attempt=2)
        policy = self.engine.determine(worker, self.high_risk_contract, state)
        assert policy.mode == "directive_lead"


class TestWorkerProfile:
    def test_default_profile(self):
        wp = WorkerProfile()
        assert wp.trust_level == "standard"
        assert wp.role == "executor"

    def test_to_dict(self):
        wp = WorkerProfile(provider="anthropic", model_name="claude-opus-4-6", trust_level="high")
        d = wp.to_dict()
        assert d["provider"] == "anthropic"
        assert d["trust_level"] == "high"


class TestRoutingDecision:
    def test_auto_id_and_timestamp(self):
        rd = RoutingDecision(target_type="human", reason="test")
        assert rd.routing_id.startswith("rt_")
        assert rd.timestamp != ""

    def test_to_dict(self):
        rd = RoutingDecision(
            target_type="reviewer",
            scope="bounded_review",
            reason="complex branch",
            triggered_by_decision_id="dec_xyz",
        )
        d = rd.to_dict()
        assert d["target_type"] == "reviewer"
        assert d["triggered_by_decision_id"] == "dec_xyz"


class TestPolicyInComposer:
    def test_strict_verifier_minimal(self, tmp_path):
        from supervisor.instructions.composer import InstructionComposer
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        node = spec.get_node("write_test")
        composer = InstructionComposer()

        strict = SupervisionPolicy(mode="strict_verifier")
        inst = composer.build(node, state, policy=strict)
        assert "[DIRECTIVE]" not in inst.content
        assert "approach and any risks" not in inst.content

    def test_collaborative_asks_for_approach(self, tmp_path):
        from supervisor.instructions.composer import InstructionComposer
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        node = spec.get_node("write_test")
        composer = InstructionComposer()

        collab = SupervisionPolicy(mode="collaborative_reviewer")
        inst = composer.build(node, state, policy=collab)
        assert "approach" in inst.content.lower()

    def test_directive_gives_strict_instructions(self, tmp_path):
        from supervisor.instructions.composer import InstructionComposer
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        node = spec.get_node("write_test")
        composer = InstructionComposer()

        directive = SupervisionPolicy(mode="directive_lead")
        inst = composer.build(node, state, policy=directive)
        assert "[DIRECTIVE]" in inst.content
        assert "Do only this one action" in inst.content
