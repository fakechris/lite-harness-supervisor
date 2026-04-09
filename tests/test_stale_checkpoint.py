"""Tests for checkpoint dedup and stale detection."""
from supervisor.adapters.transcript_adapter import TranscriptAdapter
from supervisor.domain.models import Checkpoint


def test_parse_returns_checkpoint_dataclass():
    adapter = TranscriptAdapter()
    text = """
<checkpoint>
status: step_done
current_node: write_test
summary: wrote the test
evidence:
  - modified: tests/test_example.py
candidate_next_actions:
  - implement feature
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
"""
    cp = adapter.parse_checkpoint(text)
    assert isinstance(cp, Checkpoint)
    assert cp.status == "step_done"
    assert cp.current_node == "write_test"
    assert cp.summary == "wrote the test"


def test_parse_with_seq():
    adapter = TranscriptAdapter()
    text = """
<checkpoint>
status: working
current_node: impl
summary: progress
run_id: run_abc
checkpoint_seq: 5
evidence:
  - ran: echo ok
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
"""
    cp = adapter.parse_checkpoint(text)
    assert cp.run_id == "run_abc"
    assert cp.checkpoint_seq == 5


def test_parse_returns_none_for_no_checkpoint():
    adapter = TranscriptAdapter()
    assert adapter.parse_checkpoint("just some text") is None


def test_parse_returns_none_for_incomplete_checkpoint():
    adapter = TranscriptAdapter()
    text = "<checkpoint>\nstatus: working\n</checkpoint>"
    # Missing current_node — should return None
    assert adapter.parse_checkpoint(text) is None


def test_latest_checkpoint_wins():
    adapter = TranscriptAdapter()
    text = """
<checkpoint>
status: working
current_node: step1
summary: old one
</checkpoint>
some output
<checkpoint>
status: step_done
current_node: step1
summary: new one
</checkpoint>
"""
    cp = adapter.parse_checkpoint(text)
    assert cp.summary == "new one"
    assert cp.status == "step_done"
