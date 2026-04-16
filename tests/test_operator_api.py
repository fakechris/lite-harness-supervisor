"""Tests for the operator channel canonical models and read APIs."""

import json
from pathlib import Path

from supervisor.operator.models import RunSnapshot, RunTimelineEvent
from supervisor.operator.api import (
    snapshot_from_state,
    timeline_from_session_log,
    recent_exchange,
    list_run_snapshots,
    _summarize_event,
)


# ── fixtures ────────────────────────────────────────────────────────

def _make_state(**overrides):
    base = {
        "run_id": "run_abc123",
        "spec_id": "my-spec",
        "mode": "linear_plan",
        "top_state": "RUNNING",
        "current_node_id": "step_1",
        "current_attempt": 1,
        "done_node_ids": [],
        "workspace_root": "/tmp/ws",
        "controller_mode": "daemon",
        "surface_type": "tmux",
        "pane_target": "%5",
        "delivery_state": "IDLE",
        "last_agent_checkpoint": {"summary": "working on tests"},
        "last_decision": {"next_instruction": "continue with step 1"},
        "human_escalations": [],
    }
    base.update(overrides)
    return base


def _write_session_log(path: Path, events: list[dict]):
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


# ── RunSnapshot tests ───────────────────────────────────────────────

def test_snapshot_from_running_state(tmp_path):
    state = _make_state()
    session_log = tmp_path / "session_log.jsonl"
    _write_session_log(session_log, [
        {"run_id": "run_abc123", "seq": 1, "event_type": "checkpoint",
         "timestamp": "2026-04-15T10:00:00Z", "payload": {"summary": "started"}},
    ])

    snap = snapshot_from_state(state, session_log)

    assert isinstance(snap, RunSnapshot)
    assert snap.run_id == "run_abc123"
    assert snap.spec_id == "my-spec"
    assert snap.top_state == "RUNNING"
    assert snap.current_node == "step_1"
    assert snap.surface_type == "tmux"
    assert snap.surface_target == "%5"
    assert snap.last_checkpoint_summary == "working on tests"
    assert snap.last_instruction_summary == "continue with step 1"
    assert snap.updated_at == "2026-04-15T10:00:00Z"
    assert snap.pause_reason == ""


def test_snapshot_from_paused_state(tmp_path):
    state = _make_state(
        top_state="PAUSED_FOR_HUMAN",
        human_escalations=[{"reason": "verification failed"}],
    )
    session_log = tmp_path / "session_log.jsonl"

    snap = snapshot_from_state(state, session_log)

    assert snap.top_state == "PAUSED_FOR_HUMAN"
    assert snap.pause_reason == "verification failed"


def test_snapshot_completed_waiting_for_review(tmp_path):
    state = _make_state(
        top_state="PAUSED_FOR_HUMAN",
        human_escalations=[{"reason": "requires review by: human"}],
    )
    session_log = tmp_path / "session_log.jsonl"

    snap = snapshot_from_state(state, session_log)

    assert snap.is_waiting_for_review is True
    assert "review" in snap.next_action


def test_snapshot_to_dict_roundtrip(tmp_path):
    state = _make_state()
    session_log = tmp_path / "session_log.jsonl"
    snap = snapshot_from_state(state, session_log)
    d = snap.to_dict()

    assert d["run_id"] == "run_abc123"
    assert isinstance(d["done_nodes"], list)
    assert "top_state" in d


def test_snapshot_no_session_log(tmp_path):
    state = _make_state()
    session_log = tmp_path / "nonexistent.jsonl"

    snap = snapshot_from_state(state, session_log)

    assert snap.run_id == "run_abc123"
    assert snap.updated_at == ""  # falls back to checkpoint ts if available


def test_snapshot_updated_at_from_checkpoint_when_no_log(tmp_path):
    state = _make_state(
        last_agent_checkpoint={"summary": "x", "timestamp": "2026-04-15T09:00:00Z"},
    )
    session_log = tmp_path / "nonexistent.jsonl"

    snap = snapshot_from_state(state, session_log)
    assert snap.updated_at == "2026-04-15T09:00:00Z"


# ── RunTimelineEvent tests ──────────────────────────────────────────

def test_timeline_from_session_log(tmp_path):
    session_log = tmp_path / "session_log.jsonl"
    events = [
        {"run_id": "run_abc", "seq": 1, "event_type": "checkpoint",
         "timestamp": "2026-04-15T10:00:00Z", "payload": {"summary": "started step_1"}},
        {"run_id": "run_abc", "seq": 2, "event_type": "gate_decision",
         "timestamp": "2026-04-15T10:01:00Z",
         "payload": {"decision": "CONTINUE", "reason": "on track"}},
        {"run_id": "run_abc", "seq": 3, "event_type": "injection",
         "timestamp": "2026-04-15T10:02:00Z",
         "payload": {"node_id": "step_2"}},
    ]
    _write_session_log(session_log, events)

    timeline = timeline_from_session_log(session_log, limit=10)

    assert len(timeline) == 3
    # Most recent first
    assert timeline[0].seq == 3
    assert timeline[0].event_type == "injection"
    assert "step_2" in timeline[0].summary
    assert timeline[2].seq == 1
    assert timeline[2].event_type == "checkpoint"


