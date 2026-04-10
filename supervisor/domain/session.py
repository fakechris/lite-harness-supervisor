"""SessionRun — the central first-class object.

SessionRun = identity + mutable state snapshot + read-only history view.
It is the single authority for "what is this run, where is it, what happened."
"""
from __future__ import annotations

import json
from typing import Any

from supervisor.domain.models import (
    SupervisorState, Checkpoint, SupervisorDecision, HandoffInstruction,
    AcceptanceContract, WorkerProfile, SupervisionPolicy,
)
from supervisor.domain.state_machine import FINAL_STATES


class SessionRun:
    """Wraps SupervisorState with history access, identity, and policy queries."""

    def __init__(self, state: SupervisorState, store, *,
                 acceptance: AcceptanceContract | None = None,
                 worker: WorkerProfile | None = None,
                 policy: SupervisionPolicy | None = None):
        self.state = state
        self._store = store
        self._acceptance = acceptance
        self._worker = worker or WorkerProfile()
        self._policy = policy

    @property
    def run_id(self) -> str:
        return self.state.run_id

    @property
    def spec_id(self) -> str:
        return self.state.spec_id

    @property
    def is_active(self) -> bool:
        return self.state.top_state not in FINAL_STATES

    @property
    def is_paused(self) -> bool:
        from supervisor.domain.enums import TopState
        return self.state.top_state == TopState.PAUSED_FOR_HUMAN

    @property
    def is_completed(self) -> bool:
        from supervisor.domain.enums import TopState
        return self.state.top_state == TopState.COMPLETED

    @property
    def acceptance_contract(self) -> AcceptanceContract | None:
        return self._acceptance

    @property
    def worker_profile(self) -> WorkerProfile:
        return self._worker

    @property
    def supervision_policy(self) -> SupervisionPolicy | None:
        return self._policy

    @property
    def routing_history(self) -> list[dict]:
        """Read routing decisions from session log."""
        return [
            e.get("payload", {})
            for e in self.events_since(0)
            if e.get("event_type") == "routing"
        ]

    def save(self) -> None:
        self._store.save(self.state)

    def append_event(self, event: dict) -> None:
        self._store.append_event(event)

    def append_decision(self, decision: SupervisorDecision) -> None:
        self._store.append_decision(decision.to_dict())

    def append_session_event(self, event_type: str, payload: dict) -> None:
        self._store.append_session_event(self.run_id, event_type, payload)

    def events_since(self, seq: int) -> list[dict]:
        """Read session events from the durable log since *seq*."""
        if not self._store.session_log_path.exists():
            return []
        events = []
        for line in self._store.session_log_path.read_text().strip().splitlines():
            try:
                record = json.loads(line)
                if record.get("run_id") == self.run_id and record.get("seq", 0) > seq:
                    events.append(record)
            except json.JSONDecodeError:
                continue
        return events
