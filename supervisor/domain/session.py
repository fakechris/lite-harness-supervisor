"""SessionRun — the central first-class object.

SessionRun = identity + mutable state snapshot + read-only history view.
It is the single authority for "what is this run, where is it, what happened."
"""
from __future__ import annotations

import json
from typing import Any

from supervisor.domain.models import SupervisorState, Checkpoint, SupervisorDecision, HandoffInstruction
from supervisor.domain.state_machine import FINAL_STATES


class SessionRun:
    """Wraps SupervisorState with history access and identity queries."""

    def __init__(self, state: SupervisorState, store):
        self.state = state
        self._store = store

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
                if record.get("seq", 0) > seq:
                    events.append(record)
            except json.JSONDecodeError:
                continue
        return events