def test_timeline_limit(tmp_path):
    session_log = tmp_path / "session_log.jsonl"
    events = [
        {"run_id": "r", "seq": i, "event_type": "checkpoint",
         "timestamp": f"2026-04-15T10:{i:02d}:00Z", "payload": {"summary": f"step {i}"}}
        for i in range(1, 11)
    ]
    _write_session_log(session_log, events)

    timeline = timeline_from_session_log(session_log, limit=3)
    assert len(timeline) == 3
    assert timeline[0].seq == 10


def test_timeline_since_seq(tmp_path):
    session_log = tmp_path / "session_log.jsonl"
    events = [
        {"run_id": "r", "seq": i, "event_type": "checkpoint",
         "timestamp": f"2026-04-15T10:{i:02d}:00Z", "payload": {"summary": f"s{i}"}}
        for i in range(1, 6)
    ]
    _write_session_log(session_log, events)

    timeline = timeline_from_session_log(session_log, since_seq=3)
    assert len(timeline) == 2
    assert all(e.seq > 3 for e in timeline)


def test_timeline_empty_log(tmp_path):
    session_log = tmp_path / "nonexistent.jsonl"
    timeline = timeline_from_session_log(session_log)
    assert timeline == []


def test_timeline_event_to_dict(tmp_path):
    session_log = tmp_path / "session_log.jsonl"
    _write_session_log(session_log, [
        {"run_id": "r", "seq": 1, "event_type": "human_pause",
         "timestamp": "2026-04-15T10:00:00Z",
         "payload": {"pause_reason": "needs review"}},
    ])

    timeline = timeline_from_session_log(session_log)
    d = timeline[0].to_dict()
    assert d["event_type"] == "human_pause"
    assert d["seq"] == 1
    assert "needs review" in d["summary"]


# ── event summary tests ────────────────────────────────────────────

def test_summarize_checkpoint_event():
    s = _summarize_event("checkpoint", {"summary": "finished writing tests"})
    assert s == "finished writing tests"


def test_summarize_gate_decision():
    s = _summarize_event("gate_decision", {"decision": "CONTINUE", "reason": "on track"})
    assert "CONTINUE" in s
    assert "on track" in s


def test_summarize_verification_pass():
    s = _summarize_event("verification", {"ok": True})
    assert s == "pass"


def test_summarize_verification_fail():
    s = _summarize_event("verification", {"ok": False, "reason": "test failed"})
    assert "fail" in s
    assert "test failed" in s


def test_summarize_unknown_event():
    s = _summarize_event("some_custom_event", {})
    assert s == "some custom event"


# ── recent_exchange tests ──────────────────────────────────────────

def test_recent_exchange(tmp_path):
    state = _make_state()
    session_log = tmp_path / "session_log.jsonl"
    _write_session_log(session_log, [
        {"run_id": "run_abc123", "seq": 1, "event_type": "checkpoint",
         "timestamp": "2026-04-15T10:00:00Z", "payload": {"summary": "wrote code"}},
        {"run_id": "run_abc123", "seq": 2, "event_type": "injection",
         "timestamp": "2026-04-15T10:01:00Z", "payload": {"node_id": "step_1"}},
    ])

    ex = recent_exchange(state, session_log)

    assert ex["run_id"] == "run_abc123"
    assert ex["last_checkpoint_summary"] == "working on tests"
    assert "step_1" in ex["instruction_excerpt"]
    assert ex["recent_event_count"] == 2


def test_recent_exchange_no_events(tmp_path):
    state = _make_state()
    session_log = tmp_path / "nonexistent.jsonl"

    ex = recent_exchange(state, session_log)
    assert ex["recent_event_count"] == 0
    assert ex["last_checkpoint_summary"] == "working on tests"


# ── list_run_snapshots tests ───────────────────────────────────────

def test_list_run_snapshots(tmp_path):
    runtime_root = tmp_path / "runtime"
    runs_dir = runtime_root / "runs"

    # Create two run directories
    for rid in ["run_001", "run_002"]:
        d = runs_dir / rid
        d.mkdir(parents=True)
        state = _make_state(run_id=rid)
        (d / "state.json").write_text(json.dumps(state))

    snapshots = list_run_snapshots(runtime_root)

    assert len(snapshots) == 2
    ids = {s.run_id for s in snapshots}
    assert ids == {"run_001", "run_002"}


def test_list_run_snapshots_empty(tmp_path):
    snapshots = list_run_snapshots(tmp_path)
    assert snapshots == []


def test_list_run_snapshots_skips_corrupt(tmp_path):
    runtime_root = tmp_path / "runtime"
    runs_dir = runtime_root / "runs"

    # Good run
    good = runs_dir / "run_good"
    good.mkdir(parents=True)
    (good / "state.json").write_text(json.dumps(_make_state(run_id="run_good")))

    # Corrupt run
    bad = runs_dir / "run_bad"
    bad.mkdir(parents=True)
    (bad / "state.json").write_text("not json{{{")

    snapshots = list_run_snapshots(runtime_root)
    assert len(snapshots) == 1
    assert snapshots[0].run_id == "run_good"
