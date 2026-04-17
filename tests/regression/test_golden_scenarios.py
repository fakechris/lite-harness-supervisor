"""Slice 4A — hand-authored goldens that lock fat-skill / thin-harness behavior.

Each scenario is a ``(checkpoint_payload, expected_decision_kinds,
invariants_to_check)`` triple. We:

1. normalize the payload through the canonical entry point
2. drive the payload through the current harness gate layer
3. assert the decision kind is in the expected set
4. assert the configured invariants are satisfied

These tests are the merge gate for Slice 3 (harness consumption switch):
they must stay green before AND after Slice 3 flips the gate layer onto
`NormalizedCheckpoint`. A Slice 3 change that silently re-routes a
golden is exactly the regression this harness is meant to catch.

Scenarios cover:
- Phase 17 attach-admin-only failure, both v1 and v2 shape
- missing-credential business escalation, both v1 and v2 shape
- dangerous-action safety escalation
- mixed v1-then-v2 on the same run (ingest-surface boundary)
"""
from __future__ import annotations

import pytest

from supervisor.domain.enums import DecisionType, TopState
from supervisor.loop import SupervisorLoop
from supervisor.plan.loader import load_spec
from supervisor.protocol.normalizer import normalize_checkpoint
from supervisor.storage.state_store import StateStore
from .invariants import ALL_INVARIANTS, InvariantViolation


# --- Scenario fixtures ---------------------------------------------------


PHASE_17_V1 = {
    "status": "working",
    "current_node": "step1",
    "summary": "attached pane and drafted plan",
    "run_id": "run_golden_p17_v1",
    "checkpoint_seq": 1,
    "surface_id": "tmux:golden",
    "evidence": [
        {"attach": "opened pane tmux://alpha"},
        {"clarify": "confirmed spec scope"},
        {"plan": "drafted step order"},
    ],
    "candidate_next_actions": ["continue"],
    "needs": ["none"],
    "question_for_supervisor": ["none"],
}


PHASE_17_V2 = {
    **PHASE_17_V1,
    "checkpoint_schema_version": 2,
    "progress_class": "admin",
    "evidence_scope": "prior_phase",
    "escalation_class": "none",
    "requires_authorization": False,
    "blocking_inputs": [],
    "reason_code": None,
}


MISSING_CREDENTIAL_V1 = {
    "status": "blocked",
    "current_node": "step1",
    "summary": "need GITHUB_TOKEN to continue",
    "run_id": "run_golden_miss_v1",
    "checkpoint_seq": 2,
    "surface_id": "tmux:golden",
    "evidence": [
        {"attach": "opened pane"},
    ],
    "candidate_next_actions": ["wait for token"],
    "needs": ["GITHUB_TOKEN"],
    "question_for_supervisor": ["please provide GITHUB_TOKEN"],
}


MISSING_CREDENTIAL_V2 = {
    **MISSING_CREDENTIAL_V1,
    "run_id": "run_golden_miss_v2",
    "checkpoint_schema_version": 2,
    "progress_class": "admin",
    "evidence_scope": "prior_phase",
    "escalation_class": "business",
    "requires_authorization": False,
    "blocking_inputs": ["GITHUB_TOKEN"],
    "reason_code": "esc.missing_external_input",
}


DANGEROUS_ACTION_V1 = {
    "status": "working",
    "current_node": "step1",
    "summary": "about to force push to main",
    "run_id": "run_golden_dangerous_v1",
    "checkpoint_seq": 3,
    "surface_id": "tmux:golden",
    "evidence": [
        {"plan": "drafted destructive step"},
    ],
    "candidate_next_actions": ["force push"],
    "needs": ["confirm destructive action"],
    "question_for_supervisor": [
        "about to force push main — permanent and irreversible, need authorization",
    ],
}


DANGEROUS_ACTION_V2 = {
    **DANGEROUS_ACTION_V1,
    "run_id": "run_golden_dangerous_v2",
    "checkpoint_schema_version": 2,
    "progress_class": "admin",
    "evidence_scope": "current_node",
    "escalation_class": "safety",
    "requires_authorization": True,
    "blocking_inputs": [],
    "reason_code": "esc.dangerous_irreversible",
}


# --- Helpers -------------------------------------------------------------


