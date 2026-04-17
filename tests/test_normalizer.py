"""Slice 1 — canonical normalizer contract.

Covers:

- `checkpoint_schema_version` parsing (v1 default, v2 explicit, unknown
  versions clamped to legacy)
- a single `NormalizedCheckpoint` shape that survives both versions
- pass-through of the v2 semantic fields frozen in the repartitioning
  doc (progress_class, evidence_scope, escalation_class,
  requires_authorization, blocking_inputs, reason_code)
- invalid reason_code strings do not leak to downstream consumers

These are contract tests — if any of them fail, a downstream gate layer
that has started relying on the normalized shape would start misbehaving.
"""
from __future__ import annotations

from supervisor.protocol.normalizer import (
    LEGACY_SCHEMA_VERSION,
    STRUCTURED_SCHEMA_VERSION,
    NormalizedCheckpoint,
    normalize_checkpoint,
    parse_schema_version,
)


def _base_payload(**overrides) -> dict:
    payload = {
        "status": "working",
        "current_node": "step1",
        "summary": "progress",
        "run_id": "run_test",
        "checkpoint_seq": 1,
        "surface_id": "tmux:test",
    }
    payload.update(overrides)
    return payload


def test_parse_schema_version_defaults_to_legacy():
    assert parse_schema_version({}) == LEGACY_SCHEMA_VERSION


def test_parse_schema_version_reads_explicit_v2():
    assert (
        parse_schema_version({"checkpoint_schema_version": 2})
        == STRUCTURED_SCHEMA_VERSION
    )


def test_parse_schema_version_clamps_unknown():
    # A worker emitting v3 before the runtime supports it falls back to
    # legacy — we do not auto-upgrade based on field presence.
    assert parse_schema_version({"checkpoint_schema_version": 99}) == LEGACY_SCHEMA_VERSION
    assert parse_schema_version({"checkpoint_schema_version": "bad"}) == LEGACY_SCHEMA_VERSION


def test_normalize_v1_checkpoint_leaves_semantic_fields_none():
    result = normalize_checkpoint(_base_payload())
    assert result is not None
    assert result.schema_version == LEGACY_SCHEMA_VERSION
    assert result.is_legacy
    assert result.progress_class is None
    assert result.evidence_scope is None
    assert result.escalation_class is None
    assert result.requires_authorization is None
    assert result.blocking_inputs == ()
    assert result.reason_code is None


def test_normalize_v2_passes_through_semantic_fields():
    payload = _base_payload(
        checkpoint_schema_version=2,
        progress_class="execution",
        evidence_scope="current_node",
        escalation_class="none",
        requires_authorization=False,
        blocking_inputs=["missing api key"],
        reason_code="esc.missing_external_input",
    )
    result = normalize_checkpoint(payload)
    assert result is not None
    assert not result.is_legacy
    assert result.schema_version == STRUCTURED_SCHEMA_VERSION
    assert result.progress_class == "execution"
    assert result.evidence_scope == "current_node"
    assert result.escalation_class == "none"
    assert result.requires_authorization is False
    assert result.blocking_inputs == ("missing api key",)
    assert result.reason_code == "esc.missing_external_input"


def test_normalize_v2_rejects_invalid_reason_code():
    # A malformed reason_code must not bleed through — the normalizer is
    # the only component allowed to decode the prefix families, so a bad
    # value here would poison downstream routing.
    payload = _base_payload(
        checkpoint_schema_version=2,
        reason_code="garbage.not_a_family",
    )
    result = normalize_checkpoint(payload)
    assert result is not None
    assert result.reason_code is None


def test_normalize_v2_unknown_enum_becomes_none():
    payload = _base_payload(
        checkpoint_schema_version=2,
        progress_class="not_a_real_class",
        evidence_scope="somewhere_else",
        escalation_class="extra_loud",
    )
    result = normalize_checkpoint(payload)
    assert result is not None
    assert result.progress_class is None
    assert result.evidence_scope is None
    assert result.escalation_class is None


def test_normalize_v2_blocking_inputs_accepts_scalar_and_list():
    scalar = normalize_checkpoint(
        _base_payload(checkpoint_schema_version=2, blocking_inputs="just one thing")
    )
    assert scalar is not None
    assert scalar.blocking_inputs == ("just one thing",)

    listy = normalize_checkpoint(
        _base_payload(checkpoint_schema_version=2, blocking_inputs=["a", "", "  b  "])
    )
    assert listy is not None
    assert listy.blocking_inputs == ("a", "b")


def test_normalize_v2_requires_authorization_coerces_bool_like_strings():
    truthy = normalize_checkpoint(
        _base_payload(checkpoint_schema_version=2, requires_authorization="yes")
    )
    assert truthy is not None
    assert truthy.requires_authorization is True

    falsy = normalize_checkpoint(
        _base_payload(checkpoint_schema_version=2, requires_authorization="no")
    )
    assert falsy is not None
    assert falsy.requires_authorization is False

    absent = normalize_checkpoint(
        _base_payload(checkpoint_schema_version=2, requires_authorization=None)
    )
    assert absent is not None
    assert absent.requires_authorization is None


def test_normalize_returns_none_for_invalid_payload():
    # sanitize_checkpoint_payload rejects payloads missing required keys,
    # and the normalizer mirrors that — no partial NormalizedCheckpoint.
    assert normalize_checkpoint(None) is None
    assert normalize_checkpoint({}) is None


def test_normalized_checkpoint_is_frozen():
    result = normalize_checkpoint(_base_payload())
    assert result is not None
    try:
        result.status = "broken"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("NormalizedCheckpoint must be immutable (dataclass(frozen=True))")
