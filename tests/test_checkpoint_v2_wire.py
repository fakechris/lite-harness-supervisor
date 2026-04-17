"""Slice 2 — worker-side checkpoint wire format carries v2 semantic fields.

The prompt template, the sanitizer, the transcript adapter and the
`Checkpoint` dataclass must all agree on the v2 field set so the
normalizer in Slice 3 can read them via `state.last_agent_checkpoint`.
"""
from __future__ import annotations

from pathlib import Path

from supervisor.adapters.transcript_adapter import TranscriptAdapter
from supervisor.protocol.checkpoints import (
    CHECKPOINT_STRUCTURED_VERSION,
    checkpoint_example_block,
    sanitize_checkpoint_payload,
)
from supervisor.protocol.normalizer import normalize_checkpoint


PROMPT_PATH = Path("supervisor/llm/prompts/checkpoint_protocol.txt")


def test_prompt_declares_v2_and_all_semantic_fields():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    for field in (
        "checkpoint_schema_version: 2",
        "progress_class:",
        "evidence_scope:",
        "escalation_class:",
        "requires_authorization:",
        "blocking_inputs:",
        "reason_code:",
    ):
        assert field in text, f"prompt missing v2 field: {field}"


def test_example_block_matches_prompt():
    # The runtime example block is the source of truth for the worker
    # prompt — if they drift, the worker could emit a schema the adapter
    # no longer parses.
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert checkpoint_example_block("<step_id>") in prompt


def test_sanitize_preserves_all_v2_scalar_fields():
    raw = {
        "status": "blocked",
        "current_node": "step1",
        "summary": "waiting on token",
        "checkpoint_schema_version": 2,
        "progress_class": "execution",
        "evidence_scope": "current_node",
        "escalation_class": "business",
        "requires_authorization": False,
        "blocking_inputs": ["GITHUB_TOKEN"],
        "reason_code": "esc.missing_external_input",
    }
    sanitized = sanitize_checkpoint_payload(raw)
    assert sanitized is not None
    assert sanitized["checkpoint_schema_version"] == CHECKPOINT_STRUCTURED_VERSION
    assert sanitized["progress_class"] == "execution"
    assert sanitized["evidence_scope"] == "current_node"
    assert sanitized["escalation_class"] == "business"
    assert sanitized["requires_authorization"] is False
    assert sanitized["blocking_inputs"] == ["GITHUB_TOKEN"]
    assert sanitized["reason_code"] == "esc.missing_external_input"


def test_sanitize_clamps_unknown_schema_version_to_zero():
    raw = {
        "status": "working",
        "current_node": "step1",
        "summary": "progress",
        "checkpoint_schema_version": 99,
    }
    sanitized = sanitize_checkpoint_payload(raw)
    assert sanitized is not None
    assert sanitized["checkpoint_schema_version"] == 0


def test_adapter_round_trips_v2_yaml_block():
    adapter = TranscriptAdapter()
    text = """
<checkpoint>
run_id: run_v2
checkpoint_seq: 4
checkpoint_schema_version: 2
status: working
current_node: step1
summary: ran pytest, 5 passed
progress_class: verification
evidence_scope: current_node
escalation_class: none
requires_authorization: false
blocking_inputs: []
reason_code:
evidence:
  - ran: pytest -q
  - result: 5 passed
candidate_next_actions:
  - move on to step2
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
"""
    cp = adapter.parse_checkpoint(text, run_id="run_v2", surface_id="tmux:test")
    assert cp is not None
    assert cp.checkpoint_schema_version == CHECKPOINT_STRUCTURED_VERSION
    assert cp.progress_class == "verification"
    assert cp.evidence_scope == "current_node"
    assert cp.escalation_class == "none"
    assert cp.requires_authorization is False
    assert cp.blocking_inputs == []
    assert cp.reason_code is None


def test_adapter_round_trips_v2_line_format_fallback():
    # The YAML path is the primary; the line-based fallback exists for
    # payloads that fail yaml.safe_load. Both paths must agree on v2.
    adapter = TranscriptAdapter()
    raw = adapter._parse_lines(
        "\n".join(
            [
                "status: blocked",
                "current_node: step1",
                "summary: need token",
                "checkpoint_schema_version: 2",
                "progress_class: admin",
                "escalation_class: business",
                "requires_authorization: false",
                "reason_code: esc.missing_external_input",
                "blocking_inputs:",
                "  - GITHUB_TOKEN",
            ]
        )
    )
    assert raw["checkpoint_schema_version"] == "2"
    assert raw["progress_class"] == "admin"
    assert raw["escalation_class"] == "business"
    assert raw["requires_authorization"] == "false"
    assert raw["reason_code"] == "esc.missing_external_input"
    assert raw["blocking_inputs"] == ["GITHUB_TOKEN"]


def test_v2_adapter_payload_survives_normalize_round_trip():
    # Parse → to_dict → normalize should recover every v2 field. This is
    # how Slice 3 will get them: the loop stores `cp.to_dict()` into
    # `state.last_agent_checkpoint`, then the normalizer reads from there.
    adapter = TranscriptAdapter()
    text = """
<checkpoint>
run_id: run_v2
checkpoint_seq: 5
checkpoint_schema_version: 2
status: blocked
current_node: step1
summary: waiting on token
progress_class: admin
evidence_scope: prior_phase
escalation_class: business
requires_authorization: false
blocking_inputs:
  - GITHUB_TOKEN
reason_code: esc.missing_external_input
evidence:
  - attach: opened pane
needs:
  - none
question_for_supervisor:
  - need GITHUB_TOKEN
</checkpoint>
"""
    cp = adapter.parse_checkpoint(text, run_id="run_v2", surface_id="tmux:test")
    assert cp is not None
    normalized = normalize_checkpoint(cp.to_dict())
    assert normalized is not None
    assert normalized.schema_version == CHECKPOINT_STRUCTURED_VERSION
    assert normalized.progress_class == "admin"
    assert normalized.evidence_scope == "prior_phase"
    assert normalized.escalation_class == "business"
    assert normalized.requires_authorization is False
    assert normalized.blocking_inputs == ("GITHUB_TOKEN",)
    assert normalized.reason_code == "esc.missing_external_input"
