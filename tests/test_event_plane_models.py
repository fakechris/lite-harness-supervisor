"""Tests for event-plane dataclasses.

Covers the four v1 objects: ExternalTaskRequest, ExternalTaskResult,
SessionWait, SessionMailboxItem. The invariant under test from day 1:
run_id is Optional on all four so Task 7 (plan-phase) works without a
model refactor.
"""
from __future__ import annotations

from supervisor.event_plane.models import (
    ExternalTaskRequest,
    ExternalTaskResult,
    SessionMailboxItem,
    SessionWait,
)


def test_external_task_request_defaults_and_id():
    req = ExternalTaskRequest(
        session_id="session_abc",
        provider="external_model",
        target_ref="PR#7",
    )
    assert req.request_id.startswith("req_")
    assert req.session_id == "session_abc"
    assert req.run_id is None
    assert req.status == "pending"
    assert req.phase == "execute"
    assert req.task_kind == "review"
    assert req.created_at
    assert req.updated_at


def test_external_task_request_round_trip_without_run_id():
    req = ExternalTaskRequest(
        session_id="session_abc",
        phase="plan",
        task_kind="review",
        provider="external_agent",
        target_ref="spec:intro.md",
        blocking_policy="notify_only",
    )
    restored = ExternalTaskRequest.from_dict(req.to_dict())
    assert restored.run_id is None
    assert restored.phase == "plan"
    assert restored.request_id == req.request_id


def test_external_task_request_round_trip_with_run_id():
    req = ExternalTaskRequest(
        session_id="session_abc",
        run_id="run_xyz",
        provider="github",
        target_ref="PR#42",
    )
    restored = ExternalTaskRequest.from_dict(req.to_dict())
    assert restored.run_id == "run_xyz"


def test_external_task_result_defaults():
    res = ExternalTaskResult(
        request_id="req_1",
        session_id="session_1",
        provider="external_model",
        result_kind="review_comments",
        summary="nit: rename foo",
        payload={"comments": []},
    )
    assert res.result_id.startswith("res_")
    assert res.run_id is None
    assert res.occurred_at


def test_external_task_result_round_trip_preserves_payload():
    res = ExternalTaskResult(
        request_id="req_1",
        session_id="session_1",
        provider="github",
        result_kind="change_request",
        summary="fix login",
        payload={"comments": [{"file": "a.py", "line": 3}]},
    )
    restored = ExternalTaskResult.from_dict(res.to_dict())
    assert restored.payload == res.payload


def test_session_wait_includes_queryable_deadline():
    wait = SessionWait(
        session_id="session_1",
        request_id="req_1",
        wait_kind="external_review",
        deadline_at="2026-05-01T00:00:00+00:00",
    )
    assert wait.wait_id.startswith("wait_")
    assert wait.run_id is None
    assert wait.status == "waiting"
    assert wait.entered_at
    assert wait.deadline_at == "2026-05-01T00:00:00+00:00"


def test_session_mailbox_item_defaults():
    item = SessionMailboxItem(
        session_id="session_1",
        request_id="req_1",
        source_kind="external_review",
        summary="review returned",
        payload={"review_comments": []},
    )
    assert item.mailbox_item_id.startswith("mb_")
    assert item.run_id is None
    assert item.delivery_status == "new"
    assert item.wake_decision == ""


def test_session_mailbox_item_round_trip():
    item = SessionMailboxItem(
        session_id="session_1",
        run_id="run_abc",
        request_id="req_1",
        source_kind="external_review",
        summary="x",
        payload={"k": "v"},
        delivery_status="surfaced",
        wake_decision="notify_operator",
    )
    restored = SessionMailboxItem.from_dict(item.to_dict())
    assert restored.run_id == "run_abc"
    assert restored.delivery_status == "surfaced"
    assert restored.wake_decision == "notify_operator"
