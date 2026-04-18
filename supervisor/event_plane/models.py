"""Event-plane domain objects.

Four v1 dataclasses that form the session-first deferred-work substrate:
ExternalTaskRequest, ExternalTaskResult, SessionWait, SessionMailboxItem.

Key invariant (Task 2): run_id is Optional[str] on every object. External
task requests and results correlate to session_id first; run_id is
recorded when known but may be absent (e.g., plan-phase requests with no
active run, or results arriving after the originating run ended).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExternalTaskRequest:
    """A request asking another system to do deferred work."""
    request_id: str = ""
    session_id: str = ""
    run_id: Optional[str] = None                        # None iff no active run at request time
    phase: str = "execute"                              # execute | post_implement | finish | plan
    task_kind: str = "review"                           # review | ci_wait | approval_wait | consultation
    provider: str = ""                                  # github | external_model | external_agent | future
    target_ref: str = ""
    blocking_policy: str = "notify_only"                # block_session | notify_only | advisory_only
    status: str = "pending"                             # pending | in_flight | completed | failed | expired
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.request_id:
            self.request_id = f"req_{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExternalTaskRequest":
        return cls(
            request_id=data.get("request_id", ""),
            session_id=data.get("session_id", ""),
            run_id=data.get("run_id"),
            phase=data.get("phase", "execute"),
            task_kind=data.get("task_kind", "review"),
            provider=data.get("provider", ""),
            target_ref=data.get("target_ref", ""),
            blocking_policy=data.get("blocking_policy", "notify_only"),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class ExternalTaskResult:
    """Normalized result returning later from an external system."""
    result_id: str = ""
    request_id: str = ""
    session_id: str = ""
    run_id: Optional[str] = None
    provider: str = ""
    result_kind: str = ""                               # review_comments | approval | change_request | ci_failure | ci_success | analysis
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = ""

    def __post_init__(self):
        if not self.result_id:
            self.result_id = f"res_{uuid.uuid4().hex[:12]}"
        if not self.occurred_at:
            self.occurred_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExternalTaskResult":
        return cls(
            result_id=data.get("result_id", ""),
            request_id=data.get("request_id", ""),
            session_id=data.get("session_id", ""),
            run_id=data.get("run_id"),
            provider=data.get("provider", ""),
            result_kind=data.get("result_kind", ""),
            summary=data.get("summary", ""),
            payload=dict(data.get("payload", {})),
            occurred_at=data.get("occurred_at", ""),
        )


@dataclass
class SessionWait:
    """Durable record that a session is waiting for an external result.

    ``deadline_at`` is queryable so the daemon expiry sweep (see PRD
    Expiry Lifecycle) can find past-deadline waits efficiently.
    """
    wait_id: str = ""
    session_id: str = ""
    run_id: Optional[str] = None
    request_id: str = ""
    wait_kind: str = ""                                 # external_review | ci | approval
    status: str = "waiting"                             # waiting | satisfied | expired | cancelled
    resume_policy: str = ""
    entered_at: str = ""
    resolved_at: str = ""
    deadline_at: str = ""                               # ISO8601; blank = no deadline

    def __post_init__(self):
        if not self.wait_id:
            self.wait_id = f"wait_{uuid.uuid4().hex[:12]}"
        if not self.entered_at:
            self.entered_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionWait":
        return cls(
            wait_id=data.get("wait_id", ""),
            session_id=data.get("session_id", ""),
            run_id=data.get("run_id"),
            request_id=data.get("request_id", ""),
            wait_kind=data.get("wait_kind", ""),
            status=data.get("status", "waiting"),
            resume_policy=data.get("resume_policy", ""),
            entered_at=data.get("entered_at", ""),
            resolved_at=data.get("resolved_at", ""),
            deadline_at=data.get("deadline_at", ""),
        )


@dataclass
class SessionMailboxItem:
    """Durable item representing deferred work that has arrived for a session."""
    mailbox_item_id: str = ""
    session_id: str = ""
    run_id: Optional[str] = None
    request_id: str = ""
    source_kind: str = ""                               # external_review | operator_note | future_external_event
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    delivery_status: str = "new"                        # new | surfaced | acknowledged | consumed
    wake_decision: str = ""                             # notify_operator | wake_worker | defer | record_only
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.mailbox_item_id:
            self.mailbox_item_id = f"mb_{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionMailboxItem":
        return cls(
            mailbox_item_id=data.get("mailbox_item_id", ""),
            session_id=data.get("session_id", ""),
            run_id=data.get("run_id"),
            request_id=data.get("request_id", ""),
            source_kind=data.get("source_kind", ""),
            summary=data.get("summary", ""),
            payload=dict(data.get("payload", {})),
            delivery_status=data.get("delivery_status", "new"),
            wake_decision=data.get("wake_decision", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
