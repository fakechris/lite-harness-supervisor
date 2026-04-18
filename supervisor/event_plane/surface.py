"""Passive surface over the event-plane store for observer/status output.

Rule 4 + Task 4 of the PRD: the sidecar loop must not scan the mailbox or
act on wake decisions. It may only *read* event-plane state to render
accurate status ("waiting on review", "review landed, operator must ack",
etc.). This module is the single read-path the daemon uses when building
observer responses — keeping the read side isolated makes it impossible
for loop.py to accidentally take a control action through it.
"""
from __future__ import annotations

from .store import EventPlaneStore


def summarize_for_session(store: EventPlaneStore, session_id: str) -> dict:
    """Return a passive event-plane snapshot for *session_id*.

    Counts are folded from the append-only logs via the store's public
    helpers — no I/O beyond what the store already performs.

    The ``latest_mailbox_item_id`` / ``latest_wake_decision`` fields are
    derived from the newest mailbox item across *all* delivery_statuses
    so the operator overview can answer "what happened most recently?"
    without duplicating the fold in every caller.
    """
    empty = {
        "waits_open": 0,
        "mailbox_new": 0,
        "mailbox_acknowledged": 0,
        "requests_total": 0,
        "latest_mailbox_item_id": "",
        "latest_wake_decision": "",
    }
    if not session_id:
        return empty

    requests = store.list_requests_by_session(session_id)
    open_waits = [
        w for w in store.list_open_waits()
        if w.session_id == session_id
    ]
    new_mailbox = store.list_mailbox_items(session_id, delivery_status="new")
    acked_mailbox = store.list_mailbox_items(session_id, delivery_status="acknowledged")
    all_mailbox = store.list_mailbox_items(session_id)

    latest_item_id = ""
    latest_wake = ""
    if all_mailbox:
        latest = max(all_mailbox, key=lambda m: m.created_at or "")
        latest_item_id = latest.mailbox_item_id
        latest_wake = latest.wake_decision

    return {
        "waits_open": len(open_waits),
        "mailbox_new": len(new_mailbox),
        "mailbox_acknowledged": len(acked_mailbox),
        "requests_total": len(requests),
        "latest_mailbox_item_id": latest_item_id,
        "latest_wake_decision": latest_wake,
    }
