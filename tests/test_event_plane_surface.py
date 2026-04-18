"""Tests for ``supervisor.event_plane.surface.summarize_for_session``.

The surface is the single passive read path that operator commands and
``SystemSnapshot`` aggregation use to peek at event-plane state without
taking control actions (Rule 4 + Rule 5 of the plan).
"""
from __future__ import annotations

import time

from supervisor.event_plane.models import (
    ExternalTaskRequest,
    SessionMailboxItem,
    SessionWait,
)
from supervisor.event_plane.store import EventPlaneStore
from supervisor.event_plane.surface import summarize_for_session


def test_empty_session_id_returns_zeroed_summary(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    summary = summarize_for_session(store, "")
    assert summary["waits_open"] == 0
    assert summary["mailbox_new"] == 0
    assert summary["mailbox_acknowledged"] == 0
    assert summary["requests_total"] == 0
    assert summary["latest_mailbox_item_id"] == ""
    assert summary["latest_wake_decision"] == ""


def test_summary_counts_waits_mailbox_requests(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))

    store.append_wait(SessionWait(
        session_id="s1", wait_kind="review", deadline_at="2099-01-01T00:00:00+00:00",
    ))
    store.append_mailbox_item(SessionMailboxItem(
        session_id="s1", request_id="r1", source_kind="external_review",
        summary="new item", payload={}, wake_decision="notify_operator",
    ))
    acked = SessionMailboxItem(
        session_id="s1", request_id="r2", source_kind="external_review",
        summary="acked item", payload={}, wake_decision="record_only",
    )
    store.append_mailbox_item(acked)
    acked_update = SessionMailboxItem.from_dict(acked.to_dict())
    acked_update.delivery_status = "acknowledged"
    store.append_mailbox_item(acked_update)
    store.append_request(ExternalTaskRequest(
        session_id="s1", provider="external_model", target_ref="t1",
    ))

    summary = summarize_for_session(store, "s1")
    assert summary["waits_open"] == 1
    assert summary["mailbox_new"] == 1
    assert summary["mailbox_acknowledged"] == 1
    assert summary["requests_total"] == 1


def test_summary_returns_latest_mailbox_and_wake_decision(tmp_path):
    """``summarize_for_session`` picks the newest mailbox item across
    *all* delivery_statuses so ``overview`` can answer
    "what happened most recently?" without a second query."""
    store = EventPlaneStore(str(tmp_path / "runtime"))

    first = SessionMailboxItem(
        session_id="s1", request_id="r1", source_kind="external_review",
        summary="first", payload={}, wake_decision="defer",
    )
    store.append_mailbox_item(first)
    # Ensure lexicographic created_at ordering differs across items.
    time.sleep(0.01)
    latest = SessionMailboxItem(
        session_id="s1", request_id="r2", source_kind="external_review",
        summary="latest", payload={}, wake_decision="wake_worker",
    )
    store.append_mailbox_item(latest)

    summary = summarize_for_session(store, "s1")
    assert summary["latest_mailbox_item_id"] == latest.mailbox_item_id
    assert summary["latest_wake_decision"] == "wake_worker"


def test_summary_isolated_across_sessions(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    store.append_mailbox_item(SessionMailboxItem(
        session_id="s1", request_id="r1", source_kind="external_review",
        summary="only s1", payload={}, wake_decision="wake_worker",
    ))

    s2 = summarize_for_session(store, "s2")
    assert s2["mailbox_new"] == 0
    assert s2["latest_mailbox_item_id"] == ""
    assert s2["latest_wake_decision"] == ""
