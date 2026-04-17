"""Slice 5 — v1 live-path sunset state machine.

Source of truth: ``docs/plans/2026-04-17-fat-skill-thin-harness-rule-
repartitioning.md`` → Section B "Sunset policy for v1 on the live path".

Three phases:

1. ``NORMAL`` — live path accepts v1 silently. Default before the
   Slice-5 sunset trigger (see ``supervisor/eval/robustness.py``
   ``evaluate_sunset_trigger``) reports ``ready_to_sunset``.
2. ``DEPRECATION`` — live path still accepts v1 but attaches a
   deprecation warning + upgrade hint. Operators leave the system in
   this phase long enough for the last v1 producers to migrate.
3. ``ENFORCEMENT`` — live path rejects v1. ``checkpoint_schema_version``
   absent or ``< 2`` is a compatibility error. Replay / export paths
   are unaffected (they carry permanent v1 read support — see the
   ``replay_mode=True`` branch of ``assess_ingress``).

The module is pure: it answers "does this payload pass the current
sunset phase?" and "what phase should we move to based on the signal
set?" Wiring the decision into the actual ingress adapters and the
operator-facing config is the caller's job; this keeps the policy
layer testable without a full runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from supervisor.eval.robustness import SunsetTriggerStatus
from supervisor.protocol.normalizer import (
    STRUCTURED_SCHEMA_VERSION,
    parse_schema_version,
)


class SunsetPhase(str, Enum):
    """Discrete phases of the v1 live-path sunset lifecycle."""

    NORMAL = "normal"
    DEPRECATION = "deprecation"
    ENFORCEMENT = "enforcement"


@dataclass(frozen=True)
class IngressAssessment:
    """Outcome of applying the sunset policy to a single ingress payload.

    ``accepted=True`` means the adapter should continue processing the
    payload. ``warning`` is populated during ``DEPRECATION`` when a v1
    payload slipped through so the adapter can surface an upgrade hint
    without blocking work.
    """

    accepted: bool
    phase: SunsetPhase
    schema_version: int
    warning: str | None
    rejection_reason: str | None


def assess_ingress(
    raw: dict[str, Any] | None,
    *,
    phase: SunsetPhase,
    replay_mode: bool = False,
) -> IngressAssessment:
    """Return the ingress decision for ``raw`` under ``phase``.

    ``replay_mode=True`` always short-circuits to accept: per Section B
    of the repartitioning doc, replay / export retains v1 read support
    permanently. The enforcement rejection only applies to live ingest.
    """

    version = parse_schema_version(raw or {})

    if replay_mode:
        return IngressAssessment(
            accepted=True,
            phase=phase,
            schema_version=version,
            warning=None,
            rejection_reason=None,
        )

    is_v2 = version == STRUCTURED_SCHEMA_VERSION

    if is_v2:
        return IngressAssessment(
            accepted=True,
            phase=phase,
            schema_version=version,
            warning=None,
            rejection_reason=None,
        )

    # Legacy payload on the live path.
    if phase is SunsetPhase.NORMAL:
        return IngressAssessment(
            accepted=True,
            phase=phase,
            schema_version=version,
            warning=None,
            rejection_reason=None,
        )

    if phase is SunsetPhase.DEPRECATION:
        return IngressAssessment(
            accepted=True,
            phase=phase,
            schema_version=version,
            warning=(
                "v1 checkpoint accepted under deprecation: emit "
                "checkpoint_schema_version=2 before sunset enforcement "
                "to avoid future rejection"
            ),
            rejection_reason=None,
        )

    # ENFORCEMENT — reject.
    return IngressAssessment(
        accepted=False,
        phase=phase,
        schema_version=version,
        warning=None,
        rejection_reason=(
            "live-path sunset: checkpoint_schema_version is required to "
            "be >= 2 under enforcement phase"
        ),
    )


def recommended_next_phase(
    current: SunsetPhase, trigger: SunsetTriggerStatus
) -> SunsetPhase:
    """Suggest the next phase given the current one and the sunset signal.

    Rules (intentionally one-directional — we never auto-rollback):

    - ``NORMAL`` → ``DEPRECATION`` once the trigger is ready.
    - ``DEPRECATION`` → ``ENFORCEMENT`` also on ``ready_to_sunset`` —
      the operator advances by re-checking the trigger after the
      deprecation soak period. The helper does NOT enforce the soak
      length; that is an out-of-band operator decision.
    - ``ENFORCEMENT`` stays at ``ENFORCEMENT``.
    - Any phase with an un-ready trigger stays put (no downgrade).

    Auto-rollback is deliberately omitted: if the trigger regresses
    after enforcement flips, the operator decides whether that warrants
    a compatibility rollback or a point-release hotfix — not this
    stateless helper.
    """

    if current is SunsetPhase.ENFORCEMENT:
        return SunsetPhase.ENFORCEMENT

    if not trigger.ready_to_sunset:
        return current

    if current is SunsetPhase.NORMAL:
        return SunsetPhase.DEPRECATION

    return SunsetPhase.ENFORCEMENT
