"""Tests for the shared `system_events.jsonl` observability log.

Covers:
- the frozen inclusion allowlist (kinds + state-transition to-state filter)
- append + read round-trip through the POSIX-lock protected append helper
- no-op writes when a kind is not allowlisted
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.storage.system_events import (
    ALLOWED_SYSTEM_EVENT_KINDS,
    STATE_TRANSITION_ALLOWED_TO_STATES,
    append_system_event,
    read_recent_system_events,
    should_log_system_event,
)


def test_allowlist_contains_the_frozen_v1_kinds():
    """Task 3 froze the v1 system-event surface at six kinds.  The A2A
    inbound adapter (2026-04-19) extends it by two lifecycle kinds so
    ``overview`` can surface listener host/port/auth-mode at a glance —
    still a deliberate, spelled-out addition, not silent drift."""
    assert ALLOWED_SYSTEM_EVENT_KINDS == frozenset({
        "daemon_started",
        "daemon_stopped",
        "state_transition",
        "session_wait_expired",
        "session_mailbox_item_created",
        "wake_decision_applied",
        "a2a_started",
        "a2a_stopped",
    })


def test_state_transition_allowed_to_states_match_spec():
    """Only state transitions that matter at the system level get
    promoted — RUNNING↔GATING↔VERIFYING churn stays in per-run logs."""
    assert STATE_TRANSITION_ALLOWED_TO_STATES == frozenset({
        "PAUSED_FOR_HUMAN",
        "RECOVERY_NEEDED",
        "COMPLETED",
        "FAILED",
        "ABORTED",
    })


def test_should_log_system_event_accepts_kind_not_state_transition():
    assert should_log_system_event("daemon_started", {"daemon_id": "d1"})
    assert should_log_system_event("daemon_stopped", {"daemon_id": "d1"})
    assert should_log_system_event("session_wait_expired", {"wait_id": "w1"})
    assert should_log_system_event("session_mailbox_item_created",
                                   {"mailbox_item_id": "mb1"})
    assert should_log_system_event("wake_decision_applied",
                                   {"decision": "wake_worker"})


def test_should_log_system_event_filters_state_transition_by_to_state():
    # Allowlisted high-signal transitions pass through.
    for ts in ("PAUSED_FOR_HUMAN", "RECOVERY_NEEDED", "COMPLETED", "FAILED", "ABORTED"):
        assert should_log_system_event(
            "state_transition", {"from_state": "RUNNING", "to_state": ts}
        ), ts
    # Everyday churn is suppressed at the system level.
    for ts in ("RUNNING", "GATING", "VERIFYING", "ATTACHED"):
        assert not should_log_system_event(
            "state_transition", {"from_state": "GATING", "to_state": ts}
        ), ts


def test_should_log_system_event_rejects_unknown_kinds():
    assert not should_log_system_event("agent_idle_timeout", {})
    assert not should_log_system_event("", {})
    assert not should_log_system_event("checkpoint", {"summary": "x"})


def test_append_and_read_round_trip(tmp_path: Path):
    runtime = tmp_path / "runtime"
    append_system_event(runtime, "daemon_started", {"daemon_id": "d1"})
    append_system_event(runtime, "daemon_stopped", {"daemon_id": "d1"})
    events = read_recent_system_events(runtime, limit=10)
    assert [e["event_type"] for e in events] == ["daemon_stopped", "daemon_started"]
    for ev in events:
        assert "occurred_at" in ev and ev["occurred_at"]
        assert "payload" in ev and isinstance(ev["payload"], dict)


def test_append_respects_allowlist_for_state_transition(tmp_path: Path):
    runtime = tmp_path / "runtime"
    # High-signal transition persists.
    append_system_event(
        runtime, "state_transition",
        {"from_state": "RUNNING", "to_state": "PAUSED_FOR_HUMAN", "reason": "x"},
    )
    # Churn is dropped silently.
    append_system_event(
        runtime, "state_transition",
        {"from_state": "RUNNING", "to_state": "GATING"},
    )
    events = read_recent_system_events(runtime, limit=10)
    assert len(events) == 1
    assert events[0]["payload"]["to_state"] == "PAUSED_FOR_HUMAN"


def test_append_respects_allowlist_for_unknown_kind(tmp_path: Path):
    runtime = tmp_path / "runtime"
    append_system_event(runtime, "not_on_allowlist", {"x": 1})
    assert read_recent_system_events(runtime, limit=10) == []
    # File is not created when no event was actually written.
    shared = runtime / "shared" / "system_events.jsonl"
    assert not shared.exists()


def test_read_recent_handles_missing_file(tmp_path: Path):
    assert read_recent_system_events(tmp_path / "nonexistent", limit=10) == []


def test_read_recent_limit_returns_newest_first(tmp_path: Path):
    runtime = tmp_path / "runtime"
    for i in range(5):
        append_system_event(runtime, "daemon_started", {"daemon_id": f"d{i}"})
    events = read_recent_system_events(runtime, limit=3)
    assert len(events) == 3
    # Newest first: last appended daemon_id ("d4") leads.
    assert events[0]["payload"]["daemon_id"] == "d4"
    assert events[1]["payload"]["daemon_id"] == "d3"
    assert events[2]["payload"]["daemon_id"] == "d2"


def test_append_is_best_effort_when_write_fails(tmp_path: Path, monkeypatch):
    """Observability must never block a production code path.

    Simulate a filesystem that rejects the append (read-only mount,
    permission denied, disk full) and verify the call returns False
    cleanly rather than raising into the caller.
    """
    runtime = tmp_path / "runtime"

    def _raise(*_args, **_kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(
        "supervisor.storage.system_events._atomic_append_line", _raise,
    )
    ok = append_system_event(
        runtime, "daemon_started", {"daemon_id": "d_fail"},
    )
    assert ok is False