def _drive_gate(tmp_path, payload: dict):
    """Stand up a minimal state at ATTACHED and invoke the real gate."""
    spec = load_spec("specs/examples/linear_plan.example.yaml")
    store = StateStore(str(tmp_path))
    state = store.load_or_init(spec)
    state.top_state = TopState.ATTACHED
    state.last_agent_checkpoint = {**payload, "current_node": state.current_node_id}
    loop = SupervisorLoop(store)
    decision = loop.gate(spec, state)
    return decision


def _check_invariants(cp_payload: dict, decision) -> list[InvariantViolation]:
    normalized = normalize_checkpoint(cp_payload)
    assert normalized is not None
    return [
        violation
        for check in ALL_INVARIANTS
        for violation in [check(normalized, decision)]
        if violation is not None
    ]


# --- Tests ---------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario,payload,allowed_kinds",
    [
        (
            "phase17_v1",
            PHASE_17_V1,
            {DecisionType.RE_INJECT.value},
        ),
        (
            "phase17_v2",
            PHASE_17_V2,
            {DecisionType.RE_INJECT.value},
        ),
        (
            "missing_credential_v1",
            MISSING_CREDENTIAL_V1,
            # v1: no blocking_inputs, classify_checkpoint sees
            # "blocked" status + "GITHUB_TOKEN" in needs, escalates as
            # MISSING_EXTERNAL_INPUT. In Slice 3 the v2 path will short-
            # circuit via blocking_inputs — the outward decision stays
            # ESCALATE_TO_HUMAN.
            {DecisionType.ESCALATE_TO_HUMAN.value},
        ),
        (
            "missing_credential_v2",
            MISSING_CREDENTIAL_V2,
            {DecisionType.ESCALATE_TO_HUMAN.value},
        ),
        (
            "dangerous_action_v1",
            DANGEROUS_ACTION_V1,
            {DecisionType.ESCALATE_TO_HUMAN.value},
        ),
        (
            "dangerous_action_v2",
            DANGEROUS_ACTION_V2,
            {DecisionType.ESCALATE_TO_HUMAN.value},
        ),
    ],
)
def test_golden_scenario(tmp_path, scenario, payload, allowed_kinds):
    decision = _drive_gate(tmp_path, payload)
    assert decision.decision in allowed_kinds, (
        f"{scenario}: got {decision.decision!r}, expected one of {allowed_kinds}"
    )
    violations = _check_invariants(payload, decision)
    assert not violations, f"{scenario} invariant failures: {violations}"


def test_mixed_v1_then_v2_on_same_run(tmp_path):
    """Sequence: surface emits a v1 admin-only first, then upgrades to
    v2 on the next checkpoint. Both checkpoints independently must hit
    the same gate decision (RE_INJECT) and satisfy the invariants.
    """
    # First checkpoint: v1 admin-only.
    decision_v1 = _drive_gate(tmp_path / "v1", PHASE_17_V1)
    assert decision_v1.decision == DecisionType.RE_INJECT.value
    assert not _check_invariants(PHASE_17_V1, decision_v1)

    # Second checkpoint on a fresh runtime (simulate a resumed run):
    # worker upgraded to v2 but still has nothing but admin evidence.
    decision_v2 = _drive_gate(tmp_path / "v2", PHASE_17_V2)
    assert decision_v2.decision == DecisionType.RE_INJECT.value
    assert not _check_invariants(PHASE_17_V2, decision_v2)


def test_invariant_fires_on_contradicted_decision():
    """Sanity check the invariant machinery: a constructed (cp, decision)
    pair that violates Section E must surface a violation.

    This does not exercise the harness — it's a unit test of the
    invariant oracle itself, so a later Slice 3 bug that silently
    reissues a CONTINUE under a requires_authorization=True checkpoint
    cannot also pass the invariant.
    """
    from supervisor.domain.models import SupervisorDecision

    normalized = normalize_checkpoint(
        {
            **DANGEROUS_ACTION_V2,
            "run_id": "run_invariant_probe",
        }
    )
    assert normalized is not None
    bogus = SupervisorDecision.make(
        decision=DecisionType.CONTINUE.value,
        reason="buggy — pretends to continue despite requires_authorization=true",
        gate_type="continue",
    )
    results = [check(normalized, bogus) for check in ALL_INVARIANTS]
    violations = [v for v in results if v is not None]
    assert any(v.name == "requires_authorization_blocks_continue" for v in violations)
