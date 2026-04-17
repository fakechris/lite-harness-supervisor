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
        {"command": "pytest tests/test_foo.py", "output": "2 passed"},
    ]) is False


def test_is_admin_only_evidence_accepts_strings():
    assert is_admin_only_evidence(["opened pane", "read plan"]) is True
    assert is_admin_only_evidence(["pytest tests/test_foo.py -> 3 passed"]) is False


def test_is_admin_only_evidence_reviewer_edge_case():
    """Reviewer P1-1: `modified: .supervisor/specs/foo.yaml` + `ran: git status`
    is admin activity (editing the spec, checking git), NOT work on the node.
    Previous loose pattern matching flagged this as real execution — this
    test locks the tighter behavior so we cannot regress.
    """
    assert is_admin_only_evidence([
        {"modified": ".supervisor/specs/foo.yaml"},
        {"ran": "git status --short"},
    ]) is True


def test_is_admin_only_evidence_reviewer_three_field_case():
    """Extends the reviewer case with a third `result:` field to cover the
    exact shape the reviewer reproduced.  Must still be admin-only — none of
    `modified`, `ran: git status`, or `result: worktree clean` carry an
    execution signal on their own.
    """
    assert is_admin_only_evidence([
        {"modified": ".supervisor/specs/foo.yaml"},
        {"ran": "git status --short"},
        {"result": "worktree clean"},
    ]) is True


def test_is_admin_only_evidence_diff_counts():
    """A real git-diff output snippet counts as execution."""
    assert is_admin_only_evidence([
        {"diff": "diff --git a/src/foo.py b/src/foo.py"},
    ]) is False


def test_is_admin_only_evidence_verifier_counts():
    """Verifier output is an explicit first-class execution signal."""
    assert is_admin_only_evidence([{"verifier": "ok"}]) is False
    assert is_admin_only_evidence([{"result": "verified step_1"}]) is False


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


def test_attached_admin_only_with_missing_input_text_escalates_not_reinject():
    """Reviewer P2-3: escalation classification must beat the ATTACHED
    admin-only guard.  A first-checkpoint that cites only admin artifacts
    AND carries MISSING_EXTERNAL_INPUT text in `needs` / `question_for_supervisor`
    is a legitimate business pause, not a re-inject candidate — waking the
    human is the correct move.

    Critically, `status` stays at `working` here (not `blocked`): the block
    signal comes from the needs/question text, not the status field, so this
    probes the escalation classifier path, not the status-based shortcut.
    """
    gate = ContinueGate(JudgeClient())
    decision = gate.decide({
        "top_state": TopState.ATTACHED.value,
        "last_agent_question": "",
        "last_agent_checkpoint": {
            "status": "working",
            "summary": "attached and reviewed plan",
            "evidence": [
                {"attach": "tmux://alpha"},
                {"plan": "drafted step order"},
            ],
            "needs": ["need credentials for upstream API"],
            "question_for_supervisor": ["need access token to proceed"],
        },
    })
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.needs_human is True
    # Must NOT have been routed through the RE_INJECT attach-boundary guard.
    assert decision.reason != "attached: first checkpoint has no execution evidence on current_node"
