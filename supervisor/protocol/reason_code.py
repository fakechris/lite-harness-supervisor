"""Wire-level `reason_code` format and stable code constants.

Per the fat-skill / thin-harness repartitioning
(`docs/plans/2026-04-17-fat-skill-thin-harness-rule-repartitioning.md`,
Section B / Structured Protocol Additions), the checkpoint protocol
carries a single `reason_code` field scoped by one of four prefix
families:

- ``esc.*`` — escalation causes, including fail-closed safety
  contradictions (primarily worker-declared need for human input /
  authorization, plus runtime-raised ``esc.authorization_contradiction``).
- ``rec.*`` — recovery causes (runtime-owned failure modes).
- ``ver.*`` — verification failures (test / acceptance results).
- ``sem.*`` — semantic contradictions and protocol-integrity issues.

The normalizer (see `supervisor/protocol/normalizer.py`) is the only
component allowed to decode these prefixes into internal typed
semantics. Gate layers and intervention planners should receive
already-normalized objects; they should not parse `reason_code` strings
directly.

Slice 1 only introduces the constants and validation helpers — no
existing consumer is switched to route on `reason_code` yet. Emit sites
pair the new code with their current prose reason; the wire-level
migration happens in Slice 3.
"""
from __future__ import annotations

import re
from typing import Final


# Frozen families. Growing this set is a protocol change — update the
# repartitioning doc (decision B + `reason_code` subsection) first.
REASON_CODE_FAMILIES: Final[frozenset[str]] = frozenset({"esc", "rec", "ver", "sem"})

_REASON_CODE_RE = re.compile(r"^(esc|rec|ver|sem)\.[a-z][a-z0-9_]*$")


# --- Escalation causes ---

ESC_MISSING_EXTERNAL_INPUT: Final[str] = "esc.missing_external_input"
ESC_AUTHORIZATION_REQUIRED: Final[str] = "esc.authorization_required"
ESC_REVIEW_REQUIRED: Final[str] = "esc.review_required"
ESC_BLOCKED_GENUINE: Final[str] = "esc.blocked_genuine"
ESC_AUTHORIZATION_CONTRADICTION: Final[str] = "esc.authorization_contradiction"
ESC_DANGEROUS_IRREVERSIBLE: Final[str] = "esc.dangerous_irreversible"


# --- Recovery causes ---

REC_DELIVERY_TIMEOUT: Final[str] = "rec.delivery_timeout"
REC_IDLE_TIMEOUT: Final[str] = "rec.idle_timeout"
REC_INJECT_FAILED: Final[str] = "rec.inject_failed"
REC_NODE_MISMATCH_PERSISTED: Final[str] = "rec.node_mismatch_persisted"
REC_RETRY_BUDGET_EXHAUSTED: Final[str] = "rec.retry_budget_exhausted"
REC_REINJECTION_EXHAUSTED: Final[str] = "rec.reinjection_exhausted"
REC_VERIFICATION_RETRY_EXHAUSTED: Final[str] = "rec.verification_retry_exhausted"
REC_CRASH_DURING_RECOVERY: Final[str] = "rec.crash_during_recovery"


# --- Verification failures ---

VER_TEST_FAILED: Final[str] = "ver.test_failed"


# --- Semantic contradictions and protocol-integrity issues ---

SEM_PROGRESS_CLASS_CONTRADICTION: Final[str] = "sem.progress_class_contradiction"
SEM_EVIDENCE_SCOPE_CONTRADICTION: Final[str] = "sem.evidence_scope_contradiction"
SEM_BLOCKING_INPUTS_CONTRADICTION: Final[str] = "sem.blocking_inputs_contradiction"
SEM_ESCALATION_CLASS_CONTRADICTION: Final[str] = "sem.escalation_class_contradiction"
SEM_RUNTIME_OWNED_FIELD_CONFLICT: Final[str] = "sem.runtime_owned_field_conflict"


class ReasonCodeError(ValueError):
    """Raised when a supplied `reason_code` does not match the frozen
    wire format (one of the four family prefixes + a snake_case tail)."""


def is_valid_reason_code(code: str | None) -> bool:
    """Return True iff `code` is a known, wire-valid reason_code.

    Validation is a whitelist against `KNOWN_REASON_CODES` — not just a
    regex match against the four family prefixes. Why: the normalizer is
    the only decoder of this wire field, and "looks like esc.*" is not
    enough to trust a code. An LLM-hallucinated code like
    ``esc.i_made_this_up`` matches the regex but is meaningless to every
    downstream consumer (analytics, routing, replay). Fail closed so
    those systems only ever see canonical values.
    """

    if not isinstance(code, str) or not code:
        return False
    if not _REASON_CODE_RE.match(code):
        return False
    return code in KNOWN_REASON_CODES


def validate_reason_code(code: str) -> str:
    """Return `code` unchanged if valid; raise `ReasonCodeError` otherwise.

    Useful at emit sites that already hold a constant — this is cheap
    insurance against typos creeping in at runtime.
    """

    if not is_valid_reason_code(code):
        raise ReasonCodeError(
            f"invalid reason_code {code!r}; expected `<family>.<name>` "
            f"with family in {sorted(REASON_CODE_FAMILIES)}"
        )
    return code


def reason_code_family(code: str) -> str:
    """Return the family prefix (`esc` / `rec` / `ver` / `sem`) of `code`.

    Raises `ReasonCodeError` when the code is malformed. The normalizer
    is the only component that should call this — gate layers consume
    already-typed values instead.
    """

    if not is_valid_reason_code(code):
        raise ReasonCodeError(f"invalid reason_code {code!r}")
    return code.split(".", 1)[0]


# Stable set of every code currently emitted by the harness. New codes
# should be added here and in the module-level constants above; tests
# guard against drift so this stays honest.
KNOWN_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        ESC_MISSING_EXTERNAL_INPUT,
        ESC_AUTHORIZATION_REQUIRED,
        ESC_REVIEW_REQUIRED,
        ESC_BLOCKED_GENUINE,
        ESC_AUTHORIZATION_CONTRADICTION,
        ESC_DANGEROUS_IRREVERSIBLE,
        REC_DELIVERY_TIMEOUT,
        REC_IDLE_TIMEOUT,
        REC_INJECT_FAILED,
        REC_NODE_MISMATCH_PERSISTED,
        REC_RETRY_BUDGET_EXHAUSTED,
        REC_REINJECTION_EXHAUSTED,
        REC_VERIFICATION_RETRY_EXHAUSTED,
        REC_CRASH_DURING_RECOVERY,
        VER_TEST_FAILED,
        SEM_PROGRESS_CLASS_CONTRADICTION,
        SEM_EVIDENCE_SCOPE_CONTRADICTION,
        SEM_BLOCKING_INPUTS_CONTRADICTION,
        SEM_ESCALATION_CLASS_CONTRADICTION,
        SEM_RUNTIME_OWNED_FIELD_CONFLICT,
    }
)
