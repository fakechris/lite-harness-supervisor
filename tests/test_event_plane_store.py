"""Tests for EventPlaneStore — append-only durable substrate for event-plane records.

Storage contract:
- external_tasks.jsonl holds both request and result records, discriminated by record_type
- session_waits.jsonl holds wait records (append-only; latest-per-wait_id wins)
- session_mailbox.jsonl holds mailbox items (append-only; latest-per-mailbox_item_id wins)

Helper query contract:
- latest request state (fold requests by request_id)
- open waits (status=waiting), optionally filtered to past-deadline
- mailbox items by session, optionally filtered by delivery_status
"""
from __future__ import annotations

from supervisor.event_plane.models import (
    ExternalTaskRequest,
    ExternalTaskResult,
    SessionMailboxItem,
    SessionWait,
)
from supervisor.event_plane.store import EventPlaneStore


def test_round_trip_request_and_fold_to_latest(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    req = ExternalTaskRequest(
        session_id="s1",
        provider="external_model",
        target_ref="pr1",
    )
    store.append_request(req)

    # Status transition — append updated request.
    updated = ExternalTaskRequest.from_dict(req.to_dict())
    updated.status = "in_flight"
    store.append_request(updated)

    latest = store.latest_request(req.request_id)
    assert latest is not None
    assert latest.status == "in_flight"


def test_append_result_and_list_for_request(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    req = ExternalTaskRequest(
        session_id="s1",
        provider="external_model",
        target_ref="pr1",
    )
    store.append_request(req)

    res = ExternalTaskResult(
        request_id=req.request_id,
        session_id="s1",
        provider="external_model",
        result_kind="review_comments",
        summary="LGTM",
        payload={},
    )
    store.append_result(res)

    results = store.list_results_for_request(req.request_id)
    assert len(results) == 1
    assert results[0].result_id == res.result_id


def test_append_wait_and_list_open(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    w1 = SessionWait(session_id="s1", request_id="r1", wait_kind="external_review")
    w2 = SessionWait(session_id="s2", request_id="r2", wait_kind="external_review")
    store.append_wait(w1)
    store.append_wait(w2)

    # Resolve one; it should drop out of "open".
    resolved = SessionWait.from_dict(w2.to_dict())
    resolved.status = "satisfied"
    store.append_wait(resolved)

    opens = {w.wait_id for w in store.list_open_waits()}
    assert w1.wait_id in opens
    assert w2.wait_id not in opens


def test_list_open_waits_filters_past_deadline(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    past = SessionWait(
        session_id="s1",
        request_id="r1",
        wait_kind="external_review",
        deadline_at="2000-01-01T00:00:00+00:00",  # definitely past
    )
    future = SessionWait(
        session_id="s1",
        request_id="r2",
        wait_kind="external_review",
        deadline_at="2099-01-01T00:00:00+00:00",
    )
    store.append_wait(past)
    store.append_wait(future)

    expired = {w.wait_id for w in store.list_open_waits(past_deadline_only=True)}
    assert past.wait_id in expired
    assert future.wait_id not in expired


def test_mailbox_items_by_session_and_latest_wins(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    item = SessionMailboxItem(
        session_id="s1",
        request_id="r1",
        source_kind="external_review",
        summary="x",
        payload={},
    )
    store.append_mailbox_item(item)

    # Transition: surface it.
    transitioned = SessionMailboxItem.from_dict(item.to_dict())
    transitioned.delivery_status = "surfaced"
    store.append_mailbox_item(transitioned)

    items = store.list_mailbox_items(session_id="s1")
    assert len(items) == 1
    assert items[0].delivery_status == "surfaced"

    # Other session: isolation.
    other_item = SessionMailboxItem(
        session_id="s2",
        request_id="r2",
        source_kind="external_review",
        summary="y",
        payload={},
    )
    store.append_mailbox_item(other_item)
    assert [i.session_id for i in store.list_mailbox_items(session_id="s1")] == ["s1"]


def test_mailbox_items_filter_by_delivery_status(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    a = SessionMailboxItem(session_id="s1", request_id="r1", source_kind="external_review", summary="a", payload={})
    b = SessionMailboxItem(session_id="s1", request_id="r2", source_kind="external_review", summary="b", payload={})
    store.append_mailbox_item(a)
    store.append_mailbox_item(b)

    b2 = SessionMailboxItem.from_dict(b.to_dict())
    b2.delivery_status = "consumed"
    store.append_mailbox_item(b2)

    new_items = store.list_mailbox_items(session_id="s1", delivery_status="new")
    assert [i.mailbox_item_id for i in new_items] == [a.mailbox_item_id]


def test_list_requests_by_session_handles_run_id_none(tmp_path):
    """Task 2 invariant: helpers must handle run_id=None from day 1."""
    store = EventPlaneStore(str(tmp_path / "runtime"))
    plan_req = ExternalTaskRequest(session_id="s1", phase="plan", provider="external_model", target_ref="spec")
    exec_req = ExternalTaskRequest(session_id="s1", run_id="run_x", provider="external_model", target_ref="pr")
    store.append_request(plan_req)
    store.append_request(exec_req)

    by_session = store.list_requests_by_session("s1")
    by_ids = {r.request_id: r for r in by_session}
    assert by_ids[plan_req.request_id].run_id is None
    assert by_ids[exec_req.request_id].run_id == "run_x"


# ── out-of-order guard (review finding) ────────────────────────────────
#
# The helper ``append_mailbox_item`` / ``append_request`` restamps
# ``updated_at`` to ``now()`` on every write, so under the single-writer
# daemon the physical append order matches the logical order by
# construction.  The risk is crash-replay / concurrent-writer / clock-skew
# / manual-fixture scenarios where a record arrives physically late with
# an older ``updated_at`` — these tests write directly to the JSONL
# files to simulate that out-of-order landing.


def _write_record(path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    with open(path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(record) + "\n")


def test_latest_mailbox_item_ignores_stale_out_of_order_append(tmp_path):
    """A stale callback physically arriving after a newer update must
    not overwrite the newer logical state.  Review finding — fold by
    ``updated_at``, not by append order."""
    store = EventPlaneStore(str(tmp_path / "runtime"))
    mid = "mb_test"
    _write_record(store.session_mailbox_path, {
        "mailbox_item_id": mid, "session_id": "s1", "request_id": "r1",
        "source_kind": "external_review", "summary": "review completed",
        "payload": {}, "delivery_status": "acknowledged",
        "wake_decision": "", "run_id": None,
        "created_at": "2026-04-18T10:00:00+00:00",
        "updated_at": "2026-04-18T12:00:00+00:00",
    })
    # Stale callback: physically later, logically older.
    _write_record(store.session_mailbox_path, {
        "mailbox_item_id": mid, "session_id": "s1", "request_id": "r1",
        "source_kind": "external_review", "summary": "review pending (stale)",
        "payload": {}, "delivery_status": "new",
        "wake_decision": "", "run_id": None,
        "created_at": "2026-04-18T10:00:00+00:00",
        "updated_at": "2026-04-18T11:00:00+00:00",
    })
    latest = store.latest_mailbox_item(mid)
    assert latest is not None
    assert latest.delivery_status == "acknowledged"
    assert "stale" not in latest.summary


def test_list_mailbox_items_reflects_latest_logical_state(tmp_path):
    """``list_mailbox_items`` must return the logical latest per id,
    not whatever physically appeared last in the log."""
    store = EventPlaneStore(str(tmp_path / "runtime"))
    mid = "mb_list"
    _write_record(store.session_mailbox_path, {
        "mailbox_item_id": mid, "session_id": "s1", "request_id": "r1",
        "source_kind": "external_review", "summary": "",
        "payload": {}, "delivery_status": "acknowledged",
        "wake_decision": "", "run_id": None,
        "created_at": "2026-04-18T10:00:00+00:00",
        "updated_at": "2026-04-18T12:00:00+00:00",
    })
    _write_record(store.session_mailbox_path, {
        "mailbox_item_id": mid, "session_id": "s1", "request_id": "r1",
        "source_kind": "external_review", "summary": "",
        "payload": {}, "delivery_status": "new",
        "wake_decision": "", "run_id": None,
        "created_at": "2026-04-18T10:00:00+00:00",
        "updated_at": "2026-04-18T11:00:00+00:00",
    })
    new_items = store.list_mailbox_items("s1", delivery_status="new")
    acked_items = store.list_mailbox_items("s1", delivery_status="acknowledged")
    assert new_items == []
    assert len(acked_items) == 1


def test_latest_wait_ignores_stale_out_of_order_append(tmp_path):
    """A stale ``waiting`` record arriving after ``satisfied`` must not
    flip the wait back to open."""
    store = EventPlaneStore(str(tmp_path / "runtime"))
    wid = "wait_test"
    # Original waiting record.
    _write_record(store.session_waits_path, {
        "wait_id": wid, "session_id": "s1", "request_id": "r1",
        "wait_kind": "external_review", "status": "waiting",
        "resume_policy": "", "run_id": None,
        "entered_at": "2026-04-18T10:00:00+00:00",
        "resolved_at": "", "deadline_at": "",
    })
    # Resolved update.
    _write_record(store.session_waits_path, {
        "wait_id": wid, "session_id": "s1", "request_id": "r1",
        "wait_kind": "external_review", "status": "satisfied",
        "resume_policy": "", "run_id": None,
        "entered_at": "2026-04-18T10:00:00+00:00",
        "resolved_at": "2026-04-18T11:00:00+00:00",
        "deadline_at": "",
    })
    # Stale retry of the waiting record — physically newer but logically older.
    _write_record(store.session_waits_path, {
        "wait_id": wid, "session_id": "s1", "request_id": "r1",
        "wait_kind": "external_review", "status": "waiting",
        "resume_policy": "", "run_id": None,
        "entered_at": "2026-04-18T09:00:00+00:00",
        "resolved_at": "", "deadline_at": "",
    })
    latest = store.latest_wait(wid)
    assert latest is not None
    assert latest.status == "satisfied"
    open_waits = store.list_open_waits()
    assert not any(w.wait_id == wid for w in open_waits)


def test_latest_request_ignores_stale_out_of_order_append(tmp_path):
    """Request status must not regress when a stale update arrives late."""
    store = EventPlaneStore(str(tmp_path / "runtime"))
    rid = "req_test"
    _write_record(store.external_tasks_path, {
        "record_type": "request",
        "request_id": rid, "session_id": "s1", "run_id": None,
        "phase": "execute", "task_kind": "review",
        "provider": "external_model", "target_ref": "pr1",
        "blocking_policy": "notify_only", "status": "completed",
        "created_at": "2026-04-18T09:00:00+00:00",
        "updated_at": "2026-04-18T12:00:00+00:00",
    })
    _write_record(store.external_tasks_path, {
        "record_type": "request",
        "request_id": rid, "session_id": "s1", "run_id": None,
        "phase": "execute", "task_kind": "review",
        "provider": "external_model", "target_ref": "pr1",
        "blocking_policy": "notify_only", "status": "in_flight",
        "created_at": "2026-04-18T09:00:00+00:00",
        "updated_at": "2026-04-18T10:00:00+00:00",
    })
    latest = store.latest_request(rid)
    assert latest is not None
    assert latest.status == "completed"
