"""Slice 1 — wire-level `reason_code` format / constants guard.

These tests lock the frozen contract from the fat-skill / thin-harness
repartitioning doc (Decision B + Structured Protocol Additions):

- exactly four prefix families: esc / rec / ver / sem
- all exported constants match the regex
- no code drift between module-level constants and `KNOWN_REASON_CODES`

If you are adding a new code, update `reason_code.py` AND the
repartitioning doc — this test is meant to scream at silent additions.
"""
from __future__ import annotations

import re

import pytest

from supervisor.protocol import reason_code as rc


def test_families_are_frozen_at_four():
    assert rc.REASON_CODE_FAMILIES == frozenset({"esc", "rec", "ver", "sem"})


def test_all_known_codes_match_regex():
    pattern = re.compile(r"^(esc|rec|ver|sem)\.[a-z][a-z0-9_]*$")
    for code in rc.KNOWN_REASON_CODES:
        assert pattern.match(code), f"malformed reason_code: {code!r}"


def test_is_valid_reason_code_accepts_known():
    for code in rc.KNOWN_REASON_CODES:
        assert rc.is_valid_reason_code(code)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        "unknown.family",
        "esc.",
        ".foo",
        "esc.Bad_Case",  # uppercase not allowed
        "esc.1_starts_digit",  # must start with lowercase letter
        "esc..double",
    ],
)
def test_is_valid_reason_code_rejects_bad(bad):
    assert not rc.is_valid_reason_code(bad)


def test_validate_reason_code_returns_input_or_raises():
    assert rc.validate_reason_code(rc.REC_DELIVERY_TIMEOUT) == rc.REC_DELIVERY_TIMEOUT
    with pytest.raises(rc.ReasonCodeError):
        rc.validate_reason_code("not.a.valid.code")


def test_family_lookup_matches_prefix():
    for code in rc.KNOWN_REASON_CODES:
        family = rc.reason_code_family(code)
        assert code.startswith(family + ".")
        assert family in rc.REASON_CODE_FAMILIES


def test_constants_are_in_known_set():
    # Anything named ESC_* / REC_* / VER_* / SEM_* in the module should
    # appear in KNOWN_REASON_CODES. If a new constant shows up without
    # being added to the known set, this test fires.
    for name in dir(rc):
        if any(name.startswith(prefix + "_") for prefix in ("ESC", "REC", "VER", "SEM")):
            value = getattr(rc, name)
            if isinstance(value, str):
                assert value in rc.KNOWN_REASON_CODES, (
                    f"{name}={value!r} missing from KNOWN_REASON_CODES"
                )


def test_authorization_contradiction_lives_in_esc_family():
    # Decision E (contradiction routing) keeps the safety contradiction in
    # esc.* because it is a fail-closed escalation, not a mere protocol
    # integrity issue. If this ever moves to sem.*, the routing table in
    # the doc must move too.
    assert rc.ESC_AUTHORIZATION_CONTRADICTION.startswith("esc.")


def test_sem_family_carries_non_safety_contradictions():
    for code in (
        rc.SEM_PROGRESS_CLASS_CONTRADICTION,
        rc.SEM_EVIDENCE_SCOPE_CONTRADICTION,
        rc.SEM_BLOCKING_INPUTS_CONTRADICTION,
        rc.SEM_ESCALATION_CLASS_CONTRADICTION,
        rc.SEM_RUNTIME_OWNED_FIELD_CONFLICT,
    ):
        assert code.startswith("sem.")
