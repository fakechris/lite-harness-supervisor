"""Unified operator actions — shared by CLI, TUI, and future IM channels.

Each function takes a RunContext, checks capabilities, and executes via
the appropriate mode (daemon, local, auto-start).  Expensive operations
(explain, drift, exchange-explain) are always async — even for local runs,
they use a background-thread JobTracker so the caller never blocks.
"""
from __future__ import annotations

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
    event_plane = _local_event_plane_summary(ctx, state)
    snap = snapshot_from_state(
        state, ctx.session_log_path, event_plane=event_plane,
    )
    events = timeline_from_session_log(ctx.session_log_path, limit=15)
    return {
        "snapshot": snap.to_dict(),
        "timeline": [e.to_dict() for e in events],
    }


def _local_event_plane_summary(ctx: RunContext, state: dict[str, Any]):
    """Open a short-lived EventPlaneStore on the worktree's runtime root
    and fold a :class:`RunEventPlaneSummary` for the state's session_id.

    Returns None when the state predates Task 3 (no session_id) or when
    no runtime root can be derived from the RunContext. Any open / fold
    error is swallowed: observability must never block inspect.
    """
    from pathlib import Path

    session_id = state.get("session_id", "") if state else ""
    if not session_id:
        return None
    worktree = getattr(ctx, "worktree", "") or ""
    if not worktree:
        return None
    runtime_root = Path(worktree) / ".supervisor" / "runtime"
    # Read-only contract: the local inspect path must not create the
    # event-plane shared dir on a worktree that has never emitted an
    # event.  Skip when the directory is absent — an empty summary is
    # equivalent to a never-used event plane.
    if not (runtime_root / "shared").is_dir():
        return None
    try:
        from supervisor.event_plane.store import EventPlaneStore
        from supervisor.event_plane.surface import summarize_for_session
        from supervisor.operator.models import RunEventPlaneSummary

        store = EventPlaneStore(runtime_root)
        ep = summarize_for_session(store, session_id)
    except (OSError, ValueError):
        return None
    return RunEventPlaneSummary(
        waits_open=int(ep.get("waits_open", 0)),
        mailbox_new=int(ep.get("mailbox_new", 0)),
        mailbox_acknowledged=int(ep.get("mailbox_acknowledged", 0)),
        requests_total=int(ep.get("requests_total", 0)),
        latest_mailbox_item_id=str(ep.get("latest_mailbox_item_id", "") or ""),
        latest_wake_decision=str(ep.get("latest_wake_decision", "") or ""),
    )


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


# ── shared context building ──────────────────────────────────────


def _make_explainer(ctx: RunContext):
    """Create an ExplainerClient from the run's worktree config."""
    from supervisor.llm.explainer_client import ExplainerClient

    config = ctx.load_config()
    return ExplainerClient(
        model=config.explainer_model,
        temperature=config.explainer_temperature,
        max_tokens=config.explainer_max_tokens,
        deep_model=config.deep_explainer_model,
        deep_temperature=config.deep_explainer_temperature,
        deep_max_tokens=config.deep_explainer_max_tokens,
    )


def build_explainer_context_from_state(
    state: dict[str, Any],
    session_log_path: Any,
    *,
    spec_path_fallback: str = "",
    workspace_fallback: str = "",
    **extra,
) -> dict[str, Any]:
    """Build the full context dict for explainer calls.

    Core implementation shared by both local operator actions and the daemon.
    Includes run_state, recent_events, spec_context, and codebase_signals.

    Parameters
    ----------
    state : dict
        Run state dict (from state.json or in-memory).
    session_log_path : Path
        Path to session_log.jsonl.
    spec_path_fallback : str
        Fallback spec path if not in state.
    workspace_fallback : str
        Fallback workspace root if not in state.
    """
    from supervisor.operator.api import timeline_from_session_log

    events = timeline_from_session_log(session_log_path, limit=10) if session_log_path else []

    result: dict[str, Any] = {
        "run_state": state,
        "recent_events": [e.to_dict() for e in events],
    }

    # Load spec for richer context — prompt expects "spec_context"
    spec_path = state.get("spec_path", "") or spec_path_fallback
    if spec_path:
        try:
            from supervisor.plan.loader import load_spec

            spec_data = load_spec(spec_path)
            acceptance = getattr(spec_data, "acceptance", None)
            all_nodes = getattr(spec_data, "nodes", []) or getattr(spec_data, "steps", [])
            result["spec_context"] = {
                "id": getattr(spec_data, "id", ""),
                "goal": getattr(spec_data, "goal", ""),
                "nodes": [
                    {"id": n.id, "objective": getattr(n, "objective", "")}
                    for n in all_nodes
                ],
                "required_evidence": getattr(acceptance, "required_evidence", []) if acceptance else [],
                "forbidden_states": getattr(acceptance, "forbidden_states", []) if acceptance else [],
            }
        except Exception:
            pass

    # Gather lightweight codebase signals — prompt expects "codebase_signals"
    workspace = state.get("workspace_root", "") or workspace_fallback
    codebase_signals: dict[str, Any] = {"workspace_root": workspace}
    if workspace:
        try:
            import subprocess

            git_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace, capture_output=True, text=True, timeout=5,
            )
            if git_result.returncode == 0:
                dirty_files = [
                    line.strip() for line in git_result.stdout.strip().splitlines()
                    if line.strip()
                ]
                codebase_signals["git_dirty"] = bool(dirty_files)
                codebase_signals["dirty_file_count"] = len(dirty_files)
                codebase_signals["dirty_files_sample"] = dirty_files[:10]
        except Exception:
            pass
    result["codebase_signals"] = codebase_signals

    result.update(extra)
    return result


