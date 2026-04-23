"""Shared command dispatch layer for IM operator channels.

Provides auth, command parsing, run resolution, action dispatch, and
async job polling.  Telegram and Lark adapters are thin wrappers over
this shared logic, ensuring all IM channels use the canonical operator
action layer (RunContext + OperatorActions).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from supervisor.operator.actions import (
    ActionUnavailable,
    OperatorJob,
    do_escalate_clarification,
    do_exchange,
    do_inspect,
    do_note_add,
    do_note_list,
    do_pause,
    do_resume,
    poll_job,
    submit_clarification,
    submit_drift,
    submit_explain,
    submit_explain_exchange,
)
from supervisor.operator.run_context import RunContext

logger = logging.getLogger(__name__)


# ── Auth ──────────────────────────────────────────────────────────


class CommandAuth:
    """Allowlist-based authorization.  Fail-closed."""

    def __init__(
        self,
        allowed_chat_ids: list[str] | None = None,
        allowed_user_ids: list[str] | None = None,
    ):
        self._chat_ids = set(str(c) for c in (allowed_chat_ids or []))
        self._user_ids = set(str(u) for u in (allowed_user_ids or []))

    @property
    def allowed_chat_ids(self) -> frozenset[str]:
        """Read-only view of the merged chat allowlist."""
        return frozenset(self._chat_ids)

    @property
    def allowed_user_ids(self) -> frozenset[str]:
        """Read-only view of the merged user allowlist."""
        return frozenset(self._user_ids)

    def is_authorized(self, chat_id: str, user_id: str = "") -> bool:
        if not self._chat_ids and not self._user_ids:
            return False  # fail-closed: empty allowlist rejects all
        if self._chat_ids and str(chat_id) in self._chat_ids:
            return True
        if self._user_ids and str(user_id) in self._user_ids:
            return True
        return False


# ── Command parsing ───────────────────────────────────────────────


def parse_command(text: str) -> tuple[str, list[str]]:
    """Parse "/inspect abc123 extra" into ("inspect", ["abc123", "extra"]).

    Strips leading slash and @bot_name suffix.  Returns ("", []) for
    empty or unparseable input.
    """
    text = text.strip()
    if not text:
        return "", []
    # Strip leading /
    if text.startswith("/"):
        text = text[1:]
    # Strip @bot_name suffix from command (e.g. "/runs@my_bot")
    parts = text.split(None, 1)
    if not parts:
        return "", []
    cmd = parts[0].split("@")[0].lower()
    args = parts[1].split() if len(parts) > 1 else []
    return cmd, args


# ── Run resolution ────────────────────────────────────────────────


def resolve_run(run_id_fragment: str) -> list[dict[str, Any]]:
    """Find runs matching a fragment (suffix or prefix match).

    Returns list of matching run dicts from collect_runs().
    """
    from supervisor.operator.tui import collect_runs

    all_runs = collect_runs()
    if not run_id_fragment:
        return all_runs

    fragment = run_id_fragment.lower()
    # Exact match first
    for r in all_runs:
        if r["run_id"].lower() == fragment:
            return [r]
    # Suffix match (most common: user types last N chars)
    matches = [r for r in all_runs if r["run_id"].lower().endswith(fragment)]
    if matches:
        return matches
    # Prefix match
    matches = [r for r in all_runs if r["run_id"].lower().startswith(fragment)]
    if matches:
        return matches
    # Substring match
    return [r for r in all_runs if fragment in r["run_id"].lower()]


# ── Command result ────────────────────────────────────────────────


@dataclass
class CommandResult:
    """Structured result from command dispatch."""
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    buttons: list[dict[str, str]] = field(default_factory=list)
    job: OperatorJob | None = None
    ctx: RunContext | None = None
    error: bool = False


# ── Formatting helpers ────────────────────────────────────────────


def format_runs_list(runs: list[dict[str, Any]]) -> str:
    """Format run list for IM display."""
    if not runs:
        return "No runs found."
    lines = []
    for r in runs[:15]:
        rid = r["run_id"][-12:]
        state = r.get("top_state", "?")
        node = r.get("current_node", "")
        tag = r.get("tag", "")
        wt = r.get("worktree", "")
        line = f"  {rid}  {state:<18} {tag:<10}"
        if node:
            line += f" {node}"
        if wt:
            line += f"\n    {wt}"
        lines.append(line)
    header = f"Runs ({len(runs)}):"
    return header + "\n" + "\n".join(lines)


def format_inspect_result(data: dict[str, Any]) -> str:
    """Format inspect result (snapshot + timeline) for IM."""
    snap = data.get("snapshot", {})
    timeline = data.get("timeline", [])

    lines = []
    if snap:
        lines.append(f"Run: {snap.get('run_id', '?')}")
        lines.append(f"Spec: {snap.get('spec_id', '?')}")
        lines.append(f"State: {snap.get('top_state', '?')}")
        lines.append(f"Node: {snap.get('current_node', '?')} (attempt {snap.get('current_attempt', 0)})")
        lines.append(f"Worktree: {snap.get('worktree_root', '?')}")
        lines.append(f"Done: {', '.join(snap.get('done_nodes', [])) or '(none)'}")
        if snap.get("pause_reason"):
            lines.append(f"Pause: {snap['pause_reason']}")
        if snap.get("last_checkpoint_summary"):
            lines.append(f"Checkpoint: {snap['last_checkpoint_summary'][:80]}")
        if snap.get("next_action"):
            lines.append(f"Next: {snap['next_action'][:80]}")
    else:
        lines.append("(no snapshot)")

    if timeline:
        lines.append("")
        lines.append("Timeline:")
        for ev in timeline[:10]:
            ts = ev.get("occurred_at", "")[:16]
            etype = ev.get("event_type", "?")
            summary = ev.get("summary", "")[:50]
            lines.append(f"  {ts} [{etype}] {summary}")

    return "\n".join(lines)


def format_exchange_result(data: dict[str, Any]) -> str:
    """Format exchange result for IM."""
    if not data:
        return "(no exchange data)"
    lines = ["Exchange:"]
    cp = data.get("last_checkpoint_summary", "")
    instr = data.get("last_instruction_summary", "")
    lines.append(f"  Checkpoint: {cp[:120] or '(none)'}")
    lines.append(f"  Instruction: {instr[:120] or '(none)'}")
    if data.get("checkpoint_excerpt"):
        lines.append(f"  CP excerpt: {data['checkpoint_excerpt'][:100]}")
    if data.get("instruction_excerpt"):
        lines.append(f"  Instr excerpt: {data['instruction_excerpt'][:100]}")
    lines.append(f"  Recent events: {data.get('recent_event_count', 0)}")
    return "\n".join(lines)


def format_explanation_result(result: dict[str, Any]) -> str:
    """Format explainer result (explain/drift/clarification) for IM."""
    if not result:
        return "(no result)"
    lines = []
    # explain_run format
    if "explanation" in result:
        lines.append(result["explanation"][:300])
    if "current_activity" in result:
        lines.append(f"Activity: {result['current_activity'][:100]}")
    if "recent_progress" in result:
        lines.append(f"Progress: {result['recent_progress'][:100]}")
    if "next_expected" in result:
        lines.append(f"Next: {result['next_expected'][:100]}")
    # drift format
    if "status" in result and "reasons" in result:
        lines.append(f"Drift: {result['status']}")
        for r in result.get("reasons", [])[:3]:
            lines.append(f"  - {r[:80]}")
        if result.get("recommended_action"):
            lines.append(f"Action: {result['recommended_action'][:100]}")
    # clarification format
    if "answer" in result and "explanation" not in result:
        lines.append(result["answer"][:300])
        for e in result.get("evidence", [])[:3]:
            lines.append(f"  - {e[:80]}")
        if result.get("follow_up"):
            lines.append(f"Follow-up: {result['follow_up'][:100]}")
    # confidence
    conf = result.get("confidence")
    if conf is not None:
        lines.append(f"Confidence: {conf}")
    return "\n".join(lines) if lines else "(empty result)"


def format_notes_result(notes: list[dict[str, Any]]) -> str:
    """Format notes list for IM."""
    if not notes:
        return "(no notes)"
    lines = ["Notes:"]
    for note in notes[:15]:
        ts = note.get("timestamp", "")[:16]
        author = note.get("author_run_id", "?")[:12]
        content = note.get("content", "")[:80]
        lines.append(f"  {ts} [{author}] {content}")
    return "\n".join(lines)


_HELP_TEXT = """Available commands:
/runs - list all runs
/run <id> - show run summary
/inspect <id> - snapshot + timeline
/exchange <id> - recent exchange
/explain <id> - explain what the run is doing
/drift <id> - assess drift from plan
/ask <id> <question> - ask about the run
/escalate <id> [question] - escalate last (or given) clarification to the worker
/pause <id> - pause a run
/resume <id> - resume a paused run
/note <id> <text> - add operator note
/notes <id> - list notes
/help - show this help"""


# ── Action buttons ────────────────────────────────────────────────


def _run_buttons(run_id: str) -> list[dict[str, str]]:
    """Standard action buttons for a run."""
    return [
        {"label": "Inspect", "cmd": "inspect", "run_id": run_id},
        {"label": "Explain", "cmd": "explain", "run_id": run_id},
        {"label": "Drift", "cmd": "drift", "run_id": run_id},
        {"label": "Pause", "cmd": "pause", "run_id": run_id},
        {"label": "Resume", "cmd": "resume", "run_id": run_id},
        {"label": "Notes", "cmd": "notes", "run_id": run_id},
    ]


# ── Command dispatch ─────────────────────────────────────────────


def _require_run(args: list[str]) -> tuple[RunContext, dict[str, Any]] | CommandResult:
    """Resolve a single run from args.  Returns (ctx, run_dict) or error CommandResult."""
    if not args:
        return CommandResult(text="Usage: /<command> <run_id>", error=True)
    candidates = resolve_run(args[0])
    if not candidates:
        return CommandResult(text=f"Run not found: {args[0]}", error=True)
    if len(candidates) > 1:
        text = f"Ambiguous run id '{args[0]}', {len(candidates)} matches:\n"
        text += format_runs_list(candidates)
        return CommandResult(text=text, error=True)
    run = candidates[0]
    ctx = RunContext.from_run_dict(run)
    return ctx, run


def _latest_clarification(
    ctx: RunContext, *, override: str = "",
) -> tuple[str, float | None]:
    """Pick the question + confidence to use for ``/escalate``.

    When *override* is non-empty the operator supplied the question
    explicitly, so we use it verbatim with unknown confidence. Otherwise
    we scan the session log for the most recent ``clarification_response``
    event and pull its ``question`` / ``confidence`` fields.
    """
    if override:
        return override, None
    log_path = ctx.session_log_path
    if log_path is None or not log_path.exists():
        return "", None
    from supervisor.operator.api import timeline_from_session_log

    events = timeline_from_session_log(log_path, limit=50)
    for ev in events:
        if ev.event_type == "clarification_response":
            q = str(ev.payload.get("question", "")).strip()
            if not q:
                continue
            conf = ev.payload.get("confidence")
            try:
                conf_val: float | None = float(conf) if conf is not None else None
            except (TypeError, ValueError):
                conf_val = None
            return q, conf_val
    return "", None


def dispatch_command(
    cmd: str,
    args: list[str],
    *,
    language: str = "zh",
) -> CommandResult:
    """Route a parsed command to the appropriate operator action."""

    if cmd in ("runs", "list"):
        from supervisor.operator.tui import collect_runs
        runs = collect_runs()
        return CommandResult(
            text=format_runs_list(runs),
            data={"runs": runs},
        )

    if cmd == "help":
        return CommandResult(text=_HELP_TEXT)

    if cmd == "run":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        text = format_runs_list([run])
        return CommandResult(
            text=text,
            data={"run": run},
            buttons=_run_buttons(run["run_id"]),
        )

    if cmd == "inspect":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            data = do_inspect(ctx)
            return CommandResult(
                text=format_inspect_result(data),
                data=data,
                buttons=_run_buttons(run["run_id"]),
            )
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "exchange":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            data = do_exchange(ctx)
            return CommandResult(
                text=format_exchange_result(data),
                data=data,
                buttons=_run_buttons(run["run_id"]),
            )
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "explain":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            job = submit_explain(ctx, language=language)
            return CommandResult(
                text="Working...",
                job=job,
                ctx=ctx,
                buttons=_run_buttons(run["run_id"]),
            )
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "drift":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            job = submit_drift(ctx, language=language)
            return CommandResult(
                text="Working...",
                job=job,
                ctx=ctx,
                buttons=_run_buttons(run["run_id"]),
            )
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "ask":
        if len(args) < 2:
            return CommandResult(text="Usage: /ask <run_id> <question>", error=True)
        run_id_frag = args[0]
        question = " ".join(args[1:])
        resolved = _require_run([run_id_frag])
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            job = submit_clarification(ctx, question, language=language)
            return CommandResult(
                text="Working...",
                job=job,
                ctx=ctx,
                buttons=_run_buttons(run["run_id"]),
            )
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "escalate":
        if not args:
            return CommandResult(
                text="Usage: /escalate <run_id> [question]", error=True,
            )
        resolved = _require_run([args[0]])
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        explicit_question = " ".join(args[1:]).strip()
        question, confidence = _latest_clarification(ctx, override=explicit_question)
        if not question:
            return CommandResult(
                text=(
                    "No prior clarification to escalate. "
                    "Ask first with /ask, or provide a question: "
                    "/escalate <run_id> <question>"
                ),
                error=True,
            )
        try:
            resp = do_escalate_clarification(
                ctx, question,
                language=language,
                reason="im_operator",
                confidence=confidence,
            )
            esc_id = resp.get("escalation_id", "")[:12]
            return CommandResult(
                text=(
                    f"Escalated to worker (id={esc_id}).\n"
                    f"Question: {question[:200]}\n"
                    "Transport lands in 0.3.8 — session log has the audit entry."
                ),
                data={"escalation_id": resp.get("escalation_id", "")},
                buttons=_run_buttons(run["run_id"]),
            )
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "pause":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            resp = do_pause(ctx)
            if resp.get("ok"):
                return CommandResult(text=f"Paused {run['run_id'][-12:]}")
            return CommandResult(text=f"Pause failed: {resp.get('error', '?')}", error=True)
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "resume":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            resp = do_resume(ctx)
            if resp.get("ok"):
                return CommandResult(text=f"Resumed {run['run_id'][-12:]}")
            return CommandResult(text=f"Resume failed: {resp.get('error', '?')}", error=True)
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "note":
        if len(args) < 2:
            return CommandResult(text="Usage: /note <run_id> <content>", error=True)
        run_id_frag = args[0]
        content = " ".join(args[1:])
        resolved = _require_run([run_id_frag])
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            resp = do_note_add(ctx, content)
            if resp.get("ok"):
                return CommandResult(text=f"Note added to {run['run_id'][-12:]}")
            return CommandResult(text=f"Note failed: {resp.get('error', '?')}", error=True)
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    if cmd == "notes":
        resolved = _require_run(args)
        if isinstance(resolved, CommandResult):
            return resolved
        ctx, run = resolved
        try:
            notes = do_note_list(ctx)
            return CommandResult(
                text=format_notes_result(notes),
                data={"notes": notes},
            )
        except ActionUnavailable as exc:
            return CommandResult(text=str(exc), error=True)

    return CommandResult(
        text=f"Unknown command: /{cmd}\n\n{_HELP_TEXT}",
        error=True,
    )


# ── Async job poller ──────────────────────────────────────────────


@dataclass
class _PendingJob:
    ctx: RunContext
    job: OperatorJob
    on_complete: Callable[[dict[str, Any]], None]
    created_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


class AsyncJobPoller:
    """Background thread that polls pending async jobs and calls back when done.

    Used by IM channels to implement streaming: send "Working..." message,
    then edit with result when the job completes.
    """

    def __init__(self, *, poll_interval: float = 2.0, timeout: float = 120.0):
        self._pending: list[_PendingJob] = []
        self._lock = threading.Lock()
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def track(
        self,
        ctx: RunContext,
        job: OperatorJob,
        on_complete: Callable[[dict[str, Any]], None],
    ) -> None:
        """Register a job for polling.  on_complete(result) called when done."""
        with self._lock:
            self._pending.append(_PendingJob(ctx=ctx, job=job, on_complete=on_complete))
        self.start()  # ensure polling thread is running

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break
            with self._lock:
                jobs = list(self._pending)
            done_ids: list[str] = []
            for pj in jobs:
                try:
                    result = poll_job(pj.ctx, pj.job)
                    status = result.get("status", "")
                    if status in ("completed", "failed"):
                        done_ids.append(pj.job.job_id)
                        try:
                            pj.on_complete(result)
                        except Exception:
                            logger.exception("job callback failed for %s", pj.job.job_id)
                    elif time.time() - pj.created_at > self._timeout:
                        done_ids.append(pj.job.job_id)
                        try:
                            pj.on_complete({"status": "failed", "error": "timeout"})
                        except Exception:
                            logger.exception("timeout callback failed for %s", pj.job.job_id)
                except Exception:
                    logger.exception("poll error for job %s", pj.job.job_id)
            if done_ids:
                with self._lock:
                    self._pending = [
                        p for p in self._pending if p.job.job_id not in done_ids
                    ]
