"""Supervisor-issued external-review adapter (Task 5).

This adapter is the v1 return path that exercises the full
request -> wait -> result -> wake pipeline end-to-end without webhook
or auth friction. The concrete reviewer is pluggable: tests inject a
deterministic callable; production wires it to an LLM client.
"""
from __future__ import annotations

from typing import Callable, Optional

from .base import ResultDelivery, ReviewSource


Reviewer = Callable[[object], dict]
"""A reviewer takes an ExternalTaskRequest-shaped object and returns a dict
with keys ``result_kind``, ``summary``, and ``payload``."""


class ExternalReviewSource(ReviewSource):
    provider_name = "external_review"

    def __init__(self, reviewer: Reviewer):
        self._reviewer = reviewer
        # Cache keeps produce_result idempotent so ingest dedupe works and
        # the reviewer is not invoked twice for the same request.
        self._cache: dict[str, ResultDelivery] = {}

    def produce_result(self, *, request) -> Optional[ResultDelivery]:
        request_id = getattr(request, "request_id", "")
        if not request_id:
            return None
        cached = self._cache.get(request_id)
        if cached is not None:
            return cached

        review = self._reviewer(request) or {}
        delivery = ResultDelivery(
            request_id=request_id,
            result_kind=review.get("result_kind", "review_comments"),
            summary=review.get("summary", ""),
            payload=dict(review.get("payload", {})),
            idempotency_key=f"external_review:{request_id}:1",
        )
        self._cache[request_id] = delivery
        return delivery
