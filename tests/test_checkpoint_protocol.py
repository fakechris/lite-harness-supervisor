from pathlib import Path

from supervisor.adapters.transcript_adapter import TranscriptAdapter
from supervisor.protocol.checkpoints import checkpoint_example_block, sanitize_checkpoint_payload


def test_checkpoint_example_matches_runtime_prompt_template():
    prompt = Path("supervisor/llm/prompts/checkpoint_protocol.txt").read_text(encoding="utf-8")
    assert checkpoint_example_block("<step_id>") in prompt


def test_transcript_adapter_rejects_unknown_checkpoint_status():
    adapter = TranscriptAdapter()
    text = """
<checkpoint>
status: almost_done
current_node: step1
summary: invalid status
</checkpoint>
"""
    assert adapter.parse_checkpoint(text) is None


def test_transcript_adapter_normalizes_structured_evidence_entries():
    adapter = TranscriptAdapter()
    text = """
<checkpoint>
status: working
current_node: step1
summary: progress
evidence:
  - modified: src/app.py
  - ran: pytest -q
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
"""
    checkpoint = adapter.parse_checkpoint(text)
    assert checkpoint is not None
    assert checkpoint.evidence == ["modified: src/app.py", "ran: pytest -q"]


def test_sanitize_checkpoint_payload_preserves_multiple_structured_evidence_parts():
    payload = sanitize_checkpoint_payload(
        {
            "status": "working",
            "current_node": "step1",
            "summary": "progress",
            "evidence": [
                {
                    "modified": "src/app.py",
                    "ran": "pytest -q",
                }
            ],
        }
    )

    assert payload is not None
    assert payload["evidence"] == ["modified: src/app.py; ran: pytest -q"]
