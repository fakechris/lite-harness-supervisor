"""Daemon-owned wake policy for event-plane mailbox items.

Turns a freshly-created ``SessionMailboxItem`` plus the originating
``ExternalTaskRequest`` plus the current run state into one of four
decisions:

- ``notify_operator`` — surface a notification and wait for operator action.
- ``wake_worker``   — daemon-initiated resume of a paused run.
- ``defer``         — hold the item; try again on the next decision point.
- ``record_only``   — persist only; nothing downstream acts on it.

Rule 4 (PRD): the sidecar loop is passive with respect to this. Source
drivers must never call ``terminal.inject()``. This module is the single
legitimate home for the decision; the daemon action handler is the only
caller in v1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from supervisor.domain.enums import TopState


_BUSY_STATES = {
    TopState.RUNNING,
    TopState.VERIFYING,
    TopState.GATING,
    TopState.ATTACHED,
}
_RESUMABLE_STATES = {
    TopState.PAUSED_FOR_HUMAN,
    TopState.RECOVERY_NEEDED,
}
_TERMINAL_STATES = {
    TopState.COMPLETED,
    TopState.FAILED,
    TopState.ABORTED,
}


@dataclass
class WakeDecision:
    decision: str
    reason: str = ""


def _coerce_top_state(value) -> Optional[TopState]:
    if isinstance(value, TopState):
        return value
    if isinstance(value, str) and value:
        try:
            return TopState(value)
        except ValueError:
            return None
    return None


def evaluate(
    *,
    request,  # ExternalTaskRequest
    mailbox_item,  # SessionMailboxItem (unused today; passed for future rules)
    run_state: Optional[dict],
) -> WakeDecision:
    """Decide what to do with a just-ingested mailbox item.

    ``run_state`` is a minimal dict-shaped view of the attached run (e.g.
    ``{"top_state": "RUNNING"}``) or ``None`` if no run is attached (plan
    phase or post-run arrival).
    """
    del mailbox_item  # reserved for future rules (e.g., summary-based routing)

    blocking_policy = getattr(request, "blocking_policy", "notify_only")

    if blocking_policy == "advisory_only":
        return WakeDecision("record_only", "advisory_only blocking policy")

    if blocking_policy == "notify_only":
        return WakeDecision("notify_operator", "notify_only blocking policy")

    # block_session: try to wake a paused run; otherwise notify/defer.
    if run_state is None:
        return WakeDecision("notify_operator", "block_session with no attached run")

    top_state = _coerce_top_state(run_state.get("top_state"))
    if top_state is None:
        return WakeDecision("notify_operator", "block_session with unknown top_state")
    if top_state in _TERMINAL_STATES:
        return WakeDecision("notify_operator", f"block_session but run is {top_state.value}")
    if top_state in _BUSY_STATES:
        return WakeDecision("defer", f"block_session but run is {top_state.value}")
    if top_state in _RESUMABLE_STATES:
        return WakeDecision("wake_worker", f"block_session and run is {top_state.value}")
    return WakeDecision("notify_operator", f"block_session with unhandled state {top_state.value}")
