"""Tests for review-source adapters (Task 5).

v1 ships exactly one concrete source: supervisor-issued external review.
The base contract is designed so a GitHub adapter (Task 5b) can be built
against it without any base-class changes.

Source drivers must never touch run state or terminal.inject() — they
only emit normalized result records that flow through the daemon's
event-plane ingest.
"""
from __future__ import annotations

from typing import Optional

from supervisor.event_plane.models import ExternalTaskRequest
from supervisor.event_plane.store import EventPlaneStore
from supervisor.event_plane.ingest import EventPlaneIngest
from supervisor.review_sources.base import ResultDelivery, ReviewSource
from supervisor.review_sources.external_review import ExternalReviewSource


def test_external_review_source_produces_result_for_request(tmp_path):
    called_with: list[ExternalTaskRequest] = []

    def reviewer(req: ExternalTaskRequest) -> dict:
        called_with.append(req)
        return {
            "result_kind": "review_comments",
            "summary": "LGTM with one nit",
            "payload": {"comments": [{"file": "x.py", "line": 3, "note": "rename"}]},
        }

    source = ExternalReviewSource(reviewer=reviewer)
    request = ExternalTaskRequest(
        session_id="s1",
        run_id="run_1",
        provider=source.provider_name,
        target_ref="PR#1",
    )

    delivery = source.produce_result(request=request)
    assert isinstance(delivery, ResultDelivery)
    assert delivery.request_id == request.request_id
    assert delivery.result_kind == "review_comments"
    assert delivery.summary == "LGTM with one nit"
    assert delivery.payload["comments"]
    assert delivery.idempotency_key  # non-empty

    # Reviewer invoked exactly once.
    assert len(called_with) == 1


def test_external_review_source_is_idempotent_on_repeat_produce(tmp_path):
    """Repeated produce_result calls for the same request return cached delivery."""
    calls = 0

    def reviewer(req: ExternalTaskRequest) -> dict:
        nonlocal calls
        calls += 1
        return {"result_kind": "review_comments", "summary": "ok", "payload": {}}

    source = ExternalReviewSource(reviewer=reviewer)
    request = ExternalTaskRequest(
        session_id="s1", run_id="run_1",
        provider=source.provider_name, target_ref="PR#1",
    )
    first = source.produce_result(request=request)
    second = source.produce_result(request=request)
    assert calls == 1  # reviewer called only once
    assert first.idempotency_key == second.idempotency_key


def test_external_review_source_flows_through_canonical_ingest(tmp_path):
    """End-to-end: adapter result goes through ingest.ingest_result and lands
    as a mailbox item exactly once (even if drained twice)."""
    store = EventPlaneStore(str(tmp_path / "runtime"))
    ingest = EventPlaneIngest(store)

    reg = ingest.register_request(
        session_id="s1",
        run_id="run_1",
        provider="external_review",
        target_ref="PR#1",
    )
    request = store.latest_request(reg["request_id"])

    def reviewer(_req):
        return {"result_kind": "review_comments", "summary": "nit", "payload": {}}

    source = ExternalReviewSource(reviewer=reviewer)

    # First drain: produce + ingest.
    delivery = source.produce_result(request=request)
    first = ingest.ingest_result(
        request_id=delivery.request_id,
        provider=source.provider_name,
        result_kind=delivery.result_kind,
        summary=delivery.summary,
        payload=delivery.payload,
        idempotency_key=delivery.idempotency_key,
    )
    # Second drain: adapter returns same delivery (cached), ingest dedupes.
    delivery_again = source.produce_result(request=request)
    second = ingest.ingest_result(
        request_id=delivery_again.request_id,
        provider=source.provider_name,
        result_kind=delivery_again.result_kind,
        summary=delivery_again.summary,
        payload=delivery_again.payload,
        idempotency_key=delivery_again.idempotency_key,
    )
    assert first["ok"] and second["ok"]
    assert second.get("deduped") is True

    items = store.list_mailbox_items(session_id="s1")
    assert len(items) == 1


def test_base_contract_accepts_github_shaped_adapter():
    """Structural: a GitHub-style adapter must fit the base class without
    changes. This is a design guardrail for Task 5b."""

    class _FakeGithubAdapter(ReviewSource):
        provider_name = "github"

        def produce_result(self, *, request: ExternalTaskRequest) -> Optional[ResultDelivery]:
            # A real GitHub adapter would hit the API here; this stub just
            # proves the signature fits.
            return ResultDelivery(
                request_id=request.request_id,
                result_kind="change_request",
                summary="please address review comments",
                payload={"review_id": "gh_12345", "comments": []},
                idempotency_key=f"gh:{request.request_id}:1",
            )

    adapter = _FakeGithubAdapter()
    req = ExternalTaskRequest(
        session_id="s1", run_id="run_1",
        provider="github", target_ref="PR#42",
    )
    delivery = adapter.produce_result(request=req)
    assert delivery is not None
    assert delivery.payload["review_id"] == "gh_12345"


def test_source_driver_does_not_import_terminal():
    """Rule 4: source drivers must never touch terminal.inject().

    Enforced structurally by asserting the shipped adapter has no
    imports or attribute accesses into the supervisor run-control
    surface. A reader who later adds ``from supervisor import terminal``
    or calls ``terminal.inject(...)`` will fail this test.
    """
    import re

    import supervisor.review_sources.external_review as mod
    import supervisor.review_sources.base as base_mod

    forbidden_patterns = [
        re.compile(r"\bfrom\s+supervisor\.terminal\b"),
        re.compile(r"\bimport\s+supervisor\.terminal\b"),
        re.compile(r"\bterminal\.inject\b"),
        re.compile(r"\bSupervisorLoop\b"),
        re.compile(r"\bstate_machine\b"),
    ]
    for loaded in (mod, base_mod):
        p = loaded.__file__
        assert p, "module file path unexpectedly empty"
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
        for pattern in forbidden_patterns:
            assert not pattern.search(text), (
                f"{p} contains forbidden run-control reference: {pattern.pattern}"
            )
