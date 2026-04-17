"""Section E — contradiction routing between v2 worker semantics and
deterministic harness checks.

Source of truth: ``docs/plans/2026-04-17-fat-skill-thin-harness-rule-
repartitioning.md`` → "E. Contradiction routing is classified by
dimension".

One-line principle:
    Safety contradictions fail closed, business contradictions escalate,
    execution-semantic contradictions re-inject, and runtime-owned fields
    never yield to worker self-report.

This module is the *thin-harness* side of the repartitioning: it knows
only that the normalizer has already validated / scrubbed the worker's
v2 fields, and it compares those typed values against the mechanical
evidence the harness itself owns (admin-only-evidence heuristic,
dangerous-action pattern hits). Pattern libraries still live in
``supervisor/gates/rules.py``; we only reference their outputs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from supervisor.gates.rules import classify_checkpoint, classify_text, is_admin_only_evidence
from supervisor.protocol.normalizer import NormalizedCheckpoint
from supervisor.protocol.reason_code import (
    SEM_BLOCKING_INPUTS_CONTRADICTION,
    SEM_ESCALATION_CLASS_CONTRADICTION,
    SEM_EVIDENCE_SCOPE_CONTRADICTION,
    SEM_PROGRESS_CLASS_CONTRADICTION,
    SEM_RUNTIME_OWNED_FIELD_CONFLICT,
    ESC_AUTHORIZATION_CONTRADICTION,
)


ContradictionRoute = Literal[
    "safety_contradiction",
    "business_contradiction",
    "execution_semantic_contradiction",
    "runtime_owned_conflict",
]


@dataclass(frozen=True)
class ContradictionOutcome:
    route: ContradictionRoute
    reason_code: str
    detail: str


# Fields the runtime owns — a worker asserting one of these values is
# treated as advisory at best. Per Section B of the repartitioning doc
# (line 549), ``recovery`` is the supervisor / runtime-owned escalation
# class. ``review`` is explicitly a legitimate worker-declared class
# (``checkpoint_protocol.txt:63`` — "completion proof is ready and a
# human must sign off") and must NOT appear here, otherwise the loop
# would silently ignore a valid request for human review.
_RUNTIME_OWNED_ESCALATION_CLASSES = frozenset({"recovery"})


def detect_contradiction(
    normalized: NormalizedCheckpoint,
    *,
    question: str = "",
) -> ContradictionOutcome | None:
    """Return the highest-priority contradiction outcome for a v2 payload.

    Priority order matches Section E: safety > business > execution
    semantic > runtime-owned. Returns ``None`` when the worker's v2
    semantics are internally consistent with the mechanical checks.

    For v1 checkpoints (schema_version != 2) this always returns None —
    there are no structured fields to contradict.
    """
    if normalized.schema_version != 2:
        return None

    cp_dict = normalized.raw or {}
    text = classify_text(question or "")
    cp_class = classify_checkpoint(cp_dict)

    # --- 1. Safety contradiction --------------------------------------
    # Worker says "no authorization needed" but deterministic dangerous-
    # action pattern fires.
    if (
        normalized.requires_authorization is False
        and (text == "DANGEROUS_ACTION" or cp_class == "DANGEROUS_ACTION")
    ):
        return ContradictionOutcome(
            route="safety_contradiction",
            reason_code=ESC_AUTHORIZATION_CONTRADICTION,
            detail=(
                "worker declared requires_authorization=false but a "
                "dangerous-action pattern hit"
            ),
        )

    # --- 2. Business contradiction ------------------------------------
    # Worker declared no blocking inputs but the text / checkpoint shows
    # missing external input. This is a real missing input, not a
    # reporting defect — escalate.
    if (
        not normalized.blocking_inputs
        and (text == "MISSING_EXTERNAL_INPUT" or cp_class == "MISSING_EXTERNAL_INPUT")
    ):
        return ContradictionOutcome(
            route="business_contradiction",
            reason_code=SEM_BLOCKING_INPUTS_CONTRADICTION,
            detail="worker emitted blocking_inputs=[] but missing-input pattern fired",
        )

    # ``escalation_class=none`` contradicted by a MISSING_EXTERNAL_INPUT
    # / BLOCKED pattern also fits here — escalate, but tag with the
    # escalation_class contradiction code so diagnostics can tell them
    # apart.
    if (
        normalized.escalation_class == "none"
        and cp_class in {"MISSING_EXTERNAL_INPUT", "BLOCKED"}
    ):
        return ContradictionOutcome(
            route="business_contradiction",
            reason_code=SEM_ESCALATION_CLASS_CONTRADICTION,
            detail=(
                "worker declared escalation_class=none but checkpoint "
                f"classifier returned {cp_class}"
            ),
        )

    # --- 3. Execution-semantic contradiction --------------------------
    # Worker says they are making execution progress on the current node,
    # but the mechanical admin-only-evidence heuristic disagrees. Re-
    # inject (attach-boundary) — do NOT touch the retry budget.
    #
    # Yield to a legitimate escalation on the same checkpoint: a worker
    # declaring ``escalation_class="business"`` with non-empty
    # ``blocking_inputs``, or ``escalation_class="review"``, is already
    # asking for human attention. A mis-set ``progress_class`` on that
    # same payload is secondary noise, not a reason to silently RE_INJECT
    # — doing so would swallow a valid escalation signal. The loop-level
    # fast-paths in `SupervisorLoop.gate()` route these to ESCALATE with
    # the correct reason_code; we only need to avoid pre-empting them.
    worker_declared_valid_escalation = (
        (
            normalized.escalation_class == "business"
            and bool(normalized.blocking_inputs)
        )
        or normalized.escalation_class == "review"
    )
    if not worker_declared_valid_escalation:
        if normalized.progress_class == "execution" and is_admin_only_evidence(
            cp_dict.get("evidence")
        ):
            return ContradictionOutcome(
                route="execution_semantic_contradiction",
                reason_code=SEM_PROGRESS_CLASS_CONTRADICTION,
                detail=(
                    "worker declared progress_class=execution but evidence "
                    "is admin-only"
                ),
            )

        if normalized.evidence_scope == "current_node" and is_admin_only_evidence(
            cp_dict.get("evidence")
        ):
            return ContradictionOutcome(
                route="execution_semantic_contradiction",
                reason_code=SEM_EVIDENCE_SCOPE_CONTRADICTION,
                detail=(
                    "worker declared evidence_scope=current_node but the "
                    "cited evidence is admin-only"
                ),
            )

    # --- 4. Runtime-owned field conflict ------------------------------
    # Worker asserted a value for a field the runtime owns. Runtime
    # state wins; we surface the conflict via reason_code so operators /
    # eval can see the drift, but the decision itself routes per the
    # non-contradicted path.
    if (
        normalized.escalation_class is not None
        and normalized.escalation_class in _RUNTIME_OWNED_ESCALATION_CLASSES
    ):
        return ContradictionOutcome(
            route="runtime_owned_conflict",
            reason_code=SEM_RUNTIME_OWNED_FIELD_CONFLICT,
            detail=(
                "worker asserted escalation_class="
                f"{normalized.escalation_class!r} which is runtime-owned"
            ),
        )

    return None
