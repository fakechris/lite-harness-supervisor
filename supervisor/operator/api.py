"""Operator read API — sync projections over existing runtime artifacts.

All functions here are *cheap reads*: no LLM calls, no network I/O.
They project RunSnapshot and RunTimelineEvent from state.json and
session_log.jsonl that already exist on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from supervisor.operator.models import RunSnapshot, RunTimelineEvent
from supervisor.pause_summary import (
    next_action,
    pause_reason,
    status_reason,
    is_waiting_for_review,
)


# ── helpers ─────────────────────────────────────────────────────────

def _last_checkpoint_summary(state: dict[str, Any]) -> str:
    cp = state.get("last_agent_checkpoint", {})
    return cp.get("summary", "") if isinstance(cp, dict) else ""


def _last_instruction_summary(state: dict[str, Any]) -> str:
    last_decision = state.get("last_decision", {})
    if isinstance(last_decision, dict):
        instr = last_decision.get("next_instruction", "")
        if instr:
            return instr[:200]
    return ""


def _updated_at(state: dict[str, Any], session_log_path: Path) -> str:
    """Best-effort timestamp: latest session event, or checkpoint timestamp."""
    if session_log_path.exists():
        try:
            lines = _tail_lines(session_log_path, max_lines=1)
            if lines:
                record = json.loads(lines[0])
                ts = record.get("timestamp", "")
                if ts:
                    return ts
        except (OSError, json.JSONDecodeError):
            pass
    cp = state.get("last_agent_checkpoint", {})
    if isinstance(cp, dict) and cp.get("timestamp"):
        return cp["timestamp"]
    return ""


def _tail_lines(path: Path, *, max_lines: int = 256, chunk_size: int = 4096) -> list[str]:
    """Read last N lines from a file efficiently."""
    import os
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        buffer = b""
        pos = size
        while pos > 0 and buffer.count(b"\n") <= max_lines:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            buffer = f.read(read_size) + buffer
        return buffer.decode("utf-8", errors="replace").splitlines()[-max_lines:]


# ── event summary ──────────────────────────────────────────────────

_EVENT_SUMMARIES: dict[str, Any] = {
    "checkpoint": lambda p: p.get("summary", ""),
    "gate_decision": lambda p: f"{p.get('decision', '')} — {p.get('reason', '')}",
    "injection": lambda p: f"instruction to {p.get('node_id', '')}",
    "injection_failed": lambda p: f"delivery failed: {p.get('reason', '')}",
    "injection_observation_only": lambda p: f"observation-only inject to {p.get('node_id', '')}",
    "human_pause": lambda p: p.get("pause_reason", p.get("reason", "")),
    "resume_requested": lambda p: f"resumed from {p.get('resumed_from', '')}",
    "verification": lambda p: "pass" if p.get("ok") else f"fail: {p.get('reason', '')}",
    "routing": lambda p: f"→ {p.get('target_type', '')} ({p.get('scope', '')})",
    "review_acknowledged": lambda p: f"by {p.get('reviewer', '')}",
    "auto_intervention": lambda p: p.get("reason", ""),
    "delivery_state_change": lambda p: f"{p.get('from', '')} → {p.get('to', '')}",
    "delivery_ack_timeout": lambda p: f"timeout after {p.get('elapsed_sec', '?')}s",
    "agent_idle_timeout": lambda p: f"idle for {p.get('idle_sec', '?')}s",
    "observation_delivery_stalled": lambda p: p.get("reason", "delivery stalled"),
    "orphaned_run_recovered": lambda _: "recovered after crash",
    "completed_after_review": lambda p: f"completed after review by {p.get('reviewer', '')}",
    "clarification_request": lambda p: f"Q: {p.get('question', '')[:80]}",
    "clarification_response": lambda p: f"A: {p.get('answer', '')[:80]}",
}


def _summarize_event(event_type: str, payload: dict[str, Any]) -> str:
    fn = _EVENT_SUMMARIES.get(event_type)
    if fn:
        try:
            return fn(payload)
        except (KeyError, TypeError):
            pass
    return event_type.replace("_", " ")


# ── timeline event writer ─────────────────────────────────────────


def append_timeline_event(
    session_log_path: Path,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Append a single event to session_log.jsonl.

    Lightweight writer for operator-originated events (clarification, etc.)
    that don't go through StateStore.
    """
    from datetime import datetime, timezone

    seq = _read_max_seq(session_log_path) + 1
    record = {
        "run_id": run_id,
        "seq": seq,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    session_log_path.parent.mkdir(parents=True, exist_ok=True)
    with session_log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_max_seq(session_log_path: Path) -> int:
    """Read the highest seq from the tail of session_log.jsonl."""
    if not session_log_path.exists():
        return 0
    try:
        lines = _tail_lines(session_log_path, max_lines=10)
    except OSError:
        return 0
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        seq = record.get("seq", 0)
        if isinstance(seq, int):
            return seq
    return 0


# ── public API ──────────────────────────────────────────────────────

def snapshot_from_state(state: dict[str, Any], session_log_path: Path) -> RunSnapshot:
    """Build a RunSnapshot from a raw state dict (state.json content)."""
    return RunSnapshot(
        run_id=state.get("run_id", ""),
        spec_id=state.get("spec_id", ""),
        worktree_root=state.get("workspace_root", ""),
        controller_mode=state.get("controller_mode", ""),
        surface_type=state.get("surface_type", ""),
        surface_target=state.get("pane_target", ""),
        top_state=state.get("top_state", "UNKNOWN"),
        current_node=state.get("current_node_id", ""),
        current_attempt=state.get("current_attempt", 0),
        done_nodes=state.get("done_node_ids", []),
        pause_reason=pause_reason(state),
        status_reason=status_reason(state),
        next_action=next_action(state),
        is_waiting_for_review=is_waiting_for_review(state),
        last_checkpoint_summary=_last_checkpoint_summary(state),
        last_instruction_summary=_last_instruction_summary(state),
        delivery_state=state.get("delivery_state", "IDLE"),
        updated_at=_updated_at(state, session_log_path),
    )


def timeline_from_session_log(
    session_log_path: Path,
    *,
    limit: int = 20,
    since_seq: int = 0,
) -> list[RunTimelineEvent]:
    """Read RunTimelineEvents from session_log.jsonl.

    Args:
        session_log_path: Path to session_log.jsonl.
        limit: Maximum events to return (most recent first).
        since_seq: Only return events with seq > since_seq.
    """
    if not session_log_path.exists():
        return []

    events: list[RunTimelineEvent] = []
    try:
        lines = _tail_lines(session_log_path, max_lines=max(limit * 2, 256))
    except OSError:
        return []

    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        seq = record.get("seq", 0)
        if seq <= since_seq:
            continue

        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        events.append(RunTimelineEvent(
            run_id=record.get("run_id", ""),
            seq=seq,
            event_type=record.get("event_type", ""),
            occurred_at=record.get("timestamp", ""),
            summary=_summarize_event(record.get("event_type", ""), payload),
            payload=payload,
        ))

    # Most recent first, capped at limit
    events.sort(key=lambda e: e.seq, reverse=True)
    return events[:limit]


def recent_exchange(
    state: dict[str, Any],
    session_log_path: Path,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Build a lightweight exchange view from recent events.

    Returns a dict with worker_text, supervisor_instruction, and
    checkpoint excerpts from the last few session events.
    This is the *cheap* version — no LLM summarization.
    """
    events = timeline_from_session_log(session_log_path, limit=limit)

    checkpoint_excerpt = ""
    instruction_excerpt = ""
    for ev in events:
        if ev.event_type == "checkpoint" and not checkpoint_excerpt:
            checkpoint_excerpt = ev.summary
        if ev.event_type == "injection" and not instruction_excerpt:
            instruction_excerpt = ev.summary

    return {
        "run_id": state.get("run_id", ""),
        "last_checkpoint_summary": _last_checkpoint_summary(state),
        "last_instruction_summary": _last_instruction_summary(state),
        "checkpoint_excerpt": checkpoint_excerpt,
        "instruction_excerpt": instruction_excerpt,
        "recent_event_count": len(events),
    }


# ── multi-run helpers ───────────────────────────────────────────────

def list_run_snapshots(runtime_root: Path) -> list[RunSnapshot]:
    """Scan all run directories under runtime_root/runs/ and return snapshots."""
    runs_dir = runtime_root / "runs"
    if not runs_dir.is_dir():
        return []

    snapshots: list[RunSnapshot] = []
    for run_dir in sorted(runs_dir.iterdir()):
        state_path = run_dir / "state.json"
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        session_log_path = run_dir / "session_log.jsonl"
        snapshots.append(snapshot_from_state(state, session_log_path))

    return snapshots
