"""Tests for EventPlaneIngest — session-first request/result/mailbox ingest.

Ingest sits over EventPlaneStore and enforces the correlation rules the
PRD calls out:

- a request persists as ExternalTaskRequest + an associated SessionWait
- a result persists as ExternalTaskResult, resolves its SessionWait, and
  creates a SessionMailboxItem with delivery_status="new" (wake policy is
  a later task and must not run on this path)
- results for unknown request_ids are rejected
- duplicate result delivery (same idempotency_key) is deduped — no second
  mailbox item, no double wait-resolution
- run_id is optional on every object (Task 2 invariant)
"""
from __future__ import annotations

from supervisor.event_plane.ingest import EventPlaneIngest
from supervisor.event_plane.store import EventPlaneStore


def _make_ingest(tmp_path):
    store = EventPlaneStore(str(tmp_path / "runtime"))
    ingest = EventPlaneIngest(store)
    return ingest, store


def test_register_request_creates_request_and_wait(tmp_path):
    ingest, store = _make_ingest(tmp_path)
    resp = ingest.register_request(
        session_id="s1",
        run_id="run_1",
        provider="external_model",
        target_ref="PR#1",
        task_kind="review",
        blocking_policy="notify_only",
    )
    assert resp["ok"] is True
    request_id = resp["request_id"]
    wait_id = resp["wait_id"]

    req = store.latest_request(request_id)
    assert req is not None
    assert req.session_id == "s1"
    assert req.run_id == "run_1"
    assert req.status == "pending"

    wait = store.latest_wait(wait_id)
    assert wait is not None
    assert wait.request_id == request_id
    assert wait.status == "waiting"
    assert wait.session_id == "s1"


def test_register_request_allows_no_run_id(tmp_path):
    """Task 2 invariant: plan-phase requests have no run_id."""
    ingest, store = _make_ingest(tmp_path)
    resp = ingest.register_request(
        session_id="s_plan",
        provider="external_model",
        target_ref="spec:intro.md",
        phase="plan",
    )
    assert resp["ok"] is True
    req = store.latest_request(resp["request_id"])
    assert req is not None
    assert req.run_id is None
    assert req.phase == "plan"


def test_ingest_result_resolves_wait_and_creates_mailbox_item(tmp_path):
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s1",
        run_id="run_1",
        provider="external_model",
        target_ref="PR#1",
    )
    request_id = reg["request_id"]
    wait_id = reg["wait_id"]

    resp = ingest.ingest_result(
        request_id=request_id,
        provider="external_model",
        result_kind="review_comments",
        summary="nit",
        payload={"comments": []},
    )
    assert resp["ok"] is True
    assert resp["mailbox_item_id"]

    wait = store.latest_wait(wait_id)
    assert wait is not None
    assert wait.status == "satisfied"
    assert wait.resolved_at

    # The request should transition to completed so operator UX doesn't
    # show a result-already-in-hand request as still pending.
    req = store.latest_request(request_id)
    assert req is not None
    assert req.status == "completed"

    items = store.list_mailbox_items(session_id="s1")
    assert len(items) == 1
    assert items[0].delivery_status == "new"
    assert items[0].wake_decision == ""  # Task 4 will populate this
    assert items[0].source_kind == "external_review"


def test_ingest_result_rejects_unknown_request(tmp_path):
    ingest, _ = _make_ingest(tmp_path)
    resp = ingest.ingest_result(
        request_id="req_does_not_exist",
        provider="external_model",
        result_kind="review_comments",
    )
    assert resp["ok"] is False
    assert "unknown" in resp.get("error", "").lower()


def test_ingest_result_is_idempotent_on_duplicate_key(tmp_path):
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s1",
        run_id="run_1",
        provider="external_model",
        target_ref="PR#1",
    )
    request_id = reg["request_id"]

    first = ingest.ingest_result(
        request_id=request_id,
        provider="external_model",
        result_kind="review_comments",
        summary="v1",
        idempotency_key="provider_evt_42",
    )
    second = ingest.ingest_result(
        request_id=request_id,
        provider="external_model",
        result_kind="review_comments",
        summary="v1",
        idempotency_key="provider_evt_42",
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second.get("deduped") is True
    assert second["result_id"] == first["result_id"]
    assert second["mailbox_item_id"] == first["mailbox_item_id"]

    # Exactly one mailbox item and one result.
    items = store.list_mailbox_items(session_id="s1")
    assert len(items) == 1
    results = store.list_results_for_request(request_id)
    assert len(results) == 1


def test_ingest_result_inherits_run_id_from_request_when_absent(tmp_path):
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s1",
        run_id="run_active",
        provider="external_model",
        target_ref="PR#1",
    )
    ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="review_comments",
    )
    items = store.list_mailbox_items(session_id="s1")
    assert items[0].run_id == "run_active"


def test_ingest_result_tolerates_missing_run_id(tmp_path):
    """Plan-phase or session-orphaned result: run_id may be None on both sides."""
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s_plan",
        provider="external_model",
        target_ref="spec:intro.md",
        phase="plan",
    )
    resp = ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="analysis",
    )
    assert resp["ok"] is True
    items = store.list_mailbox_items(session_id="s_plan")
    assert len(items) == 1
    assert items[0].run_id is None


