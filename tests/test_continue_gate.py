from supervisor.domain.enums import DecisionType, TopState
from supervisor.gates.continue_gate import ContinueGate
from supervisor.gates.rules import is_admin_only_evidence
from supervisor.llm.judge_client import JudgeClient

def test_soft_confirmation_continues():
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({"last_agent_question": "要不要我继续往下做？", "last_agent_checkpoint": {}})
    assert decision.decision == "CONTINUE"

def test_missing_input_escalates():
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({"last_agent_question": "需要你提供 token 和权限", "last_agent_checkpoint": {}})
    assert decision.decision == "ESCALATE_TO_HUMAN"

def test_stub_fallback_prefers_continue():
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({"last_agent_question": "当前在修复一个测试失败", "last_agent_checkpoint": {}})
    assert decision.decision == "CONTINUE"


def test_is_admin_only_evidence_empty():
    assert is_admin_only_evidence([]) is True
    assert is_admin_only_evidence(None) is True


def test_is_admin_only_evidence_admin_artifacts():
    # Attach, clarify, plan, spec — no execution signal
    assert is_admin_only_evidence([
        {"attach": "opened pane tmux://alpha"},
        {"clarify": "confirmed spec scope"},
        {"plan": "drafted step order"},
    ]) is True


def test_is_admin_only_evidence_real_execution():
    # Concrete execution signals should be detected
    assert is_admin_only_evidence([
        {"command": "ran pytest -q"},
        {"output": "5 passed"},
    ]) is False


def test_is_admin_only_evidence_mixed_counts_execution():
    # Any execution-signaled item flips the whole checkpoint to real work
    assert is_admin_only_evidence([
        {"attach": "opened pane"},
        {"file": "modified src/foo.py"},
    ]) is False


def test_is_admin_only_evidence_accepts_strings():
    assert is_admin_only_evidence(["opened pane", "read plan"]) is True
    assert is_admin_only_evidence(["ran migration script"]) is False


def test_attached_admin_only_evidence_reinjects():
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({
        "top_state": TopState.ATTACHED.value,
        "last_agent_question": "",
        "last_agent_checkpoint": {
            "status": "working",
            "summary": "attached to pane and reviewed plan",
            "evidence": [
                {"attach": "tmux://alpha"},
                {"plan": "step order confirmed"},
            ],
        },
    })
    assert decision.decision == DecisionType.RE_INJECT.value
    assert decision.needs_human is False


def test_attached_real_execution_continues():
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({
        "top_state": TopState.ATTACHED.value,
        "last_agent_question": "",
        "last_agent_checkpoint": {
            "status": "working",
            "summary": "executed step_1",
            "evidence": [
                {"command": "ran make build"},
                {"output": "build succeeded"},
            ],
        },
    })
    assert decision.decision == DecisionType.CONTINUE.value


def test_running_admin_only_evidence_does_not_reinject():
    # Default-CONTINUE bias is preserved for RUNNING state; RE_INJECT is an
    # attach-boundary mechanism only.
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({
        "top_state": TopState.RUNNING.value,
        "last_agent_question": "",
        "last_agent_checkpoint": {
            "status": "working",
            "summary": "reviewing the plan",
            "evidence": [{"plan": "next step identified"}],
        },
    })
    assert decision.decision == DecisionType.CONTINUE.value


def test_attached_blocked_still_escalates():
    # Escalation paths remain active on the attach boundary — missing external
    # input on first checkpoint is still a legitimate human pause.
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({
        "top_state": TopState.ATTACHED.value,
        "last_agent_question": "need access credentials to proceed",
        "last_agent_checkpoint": {
            "status": "blocked",
            "summary": "",
            "evidence": [],
        },
    })
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.needs_human is True
