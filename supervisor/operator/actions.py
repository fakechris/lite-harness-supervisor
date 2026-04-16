"""Unified operator actions — shared by CLI, TUI, and future IM channels.

Each function takes a RunContext, checks capabilities, and executes via
the appropriate mode (daemon, local, auto-start).  Expensive operations
(explain, drift, exchange-explain) are always async — even for local runs,
they use a background-thread JobTracker so the caller never blocks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from supervisor.operator.jobs import JobTracker
from supervisor.operator.run_context import (
    ActionMode,
    ActionUnavailable,
    RunContext,
)

# Module-level local job tracker for socketless async ops
_local_jobs = JobTracker(max_completed=20)


@dataclass
class OperatorJob:
    """Handle returned by async submit_* functions."""
    job_id: str
    source: str  # "daemon" | "local"


# ── sync actions ─────────────────────────────────────────────────


def do_inspect(ctx: RunContext) -> dict[str, Any]:
    """Return snapshot + timeline for a run."""
    caps = ctx.capabilities()
    mode = caps.inspect

    if mode == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("inspect", "unavailable"))

    if mode == ActionMode.SYNC_DAEMON:
        client = ctx.get_client()
        snap_resp = client.get_snapshot(ctx.run_id)
        tl_resp = client.get_timeline(ctx.run_id, limit=15)
        return {
            "snapshot": snap_resp if snap_resp.get("ok") else {},
            "timeline": tl_resp.get("events", []) if tl_resp.get("ok") else [],
        }

    # SYNC_LOCAL — read from disk
    from supervisor.operator.api import snapshot_from_state, timeline_from_session_log

    state = ctx.load_state()
    if not state:
        return {"snapshot": {}, "timeline": []}
    snap = snapshot_from_state(state, ctx.session_log_path)
    events = timeline_from_session_log(ctx.session_log_path, limit=15)
    return {
        "snapshot": snap.to_dict(),
        "timeline": [e.to_dict() for e in events],
    }


def do_exchange(ctx: RunContext) -> dict[str, Any]:
    """Return recent exchange summary for a run."""
    caps = ctx.capabilities()
    mode = caps.exchange

    if mode == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("exchange", "unavailable"))

    if mode == ActionMode.SYNC_DAEMON:
        client = ctx.get_client()
        return client.get_exchange(ctx.run_id)

    # SYNC_LOCAL
    from supervisor.operator.api import recent_exchange

    state = ctx.load_state()
    if not state:
        return {}
    return recent_exchange(state, ctx.session_log_path)


def do_pause(ctx: RunContext) -> dict[str, Any]:
    """Pause a run via daemon."""
    caps = ctx.capabilities()
    if caps.pause == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("pause", "unavailable"))

    client = ctx.get_client()
    return client.stop_run(ctx.run_id)


def do_resume(ctx: RunContext) -> dict[str, Any]:
    """Resume a paused/orphaned run, auto-starting daemon if needed."""
    caps = ctx.capabilities()
    mode = caps.resume

    if mode == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("resume", "unavailable"))

    if not ctx.spec_path:
        raise ActionUnavailable(f"no spec_path in state for {ctx.run_id[-12:]}")
    if not ctx.pane_target or ctx.pane_target == "?":
        raise ActionUnavailable(f"no pane_target for {ctx.run_id[-12:]}")

    if mode == ActionMode.AUTO_START:
        client = ctx.ensure_daemon()
    else:
        client = ctx.get_client()

    return client.resume(ctx.spec_path, ctx.pane_target)


def do_note_add(ctx: RunContext, content: str, *,
                title: str = "", note_type: str = "operator") -> dict[str, Any]:
    """Add a run-scoped operator note."""
    caps = ctx.capabilities()
    if caps.note_add == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("note_add", "unavailable"))

    client = ctx.get_client()
    return client.note_add(
        content,
        note_type=note_type,
        target_run_id=ctx.run_id,
        title=title or f"note for {ctx.run_id[-12:]}",
    )


def do_note_list(ctx: RunContext) -> list[dict[str, Any]]:
    """List notes for this run."""
    caps = ctx.capabilities()
    if caps.note_list == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("note_list", "unavailable"))

    client = ctx.get_client()
    resp = client.note_list(target_run_id=ctx.run_id)
    return resp.get("notes", [])


# ── async actions (always non-blocking) ──────────────────────────


def submit_explain(ctx: RunContext, *, language: str = "en") -> OperatorJob:
    """Submit an explain_run job. Never blocks the caller."""
    caps = ctx.capabilities()
    if caps.explain == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("explain", "unavailable"))

    if caps.explain == ActionMode.ASYNC_DAEMON:
        client = ctx.get_client()
        resp = client.explain_run(ctx.run_id, language=language)
        return OperatorJob(job_id=resp["job_id"], source="daemon")

    # ASYNC_LOCAL — background thread
    config = ctx.load_config()
    run_id = ctx.run_id
    state_path = ctx.state_path

    def _job() -> dict:
        from supervisor.llm.explainer_client import ExplainerClient
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        explainer = ExplainerClient(
            model=config.explainer_model,
            temperature=config.explainer_temperature,
            max_tokens=config.explainer_max_tokens,
        )
        return explainer.explain_run({"run_state": state, "language": language})

    job_id = _local_jobs.submit("explain", _job)
    return OperatorJob(job_id=job_id, source="local")


def submit_drift(ctx: RunContext, *, language: str = "en") -> OperatorJob:
    """Submit a drift assessment job. Never blocks the caller."""
    caps = ctx.capabilities()
    if caps.drift == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("drift", "unavailable"))

    if caps.drift == ActionMode.ASYNC_DAEMON:
        client = ctx.get_client()
        resp = client.assess_drift(ctx.run_id, language=language)
        return OperatorJob(job_id=resp["job_id"], source="daemon")

    # ASYNC_LOCAL
    config = ctx.load_config()
    state_path = ctx.state_path

    def _job() -> dict:
        from supervisor.llm.explainer_client import ExplainerClient
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        explainer = ExplainerClient(
            model=config.explainer_model,
            temperature=config.explainer_temperature,
            max_tokens=config.explainer_max_tokens,
        )
        return explainer.assess_drift({"run_state": state, "language": language})

    job_id = _local_jobs.submit("drift", _job)
    return OperatorJob(job_id=job_id, source="local")


def submit_explain_exchange(ctx: RunContext, *, language: str = "en") -> OperatorJob:
    """Submit an explain_exchange job. Never blocks the caller."""
    caps = ctx.capabilities()
    if caps.explain == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("explain", "unavailable"))

    if caps.explain == ActionMode.ASYNC_DAEMON:
        client = ctx.get_client()
        resp = client.explain_exchange(ctx.run_id, language=language)
        return OperatorJob(job_id=resp["job_id"], source="daemon")

    # ASYNC_LOCAL
    from supervisor.operator.api import recent_exchange

    config = ctx.load_config()
    state_path = ctx.state_path
    session_log_path = ctx.session_log_path

    def _job() -> dict:
        from supervisor.llm.explainer_client import ExplainerClient
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        exchange = recent_exchange(state, session_log_path)
        explainer = ExplainerClient(
            model=config.explainer_model,
            temperature=config.explainer_temperature,
            max_tokens=config.explainer_max_tokens,
        )
        return explainer.explain_exchange({"exchange": exchange, "language": language})

    job_id = _local_jobs.submit("explain_exchange", _job)
    return OperatorJob(job_id=job_id, source="local")


def poll_job(ctx: RunContext, job: OperatorJob) -> dict[str, Any]:
    """Poll for an async job result. Non-blocking.

    Returns dict with 'status' key: pending | running | completed | failed.
    """
    if job.source == "daemon":
        client = ctx.get_client()
        if client is None:
            return {"status": "failed", "error": "daemon unreachable"}
        return client.get_job(job.job_id)

    # Local job
    j = _local_jobs.get(job.job_id)
    if j is None:
        return {"status": "failed", "error": "job not found"}
    return j.to_dict()
