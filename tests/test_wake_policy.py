"""Tests for the event-plane wake policy.

The wake policy turns a just-landed mailbox item into one of four decisions:
notify_operator, wake_worker, defer, record_only. Rule 4 of the PRD says
these decisions are daemon-owned, not sidecar-loop-owned, and that source
drivers must never call terminal.inject() directly — so this module is the
only legitimate place the decision is made. Tests here are pure and do
not touch the daemon socket.
"""
from __future__ import annotations

from supervisor.domain.enums import TopState
from supervisor.event_plane.models import (
    ExternalTaskRequest,
    SessionMailboxItem,
)
from supervisor.event_plane.wake_policy import evaluate, WakeDecision


def _request(blocking_policy: str = "notify_only", task_kind: str = "review") -> ExternalTaskRequest:
    return ExternalTaskRequest(
        session_id="s1",
        run_id="run_1",
        provider="external_model",
        target_ref="PR#1",
        task_kind=task_kind,
        blocking_policy=blocking_policy,
    )


def _mailbox_item() -> SessionMailboxItem:
    return SessionMailboxItem(
        session_id="s1",
        run_id="run_1",
        request_id="req_x",
        source_kind="external_review",
        summary="nit",
        payload={},
    )


def test_advisory_only_maps_to_record_only():
    decision = evaluate(
        request=_request(blocking_policy="advisory_only"),
        mailbox_item=_mailbox_item(),
        run_state={"top_state": TopState.PAUSED_FOR_HUMAN.value},
    )
    assert isinstance(decision, WakeDecision)
    assert decision.decision == "record_only"


def test_notify_only_maps_to_notify_operator_regardless_of_run_state():
    decision = evaluate(
        request=_request(blocking_policy="notify_only"),
        mailbox_item=_mailbox_item(),
        run_state={"top_state": TopState.PAUSED_FOR_HUMAN.value},
    )
    assert decision.decision == "notify_operator"


def test_block_session_on_paused_run_maps_to_wake_worker():
    decision = evaluate(
        request=_request(blocking_policy="block_session"),
        mailbox_item=_mailbox_item(),
        run_state={"top_state": TopState.PAUSED_FOR_HUMAN.value},
    )
    assert decision.decision == "wake_worker"


def test_block_session_on_busy_run_maps_to_defer():
    decision = evaluate(
        request=_request(blocking_policy="block_session"),
        mailbox_item=_mailbox_item(),
        run_state={"top_state": TopState.RUNNING.value},
    )
    assert decision.decision == "defer"


def test_block_session_on_terminal_run_maps_to_notify_operator():
    decision = evaluate(
        request=_request(blocking_policy="block_session"),
        mailbox_item=_mailbox_item(),
        run_state={"top_state": TopState.COMPLETED.value},
    )
    assert decision.decision == "notify_operator"


def test_block_session_without_run_state_maps_to_notify_operator():
    """Plan-phase or post-run arrival: operator must decide."""
    decision = evaluate(
        request=_request(blocking_policy="block_session"),
        mailbox_item=_mailbox_item(),
        run_state=None,
    )
    assert decision.decision == "notify_operator"


def test_decision_carries_reason_for_audit():
    decision = evaluate(
        request=_request(blocking_policy="block_session"),
        mailbox_item=_mailbox_item(),
        run_state={"top_state": TopState.RUNNING.value},
    )
    assert decision.reason  # non-empty
