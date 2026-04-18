"""Review-source adapter base contract.

Adapters map an ``ExternalTaskRequest`` (issued via the event plane) to a
``ResultDelivery`` (normalized result). The daemon owns calling
``ingest_result`` with the delivery's fields — adapters never write to
the store directly and never mutate run state.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ResultDelivery:
    """Normalized adapter-produced result ready for event-plane ingest."""
    request_id: str
    result_kind: str                     # review_comments | approval | change_request | ...
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""


class ReviewSource(ABC):
    """Abstract base for a deferred-review source driver.

    Concrete adapters must not import or reference run-state mutation
    surfaces. They are pure transformers: request -> (optional) delivery.
    """
    provider_name: str = ""

    @abstractmethod
    def produce_result(self, *, request) -> Optional[ResultDelivery]:
        """Return a ResultDelivery for *request* once one is available.

        Returning None means "not ready yet" — the daemon will poll again
        on the next reconcile tick. Implementations must be idempotent:
        repeated calls for the same request must produce the same
        delivery (same idempotency_key) so ingest's dedup path works.
        """
        raise NotImplementedError
