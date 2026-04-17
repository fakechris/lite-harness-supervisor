"""Slice 1 — ESCALATE_TO_HUMAN decisions carry a typed `reason_code`.

No consumer routes on `reason_code` yet (that switch lands in Slice 3),
but emit sites must pair their prose reasons with the corresponding
`esc.*` / `rec.*` code today so the wire format stays honest across v1 /
v2 checkpoints.

Pins the mapping for the three classifier-driven escalation classes and
guards against drift between `classify_for_escalation` and the shared
`_ESCALATION_REASON` table.
"""
from __future__ import annotations

import pytest

from supervisor.domain.enums import DecisionType
from supervisor.gates.escalation import (
    ESCALATION_CLASSES,
    escalation_decision,
)
from supervisor.protocol.reason_code import (
    ESC_BLOCKED_GENUINE,
    ESC_DANGEROUS_IRREVERSIBLE,
    ESC_MISSING_EXTERNAL_INPUT,
    is_valid_reason_code,
)


@pytest.mark.parametrize(
    "hit,expected_code",
    [
        ("MISSING_EXTERNAL_INPUT", ESC_MISSING_EXTERNAL_INPUT),
        ("DANGEROUS_ACTION", ESC_DANGEROUS_IRREVERSIBLE),
        ("BLOCKED", ESC_BLOCKED_GENUINE),
    ],
)
def test_escalation_decision_carries_reason_code(hit, expected_code):
    decision = escalation_decision(hit, gate_type="continue")
    assert decision.decision == DecisionType.ESCALATE_TO_HUMAN.value
    assert decision.reason_code == expected_code
    assert is_valid_reason_code(decision.reason_code)


def test_every_escalation_class_has_reason_code():
    # If someone adds a new class to `ESCALATION_CLASSES` without
    # updating `_ESCALATION_REASON`, this test fails loudly.
    for hit in ESCALATION_CLASSES:
        decision = escalation_decision(hit, gate_type="continue")
        assert decision.reason_code is not None
        assert decision.reason_code.startswith("esc.")


def test_reason_code_survives_to_dict_roundtrip():
    # Persistence relies on `asdict(self)`; reason_code must be on the
    # wire once the decision is serialised.
    decision = escalation_decision("MISSING_EXTERNAL_INPUT", gate_type="continue")
    payload = decision.to_dict()
    assert payload["reason_code"] == ESC_MISSING_EXTERNAL_INPUT
