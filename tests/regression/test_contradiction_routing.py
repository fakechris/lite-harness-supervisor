"""Slice 3 — Section E contradiction routing integration tests.

Separate from the Slice 4A goldens so the merge-gate invariants stay
clean from Slice-3-specific payloads. Each test pairs a crafted v2
contradiction against the expected gate outcome + reason_code family.
"""
from __future__ import annotations

from supervisor.domain.enums import DecisionType, TopState
from supervisor.gates.contradictions import detect_contradiction
from supervisor.loop import SupervisorLoop
from supervisor.plan.loader import load_spec
from supervisor.protocol.normalizer import normalize_checkpoint
from supervisor.protocol.reason_code import (
    ESC_AUTHORIZATION_CONTRADICTION,
    ESC_MISSING_EXTERNAL_INPUT,
    SEM_BLOCKING_INPUTS_CONTRADICTION,
    SEM_EVIDENCE_SCOPE_CONTRADICTION,
    SEM_PROGRESS_CLASS_CONTRADICTION,
)
from supervisor.storage.state_store import StateStore


def _base(**overrides):
    payload = {
        "status": "working",
        "current_node": "step1",
        "summary": "test",
        "run_id": "run_sec_e",
        "checkpoint_seq": 1,
        "surface_id": "tmux:test",
        "checkpoint_schema_version": 2,
    }
    payload.update(overrides)
    return payload


def _drive(tmp_path, payload: dict):
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path))
    state = store.load_or_init(spec)
    state.top_state = TopState.ATTACHED
    payload = {**payload, "current_node": state.current_node_id}
    state.last_agent_checkpoint = payload
    loop = SupervisorLoop(store)
    return loop.gate(spec, state)


# --- Safety contradiction ------------------------------------------------


def test_safety_contradiction_escalates_with_esc_authorization_contradiction(tmp_path):
    # Worker denies authorization need, but pattern classifier sees a
    # destructive action — this is the Section E "safety fail-closed" row.
    payload = _base(
        summary="about to force push to main",
        evidence=[{"plan": "destructive step"}],
        question_for_supervisor=["force push coming — no authorization needed"],
        progress_class="execution",
        evidence_scope="current_node",
        escalation_class="none",
        requires_authorization=False,
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.reason_code == ESC_AUTHORIZATION_CONTRADICTION


# --- Business contradiction ---------------------------------------------


def test_business_contradiction_on_empty_blocking_inputs_escalates(tmp_path):
    payload = _base(
        status="working",
        summary="need credentials to continue",
        evidence=[{"attach": "opened pane"}],
        needs=["need access credentials to continue"],
        question_for_supervisor=["need credentials input"],
        progress_class="admin",
        evidence_scope="prior_phase",
        escalation_class="business",
        requires_authorization=False,
        blocking_inputs=[],
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.reason_code == SEM_BLOCKING_INPUTS_CONTRADICTION


# --- Execution-semantic contradiction -----------------------------------


def test_progress_class_execution_with_admin_only_evidence_reinjects(tmp_path):
    payload = _base(
        summary="claims executed but only admin evidence",
        evidence=[
            {"attach": "opened pane"},
            {"clarify": "confirmed spec"},
            {"plan": "drafted step order"},
        ],
        progress_class="execution",
        evidence_scope="current_node",
        escalation_class="none",
        requires_authorization=False,
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.RE_INJECT.value
    assert decision.reason_code == SEM_PROGRESS_CLASS_CONTRADICTION


def test_evidence_scope_current_node_with_admin_only_evidence_reinjects(tmp_path):
    # progress_class left admin so the progress-class rule doesn't fire —
    # the evidence_scope rule should catch it independently.
    payload = _base(
        summary="claims current-node evidence but only plan artifacts",
        evidence=[
            {"attach": "opened pane"},
            {"plan": "drafted plan"},
        ],
        progress_class="admin",
        evidence_scope="current_node",
        escalation_class="none",
        requires_authorization=False,
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.RE_INJECT.value
    assert decision.reason_code == SEM_EVIDENCE_SCOPE_CONTRADICTION


# --- Runtime-owned field conflict ---------------------------------------


def test_runtime_owned_escalation_class_recovery_demoted(tmp_path, capsys):
    # Worker asserting escalation_class=recovery is runtime-owned state
    # (Section B line 549 reserves "recovery" as a supervisor/runtime-
    # owned class). The contradiction detector demotes the worker field
    # to a log-only signal and does NOT set the decision's reason_code to
    # sem.runtime_owned_field_conflict; the decision falls through to a
    # non-contradicted path (CONTINUE here, since the rest of the
    # payload is healthy).
    payload = _base(
        summary="test work ran",
        evidence=[
            {"ran": "pytest -q"},
            {"result": "5 passed"},
        ],
        progress_class="execution",
        evidence_scope="current_node",
        escalation_class="recovery",
        requires_authorization=False,
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.CONTINUE.value


def test_worker_declared_review_escalates_with_esc_review_required(tmp_path):
    # Worker declares escalation_class=review — per the protocol prompt,
    # this is "completion proof is ready and a human must sign off".
    # Must escalate to human, carrying esc.review_required.
    payload = _base(
        summary="all tests pass, ready for review",
        evidence=[
            {"ran": "pytest -q"},
            {"result": "all green"},
        ],
        progress_class="execution",
        evidence_scope="current_node",
        escalation_class="review",
        requires_authorization=False,
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.reason_code == "esc.review_required"


# --- Pure-unit tests on the detector ------------------------------------


def test_detect_contradiction_on_v1_payload_returns_none():
    payload = {
        "status": "working",
        "current_node": "step1",
        "summary": "v1 payload",
        "run_id": "run_v1",
        "checkpoint_seq": 1,
        "surface_id": "tmux:test",
    }
    normalized = normalize_checkpoint(payload)
    assert normalized is not None
    assert normalized.schema_version == 1
    assert detect_contradiction(normalized) is None


def test_detect_contradiction_clean_v2_returns_none():
    payload = _base(
        summary="actually ran tests, 5 passed",
        evidence=[{"ran": "pytest -q"}, {"result": "5 passed"}],
        progress_class="execution",
        evidence_scope="current_node",
        escalation_class="none",
        requires_authorization=False,
        blocking_inputs=[],
    )
    normalized = normalize_checkpoint(payload)
    assert normalized is not None
    assert detect_contradiction(normalized) is None


# --- Structured fast-path ------------------------------------------------


def test_requires_authorization_true_short_circuits_to_escalate(tmp_path):
    payload = _base(
        summary="about to do something safety-sensitive",
        evidence=[{"plan": "drafted dangerous step"}],
        progress_class="admin",
        evidence_scope="current_node",
        escalation_class="safety",
        requires_authorization=True,
        blocking_inputs=[],
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.reason_code == "esc.authorization_required"


def test_business_blocking_inputs_short_circuits_to_escalate(tmp_path):
    payload = _base(
        status="working",
        summary="stuck on external input",
        evidence=[{"attach": "opened pane"}],
        progress_class="admin",
        evidence_scope="prior_phase",
        escalation_class="business",
        requires_authorization=False,
        blocking_inputs=["GITHUB_TOKEN", "staging DB URL"],
    )
    decision = _drive(tmp_path, payload)
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.reason_code == ESC_MISSING_EXTERNAL_INPUT
