"""Slice 4A — invariant oracles for the fat-skill / thin-harness switch.

These invariants are the merge gate for Slice 3 (harness consumption
switch). They are stated as pure functions over a
``(NormalizedCheckpoint, SupervisorDecision)`` pair so they can run in
unit tests today (against the current harness) and continue to run
after Slice 3 swaps the harness onto normalized reads.

Source of truth: ``docs/plans/2026-04-17-fat-skill-thin-harness-rule-
repartitioning.md`` → Success Criteria + Section E (contradiction
routing) + Decision 1 (Slice 4A invariant list).

Not all invariants can fire on every golden — e.g. the ``blocking_inputs``
invariant is vacuous for a checkpoint that left the field empty. The
caller passes the invariants the scenario is meant to exercise; the
runner asserts they all return `None` (i.e. no violation).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from supervisor.domain.enums import DecisionType
from supervisor.protocol.normalizer import NormalizedCheckpoint
from supervisor.protocol.reason_code import (
    REASON_CODE_FAMILIES,
    reason_code_family,
)


Decision = Any  # SupervisorDecision — kept loose to avoid a circular import


@dataclass(frozen=True)
class InvariantViolation:
    name: str
    detail: str


def attached_never_advances_without_structured_execution(
    cp: NormalizedCheckpoint, decision: Decision
) -> InvariantViolation | None:
    """ATTACHED must not produce a CONTINUE / VERIFY_STEP decision unless
    the checkpoint truthfully reports ``progress_class=execution`` AND
    ``evidence_scope=current_node``.

    Per Section A of the repartitioning doc: the fat skill is where the
    structured semantic comes from; the thin harness enforces the
    conservative "no progress unless the worker said so" direction.
    """
    if decision is None:
        return None
    kind = _kind(decision)
    if kind not in {DecisionType.CONTINUE.value, DecisionType.VERIFY_STEP.value}:
        return None
    if cp.schema_version != 2:
        return None  # v1 checkpoints predate the invariant
    if cp.progress_class == "execution" and cp.evidence_scope == "current_node":
        return None
    return InvariantViolation(
        name="attached_structured_execution_required",
        detail=(
            f"decision={kind} on v2 checkpoint with "
            f"progress_class={cp.progress_class!r} "
            f"evidence_scope={cp.evidence_scope!r}"
        ),
    )


def requires_authorization_never_continues(
    cp: NormalizedCheckpoint, decision: Decision
) -> InvariantViolation | None:
    """``requires_authorization=True`` must pause / escalate; never CONTINUE.

    Section D (dangerous-action precedence) + Section E row 1 (safety
    contradiction fail-closed).
    """
    if decision is None:
        return None
    if cp.requires_authorization is not True:
        return None
    kind = _kind(decision)
    if kind == DecisionType.CONTINUE.value:
        return InvariantViolation(
            name="requires_authorization_blocks_continue",
            detail=(
                "checkpoint declared requires_authorization=True "
                f"but decision={kind}"
            ),
        )
    return None


def blocking_inputs_never_re_inject(
    cp: NormalizedCheckpoint, decision: Decision
) -> InvariantViolation | None:
    """``blocking_inputs != []`` means the worker is genuinely missing
    external input — RE_INJECT would pretend the problem is reporting
    quality, but the right call is business escalation.
    """
    if decision is None:
        return None
    if not cp.blocking_inputs:
        return None
    kind = _kind(decision)
    if kind == DecisionType.RE_INJECT.value:
        return InvariantViolation(
            name="blocking_inputs_block_reinject",
            detail=(
                f"blocking_inputs={list(cp.blocking_inputs)} "
                f"but decision={kind}"
            ),
        )
    return None


def reason_code_family_never_cross_routes(
    cp: NormalizedCheckpoint, decision: Decision
) -> InvariantViolation | None:
    """Family-scoped routing: ``esc.*`` → ESCALATE_TO_HUMAN / pause;
    ``ver.*`` → VERIFY_STEP outcome; ``rec.*`` → recovery-class pause;
    ``sem.*`` → not CONTINUE (routes per Section E).

    This invariant only fires when the decision carries a ``reason_code``
    (i.e. after Slice 3 wires them end-to-end). Pre-Slice-3 decisions
    without a ``reason_code`` are treated as vacuously compatible.
    """
    if decision is None:
        return None
    code = getattr(decision, "reason_code", None) or (
        decision.get("reason_code") if isinstance(decision, dict) else None
    )
    if not code:
        return None
    try:
        family = reason_code_family(code)
    except Exception:
        return InvariantViolation(
            name="reason_code_family_malformed",
            detail=f"decision carried malformed reason_code={code!r}",
        )
    if family not in REASON_CODE_FAMILIES:
        return InvariantViolation(
            name="reason_code_family_unknown",
            detail=f"reason_code family {family!r} not in {REASON_CODE_FAMILIES}",
        )
    kind = _kind(decision)
    if family == "esc" and kind == DecisionType.CONTINUE.value:
        return InvariantViolation(
            name="esc_family_must_not_continue",
            detail=f"reason_code={code!r} (esc.*) paired with decision={kind}",
        )
    if family == "sem" and kind == DecisionType.CONTINUE.value:
        return InvariantViolation(
            name="sem_family_must_not_continue",
            detail=f"reason_code={code!r} (sem.*) paired with decision={kind}",
        )
    return None


ALL_INVARIANTS: tuple[Callable[[NormalizedCheckpoint, Decision], InvariantViolation | None], ...] = (
    attached_never_advances_without_structured_execution,
    requires_authorization_never_continues,
    blocking_inputs_never_re_inject,
    reason_code_family_never_cross_routes,
)


def _kind(decision: Decision) -> str:
    if decision is None:
        return ""
    if isinstance(decision, dict):
        value = decision.get("decision", "")
    else:
        value = getattr(decision, "decision", "")
    return str(value).upper()
