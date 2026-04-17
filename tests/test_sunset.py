"""Slice 5 — tests for the v1 live-path sunset state machine."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from supervisor.eval.robustness import CheckpointObservation, evaluate_sunset_trigger
from supervisor.eval.v2_synthetic import FROZEN_INGRESS_SURFACES
from supervisor.protocol.normalizer import normalize_checkpoint
from supervisor.protocol.sunset import (
    IngressAssessment,
    SunsetPhase,
    assess_ingress,
    recommended_next_phase,
)


def _obs(surface: str, *, schema: int, at: datetime, seq: int = 1) -> CheckpointObservation:
    payload = {
        "status": "working",
        "current_node": "step1",
        "summary": "test",
        "run_id": f"run_{surface}_{seq}",
        "checkpoint_seq": seq,
        "surface_id": surface,
    }
    if schema == 2:
        payload.update(
            {
                "checkpoint_schema_version": 2,
                "evidence": [{"ran": "pytest"}, {"result": "passed"}],
                "progress_class": "execution",
                "evidence_scope": "current_node",
                "escalation_class": "none",
                "requires_authorization": False,
                "blocking_inputs": [],
            }
        )
    else:
        payload["evidence"] = [{"attach": "opened pane"}]
    normalized = normalize_checkpoint(payload)
    assert normalized is not None
    return CheckpointObservation(checkpoint=normalized, observed_at=at)


def _v1_payload():
    return {
        "status": "working",
        "current_node": "step1",
        "summary": "legacy payload",
        "run_id": "run_legacy",
        "checkpoint_seq": 1,
        "surface_id": "tmux",
    }


def _v2_payload():
    return {
        "status": "working",
        "current_node": "step1",
        "summary": "structured",
        "run_id": "run_structured",
        "checkpoint_seq": 1,
        "surface_id": "tmux",
        "checkpoint_schema_version": 2,
    }


def test_normal_phase_accepts_v1_silently():
    assessment = assess_ingress(_v1_payload(), phase=SunsetPhase.NORMAL)
    assert assessment.accepted
    assert assessment.warning is None
    assert assessment.rejection_reason is None


def test_deprecation_phase_accepts_v1_with_warning():
    assessment = assess_ingress(_v1_payload(), phase=SunsetPhase.DEPRECATION)
    assert assessment.accepted
    assert assessment.warning is not None
    assert "checkpoint_schema_version=2" in assessment.warning


def test_enforcement_phase_rejects_v1():
    assessment = assess_ingress(_v1_payload(), phase=SunsetPhase.ENFORCEMENT)
    assert not assessment.accepted
    assert assessment.rejection_reason is not None
    assert ">= 2" in assessment.rejection_reason


def test_enforcement_accepts_v2():
    assessment = assess_ingress(_v2_payload(), phase=SunsetPhase.ENFORCEMENT)
    assert assessment.accepted
    assert assessment.warning is None


def test_replay_mode_bypasses_enforcement():
    # Permanent v1 read support on the replay / export path, even when
    # the live path is in enforcement.
    assessment = assess_ingress(
        _v1_payload(), phase=SunsetPhase.ENFORCEMENT, replay_mode=True
    )
    assert assessment.accepted
    assert assessment.rejection_reason is None


def test_missing_or_none_payload_treated_as_legacy():
    assessment = assess_ingress(None, phase=SunsetPhase.ENFORCEMENT)
    assert not assessment.accepted
    assert assessment.schema_version == 1


def test_recommended_next_phase_normal_to_deprecation_when_trigger_ready():
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = [
        _obs(surface, schema=2, at=base - timedelta(days=offset), seq=offset)
        for offset in range(14)
        for surface in FROZEN_INGRESS_SURFACES
    ]
    trigger = evaluate_sunset_trigger(obs, reference_day=ref)
    assert trigger.ready_to_sunset

    assert (
        recommended_next_phase(SunsetPhase.NORMAL, trigger) is SunsetPhase.DEPRECATION
    )
    assert (
        recommended_next_phase(SunsetPhase.DEPRECATION, trigger)
        is SunsetPhase.ENFORCEMENT
    )
    assert (
        recommended_next_phase(SunsetPhase.ENFORCEMENT, trigger)
        is SunsetPhase.ENFORCEMENT
    )


def test_recommended_next_phase_holds_when_trigger_not_ready():
    ref = date(2026, 4, 17)
    base = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
    obs = [
        _obs(surface, schema=1, at=base - timedelta(days=offset), seq=offset)
        for offset in range(14)
        for surface in FROZEN_INGRESS_SURFACES
    ]
    trigger = evaluate_sunset_trigger(obs, reference_day=ref)
    assert not trigger.ready_to_sunset

    assert recommended_next_phase(SunsetPhase.NORMAL, trigger) is SunsetPhase.NORMAL
    assert (
        recommended_next_phase(SunsetPhase.DEPRECATION, trigger)
        is SunsetPhase.DEPRECATION
    )
    # Enforcement never rolls back, even if trigger regresses.
    assert (
        recommended_next_phase(SunsetPhase.ENFORCEMENT, trigger)
        is SunsetPhase.ENFORCEMENT
    )


def test_assessment_carries_schema_version_for_logging():
    v1 = assess_ingress(_v1_payload(), phase=SunsetPhase.DEPRECATION)
    v2 = assess_ingress(_v2_payload(), phase=SunsetPhase.DEPRECATION)
    assert v1.schema_version == 1
    assert v2.schema_version == 2


def test_assessment_is_a_frozen_dataclass():
    assessment = assess_ingress(_v2_payload(), phase=SunsetPhase.NORMAL)
    assert isinstance(assessment, IngressAssessment)
    # frozen dataclass → assignment must raise
    try:
        assessment.accepted = False  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("IngressAssessment should be immutable")
