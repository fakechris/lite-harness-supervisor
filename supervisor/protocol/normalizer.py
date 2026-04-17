"""Canonical checkpoint normalization layer.

Per decision C of the fat-skill / thin-harness repartitioning, raw
checkpoint payloads are normalized **exactly once** at this entry
point. Everything downstream — loop, continue / finish gates,
pause summary, recovery planner, eval / replay — should consume the
resulting `NormalizedCheckpoint` rather than re-interpreting the raw
payload.

Slice 1 establishes the contract:

- `checkpoint_schema_version` parsing (v1 legacy / v2 structured)
- a single `NormalizedCheckpoint` object covering both versions
- pass-through of the semantic v2 fields (`progress_class`,
  `evidence_scope`, `escalation_class`, `requires_authorization`,
  `blocking_inputs`, `reason_code`) — the fields themselves are frozen
  wire names (see Structured Protocol Additions in the plan) and are
  accepted here even before gate layers consume them

Slice 2 extends the v2 branch with richer validation and populates
downstream consumers. Slice 3 switches the harness consumption over.
Until then, the legacy heuristic paths remain the control-flow
primary — this module is additive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from supervisor.protocol.checkpoints import sanitize_checkpoint_payload
from supervisor.protocol.reason_code import is_valid_reason_code


LEGACY_SCHEMA_VERSION: Final[int] = 1
STRUCTURED_SCHEMA_VERSION: Final[int] = 2
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset(
    {LEGACY_SCHEMA_VERSION, STRUCTURED_SCHEMA_VERSION}
)

PROGRESS_CLASS_VALUES: Final[frozenset[str]] = frozenset(
    {"execution", "verification", "admin"}
)
EVIDENCE_SCOPE_VALUES: Final[frozenset[str]] = frozenset(
    {"current_node", "prior_phase", "unknown"}
)
ESCALATION_CLASS_VALUES: Final[frozenset[str]] = frozenset(
    # "recovery" is explicitly a runtime-owned class per the plan (Section
    # B, line 549). It is accepted here only so the contradiction detector
    # can see a worker-emitted "recovery" and demote it — the worker
    # protocol does not advertise it as a legal worker value.
    {"none", "business", "safety", "review", "recovery"}
)


@dataclass(frozen=True)
class NormalizedCheckpoint:
    """The single canonical shape consumed by downstream gate / recovery /
    eval layers.

    v1 payloads leave the v2 semantic fields as ``None`` (or empty tuple
    for lists). v2 payloads populate whatever the worker emitted; the
    normalizer intentionally does not synthesize missing fields.
    """

    schema_version: int
    status: str
    current_node: str
    summary: str
    run_id: str
    checkpoint_seq: int
    surface_id: str
    evidence: tuple[str, ...]
    candidate_next_actions: tuple[str, ...]
    needs: tuple[str, ...]
    question_for_supervisor: tuple[str, ...]

    # v2 semantic fields — frozen wire names. ``None`` / empty means
    # "not declared by the worker on this checkpoint".
    progress_class: str | None = None
    evidence_scope: str | None = None
    escalation_class: str | None = None
    requires_authorization: bool | None = None
    blocking_inputs: tuple[str, ...] = ()
    reason_code: str | None = None

    # Diagnostics — populated by later slices. Kept here so the shape
    # does not change when Slice 3 wires contradiction routing in.
    contradictions: tuple[str, ...] = ()

    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_legacy(self) -> bool:
        return self.schema_version == LEGACY_SCHEMA_VERSION


def parse_schema_version(raw: dict[str, Any]) -> int:
    """Return the checkpoint schema version declared by `raw`, defaulting
    to ``LEGACY_SCHEMA_VERSION`` when absent.

    Unknown versions are clamped to legacy — we do **not** infer "new
    style vs old style" from field presence (see decision B in the
    repartitioning doc). A worker that wants structured semantics must
    advertise them explicitly via `checkpoint_schema_version=2`.
    """

    raw_version = raw.get("checkpoint_schema_version") if isinstance(raw, dict) else None
    if raw_version is None:
        return LEGACY_SCHEMA_VERSION
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        return LEGACY_SCHEMA_VERSION
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return LEGACY_SCHEMA_VERSION
    return version


def normalize_checkpoint(
    raw: dict[str, Any],
    *,
    fallback_run_id: str = "",
    fallback_surface_id: str = "",
) -> NormalizedCheckpoint | None:
    """Normalize a raw checkpoint payload into the canonical shape.

    Returns ``None`` when the payload fails the existing sanitization
    rules (status / current_node / run_id validation). Otherwise returns
    a `NormalizedCheckpoint` carrying the schema version and, for v2
    payloads, the semantic fields.

    This is the single canonical entry point. Downstream layers should
    call this instead of reaching into raw dicts.
    """

    if not isinstance(raw, dict):
        return None

    sanitized = sanitize_checkpoint_payload(
        raw,
        fallback_run_id=fallback_run_id,
        fallback_surface_id=fallback_surface_id,
    )
    if sanitized is None:
        return None

    version = parse_schema_version(raw)

    # Per Section B of the repartitioning doc, the runtime MUST NOT infer
    # "new style vs old style" from field presence. Only payloads that
    # explicitly advertise ``checkpoint_schema_version=2`` are allowed to
    # carry structured v2 semantics; a v1 / missing-version payload that
    # happens to include ``requires_authorization`` or ``progress_class``
    # is treated as if those fields were not there. This keeps legacy
    # payloads from silently opting into v2 fast-paths on the gate layer.
    if version == STRUCTURED_SCHEMA_VERSION:
        progress_class = _normalize_enum(raw.get("progress_class"), PROGRESS_CLASS_VALUES)
        evidence_scope = _normalize_enum(raw.get("evidence_scope"), EVIDENCE_SCOPE_VALUES)
        escalation_class = _normalize_enum(
            raw.get("escalation_class"), ESCALATION_CLASS_VALUES
        )
        requires_authorization = _normalize_bool(raw.get("requires_authorization"))
        blocking_inputs = _normalize_string_list(raw.get("blocking_inputs"))
        reason_code = _normalize_reason_code(raw.get("reason_code"))
    else:
        progress_class = None
        evidence_scope = None
        escalation_class = None
        requires_authorization = None
        blocking_inputs = ()
        reason_code = None

    return NormalizedCheckpoint(
        schema_version=version,
        status=sanitized["status"],
        current_node=sanitized["current_node"],
        summary=sanitized["summary"],
        run_id=sanitized["run_id"],
        checkpoint_seq=sanitized["checkpoint_seq"],
        surface_id=sanitized["surface_id"],
        evidence=tuple(sanitized["evidence"]),
        candidate_next_actions=tuple(sanitized["candidate_next_actions"]),
        needs=tuple(sanitized["needs"]),
        question_for_supervisor=tuple(sanitized["question_for_supervisor"]),
        progress_class=progress_class,
        evidence_scope=evidence_scope,
        escalation_class=escalation_class,
        requires_authorization=requires_authorization,
        blocking_inputs=blocking_inputs,
        reason_code=reason_code,
        raw=dict(raw),
    )


def _normalize_enum(value: Any, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return text if text in allowed else None


def _normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _normalize_string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return tuple(result)


def _normalize_reason_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if is_valid_reason_code(text) else None
