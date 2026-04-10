"""Tests for AcceptanceContract and FinishGate integration."""
from supervisor.domain.models import AcceptanceContract, FinishPolicy
from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.domain.enums import TopState


class TestAcceptanceContract:
    def test_from_finish_policy(self):
        fp = FinishPolicy(require_all_steps_done=True, require_verification_pass=False)
        contract = AcceptanceContract.from_finish_policy(fp, goal="test goal")
        assert contract.goal == "test goal"
        assert contract.require_all_steps_done is True
        assert contract.require_verification_pass is False
        assert contract.risk_class == "standard"
        assert contract.must_review_by == ""

    def test_direct_construction(self):
        contract = AcceptanceContract(
            goal="deploy feature X",
            required_evidence=["tests pass", "PR approved"],
            forbidden_states=["test_failing"],
            risk_class="high",
            must_review_by="human",
        )
        assert contract.risk_class == "high"
        assert len(contract.forbidden_states) == 1
        assert contract.must_review_by == "human"

    def test_to_dict(self):
        contract = AcceptanceContract(goal="test")
        d = contract.to_dict()
        assert d["goal"] == "test"
        assert d["risk_class"] == "standard"


class TestSpecLoaderAcceptance:
    def test_spec_without_acceptance_gets_default(self):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        assert spec.acceptance is not None
        assert spec.acceptance.goal == spec.goal
        assert spec.acceptance.require_all_steps_done is True

    def test_spec_with_acceptance(self, tmp_path):
        spec_file = tmp_path / "test.yaml"
        spec_file.write_text(
            "kind: linear_plan\n"
            "id: test\n"
            "goal: test goal\n"
            "acceptance:\n"
            "  goal: acceptance goal\n"
            "  risk_class: high\n"
            "  must_review_by: human\n"
            "  required_evidence:\n"
            "    - tests pass\n"
            "  forbidden_states:\n"
            "    - test_failing\n"
            "steps:\n"
            "  - id: s1\n"
            "    type: task\n"
            "    objective: do\n"
            "    verify:\n"
            "      - type: command\n"
            "        run: echo ok\n"
            "        expect: pass\n"
        )
        spec = load_spec(str(spec_file))
        assert spec.acceptance.goal == "acceptance goal"
        assert spec.acceptance.risk_class == "high"
        assert spec.acceptance.must_review_by == "human"
        assert "tests pass" in spec.acceptance.required_evidence
        assert "test_failing" in spec.acceptance.forbidden_states


class TestFinishGateWithContract:
    def test_must_review_blocks_completion(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        # Override acceptance with must_review_by
        spec.acceptance = AcceptanceContract(
            goal="test", must_review_by="human",
            require_all_steps_done=False, require_verification_pass=False,
        )

        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        loop = SupervisorLoop(store)

        state.done_node_ids = ["write_test", "implement_feature", "final_verify"]
        state.verification = {"ok": True}

        result = loop.finish_gate.evaluate(spec, state)
        assert result["ok"] is False
        assert "requires review" in result["reason"]

    def test_forbidden_state_blocks_completion(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        spec.acceptance = AcceptanceContract(
            goal="test", forbidden_states=["test_failing"],
            require_all_steps_done=False, require_verification_pass=False,
        )

        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)
        state.verification = {"ok": False}  # test failing

        loop = SupervisorLoop(store)
        result = loop.finish_gate.evaluate(spec, state)
        assert result["ok"] is False
        assert "test_failing" in result["reason"]

    def test_risk_class_in_result(self, tmp_path):
        spec = load_spec("specs/examples/linear_plan.example.yaml")
        spec.acceptance = AcceptanceContract(
            goal="test", risk_class="critical",
            require_all_steps_done=False, require_verification_pass=False,
        )

        store = StateStore(str(tmp_path / "runtime"))
        state = store.load_or_init(spec)

        loop = SupervisorLoop(store)
        result = loop.finish_gate.evaluate(spec, state)
        assert result["risk_class"] == "critical"