def test_list_mailbox_returns_new_items(tmp_path):
    ingest, _ = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s1",
        run_id="run_1",
        provider="external_model",
        target_ref="PR#1",
    )
    ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="review_comments",
    )
    listing = ingest.list_mailbox(session_id="s1")
    assert listing["ok"] is True
    assert len(listing["items"]) == 1
    assert listing["items"][0]["delivery_status"] == "new"


def test_ack_mailbox_item_transitions_delivery_status(tmp_path):
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s1",
        run_id="run_1",
        provider="external_model",
        target_ref="PR#1",
    )
    result_resp = ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="review_comments",
    )
    mid = result_resp["mailbox_item_id"]

    ack = ingest.ack_mailbox_item(mailbox_item_id=mid)
    assert ack["ok"] is True

    item = store.latest_mailbox_item(mid)
    assert item is not None
    assert item.delivery_status == "acknowledged"


def test_ack_mailbox_item_rejects_unknown_id(tmp_path):
    ingest, _ = _make_ingest(tmp_path)
    resp = ingest.ack_mailbox_item(mailbox_item_id="mb_bogus")
    assert resp["ok"] is False


# ─── Task 7: plan-phase correlation (no run_id at request time) ──────

def test_phase_plan_request_correlates_by_session_id_only(tmp_path):
    """A `phase=plan` request with no run_id correlates via session_id end-to-end.

    Covers PRD Rule 6 "session-scoped at return time": the object model must
    work for plan-stage review where no run exists yet.
    """
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s_plan",
        provider="external_model",
        target_ref="spec:intro.md",
        phase="plan",
        task_kind="review",
        blocking_policy="notify_only",
    )
    assert reg["ok"] is True
    assert reg["session_id"] == "s_plan"

    req = store.latest_request(reg["request_id"])
    assert req is not None
    assert req.phase == "plan"
    assert req.run_id is None

    wait = store.latest_wait(reg["wait_id"])
    assert wait is not None
    assert wait.session_id == "s_plan"
    assert wait.run_id is None
    assert wait.status == "waiting"

    # Results returning later must still correlate by session_id alone.
    resp = ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="analysis",
        summary="plan looks good",
    )
    assert resp["ok"] is True

    items = store.list_mailbox_items("s_plan")
    assert len(items) == 1
    assert items[0].run_id is None
    assert items[0].session_id == "s_plan"

    # The wait resolves to satisfied; associated request completes.
    resolved_wait = store.latest_wait(reg["wait_id"])
    assert resolved_wait.status == "satisfied"
    completed_req = store.latest_request(reg["request_id"])
    assert completed_req.status == "completed"


def test_ingest_result_rejects_unknown_result_kind(tmp_path):
    """result_kind must be one of the allowed values; an LLM or external
    reviewer that ships a free-form kind must not silently corrupt the
    mailbox taxonomy."""
    ingest, _ = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s1",
        provider="external_model",
        target_ref="PR#1",
    )
    resp = ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="made_up_kind",
        payload={},
    )
    assert resp["ok"] is False
    assert "result_kind" in resp["error"]


def test_ingest_result_rejects_provider_mismatch(tmp_path):
    """A result reporting a different provider than the request issued
    against must be rejected — otherwise cross-source contamination
    (e.g. an external_model callback resolving a github wait) is possible."""
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s1",
        provider="github",
        target_ref="PR#1",
    )
    resp = ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="review_comments",
    )
    assert resp["ok"] is False
    assert "provider" in resp["error"]

    # Request must still be pending; wait still waiting; no mailbox item.
    req = store.latest_request(reg["request_id"])
    assert req.status == "pending"
    wait = store.latest_wait(reg["wait_id"])
    assert wait.status == "waiting"
    assert store.list_mailbox_items("s1") == []


def test_ingest_result_rejects_non_mapping_payload(tmp_path):
    """Non-dict payloads (e.g. JSON list over IPC) must be rejected cleanly,
    not crash the ingest handler."""
    ingest, _ = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s_bad",
        provider="external_model",
        target_ref="PR#1",
    )
    resp = ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_model",
        result_kind="review_comments",
        payload=["not", "a", "dict"],  # type: ignore[arg-type]
    )
    assert resp["ok"] is False
    assert "payload" in resp["error"]


def test_phase_plan_result_records_without_active_run_on_ingest_side(tmp_path):
    """Result ingest must not require run_id even when the request never had one."""
    ingest, store = _make_ingest(tmp_path)
    reg = ingest.register_request(
        session_id="s_plan2",
        provider="external_agent",
        target_ref="spec:api.yaml",
        phase="plan",
    )
    resp = ingest.ingest_result(
        request_id=reg["request_id"],
        provider="external_agent",
        result_kind="analysis",
        # run_id not supplied; request also has none.
    )
    assert resp["ok"] is True

    listing = ingest.list_mailbox(session_id="s_plan2")
    assert listing["ok"] is True
    assert len(listing["items"]) == 1
    assert listing["items"][0]["run_id"] is None