def build_explainer_context(
    ctx: RunContext,
    *,
    state: dict[str, Any] | None = None,
    session_log_path: Any = None,
    **extra,
) -> dict[str, Any]:
    """Build explainer context from a RunContext.

    Convenience wrapper around ``build_explainer_context_from_state``
    that resolves state and paths from the RunContext when not provided.
    """
    if state is None:
        state = ctx.load_state()
    if session_log_path is None:
        session_log_path = ctx.session_log_path

    return build_explainer_context_from_state(
        state,
        session_log_path,
        spec_path_fallback=ctx.spec_path,
        workspace_fallback=ctx.worktree,
        **extra,
    )


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

    # ASYNC_LOCAL — background thread with full context
    explainer = _make_explainer(ctx)

    def _job() -> dict:
        context = build_explainer_context(ctx, language=language)
        return explainer.explain_run(context)

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

    # ASYNC_LOCAL — background thread with full context
    explainer = _make_explainer(ctx)

    def _job() -> dict:
        context = build_explainer_context(ctx, language=language)
        return explainer.assess_drift(context)

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

    # ASYNC_LOCAL — background thread with full context
    from supervisor.operator.api import recent_exchange

    explainer = _make_explainer(ctx)

    def _job() -> dict:
        context = build_explainer_context(ctx, language=language)
        exchange = recent_exchange(context["run_state"], ctx.session_log_path)
        context["exchange"] = exchange
        return explainer.explain_exchange(context)

    job_id = _local_jobs.submit("explain_exchange", _job)
    return OperatorJob(job_id=job_id, source="local")


def submit_clarification(ctx: RunContext, question: str, *,
                         language: str = "en") -> OperatorJob:
    """Submit a clarification request about a run. Never blocks the caller."""
    caps = ctx.capabilities()
    # Clarification follows the same mode as explain
    if caps.explain == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("explain", "unavailable"))

    if caps.explain == ActionMode.ASYNC_DAEMON:
        client = ctx.get_client()
        resp = client.request_clarification(ctx.run_id, question, language=language)
        return OperatorJob(job_id=resp["job_id"], source="daemon")

    # ASYNC_LOCAL
    explainer = _make_explainer(ctx)
    escalation_threshold = ctx.load_config().clarification_escalation_confidence

    def _job() -> dict:
        from supervisor.operator.api import append_timeline_event
        from supervisor.operator.clarification import finalize_clarification

        def _write(event_type: str, payload: dict[str, Any]) -> None:
            append_timeline_event(
                ctx.session_log_path, ctx.run_id, event_type, payload,
            )

        _write("clarification_request", {"question": question, "language": language})

        context = build_explainer_context(ctx, language=language)
        context["question"] = question
        result = explainer.request_clarification(context)

        return finalize_clarification(
            result,
            question=question,
            escalation_threshold=escalation_threshold,
            write_event=_write,
        )

    job_id = _local_jobs.submit("clarification", _job)
    return OperatorJob(job_id=job_id, source="local")


def do_escalate_clarification(
    ctx: RunContext,
    question: str,
    *,
    language: str = "en",
    reason: str = "operator_initiated",
    operator: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    """Record an operator's decision to escalate a clarification to the worker.

    Audit-only in 0.3.7. Emits ``clarification_escalated_to_worker`` into
    the session log. Actual worker transport + reply capture ship in 0.3.8.

    Returns ``{"escalation_id": <hex>, "source": "daemon"|"local"}``.
    """
    import uuid

    caps = ctx.capabilities()
    # Piggy-back on explain capability — same preconditions (need a
    # resolvable run_id + session_log).
    if caps.explain == ActionMode.UNAVAILABLE:
        raise ActionUnavailable(caps.unavailable_reasons.get("explain", "unavailable"))

    if caps.explain == ActionMode.ASYNC_DAEMON:
        client = ctx.get_client()
        resp = client.escalate_clarification(
            ctx.run_id, question,
            language=language, reason=reason,
            operator=operator, confidence=confidence,
        )
        if not resp.get("ok", False):
            raise ActionUnavailable(resp.get("error", "escalate failed"))
        return {
            "escalation_id": resp.get("escalation_id", ""),
            "source": "daemon",
        }

    # ASYNC_LOCAL — write directly to the session log
    from supervisor.operator.api import append_timeline_event

    escalation_id = uuid.uuid4().hex[:16]
    append_timeline_event(
        ctx.session_log_path, ctx.run_id,
        "clarification_escalated_to_worker",
        {
            "escalation_id": escalation_id,
            "question": question,
            "language": language,
            "reason": reason,
            "operator": operator,
            "confidence": confidence,
            "transport": "pending_0_3_8",
        },
    )
    return {"escalation_id": escalation_id, "source": "local"}


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
