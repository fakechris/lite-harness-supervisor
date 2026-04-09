from supervisor.gates.continue_gate import ContinueGate
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
