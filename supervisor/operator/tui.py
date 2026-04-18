"""Operator TUI — first channel implementation.

Three-pane layout:
  Left:   run list
  Center: selected run snapshot + timeline
  Right:  explanation / drift / exchange / next action
  Bottom: command line

Uses curses for terminal rendering. Falls back gracefully
if terminal is too small.
"""
from __future__ import annotations

import curses
import time
from typing import Any

from supervisor.operator.session_index import collect_sessions
from supervisor.operator.actions import (
    ActionUnavailable,
    OperatorJob,
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
from supervisor.operator.run_context import ActionMode, RunContext


# ── Data collection ────────────────────────────────────────────────

def collect_runs(daemons: list[dict] | None = None) -> list[dict]:
    """Return all runs in the canonical global session universe.

    Thin adapter over ``collect_sessions()``: every read surface (status,
    dashboard, tui, command_dispatch) consumes the same normalized record
    set, so they can never disagree on whether a run exists.

    The ``daemons`` argument is retained for backward compatibility with
    existing callers but is no longer consulted — discovery is driven by
    ``session_index`` registries.
    """
    del daemons  # unused; session_index owns discovery
    records = collect_sessions()
    items: list[dict] = []
    for rec in records:
        items.append({
            "run_id": rec.run_id,
            "tag": rec.tag or "local",
            "top_state": rec.top_state,
            "current_node": rec.current_node,
            "pane_target": rec.pane_target or "?",
            "worktree": rec.worktree_root,
            "socket": rec.daemon_socket,
            "status_reason": rec.last_checkpoint_summary,
        })
    return items




# ── Formatting helpers ─────────────────────────────────────────────

STATE_COLORS = {
    "RUNNING": 2,        # green
    "GATING": 3,         # yellow
    "VERIFYING": 3,      # yellow
    "PAUSED_FOR_HUMAN": 1,  # red
    "COMPLETED": 6,      # cyan
    "FAILED": 1,         # red
    "ABORTED": 1,        # red
    "READY": 4,          # blue
}


def format_run_line(run: dict, selected: bool = False) -> str:
    """Format a single run for the list pane."""
    rid = run["run_id"][-12:]  # short id
    state = run["top_state"][:10]
    node = run.get("current_node", "")[:15]
    tag = run.get("tag", "")[:8]
    marker = ">" if selected else " "
    return f"{marker} [{tag:>8}] {rid}  {state:<10} {node}"


def format_snapshot(snap: dict[str, Any]) -> list[str]:
    """Format a RunSnapshot dict into display lines."""
    lines = [
        f"Run:       {snap.get('run_id', '?')}",
        f"Spec:      {snap.get('spec_id', '?')}",
        f"State:     {snap.get('top_state', '?')}",
        f"Node:      {snap.get('current_node', '?')} (attempt {snap.get('current_attempt', 0)})",
        f"Surface:   {snap.get('surface_type', '?')} → {snap.get('surface_target', '?')}",
        f"Worktree:  {snap.get('worktree_root', '?')}",
        f"Delivery:  {snap.get('delivery_state', '?')}",
        f"Done:      {', '.join(snap.get('done_nodes', [])) or '(none)'}",
    ]
    if snap.get("pause_reason"):
        lines.append(f"Pause:     {snap['pause_reason']}")
    if snap.get("status_reason"):
        lines.append(f"Status:    {snap['status_reason']}")
    if snap.get("last_checkpoint_summary"):
        lines.append(f"Checkpoint: {snap['last_checkpoint_summary'][:80]}")
    if snap.get("next_action"):
        lines.append(f"Next:      {snap['next_action'][:80]}")
    if snap.get("updated_at"):
        lines.append(f"Updated:   {snap['updated_at'][:19]}")
    return lines


def format_timeline(events: list[dict]) -> list[str]:
    """Format timeline events into display lines."""
    lines = ["", "Timeline:"]
    if not events:
        lines.append("  (no events)")
        return lines
    for ev in events[:15]:
        ts = ev.get("occurred_at", "")[:19]
        etype = ev.get("event_type", "?")
        summary = ev.get("summary", "")[:60]
        lines.append(f"  {ts} [{etype}] {summary}")
    return lines


def format_exchange(exchange: dict[str, Any]) -> list[str]:
    """Format a recent exchange dict into display lines."""
    lines = ["Exchange:"]
    cp = exchange.get("last_checkpoint_summary", "")
    instr = exchange.get("last_instruction_summary", "")
    lines.append(f"  Checkpoint: {cp[:120] or '(none)'}")
    lines.append(f"  Instruction: {instr[:120] or '(none)'}")
    excerpt_cp = exchange.get("checkpoint_excerpt", "")
    excerpt_instr = exchange.get("instruction_excerpt", "")
    if excerpt_cp:
        lines.append(f"  CP excerpt: {excerpt_cp[:100]}")
    if excerpt_instr:
        lines.append(f"  Instr excerpt: {excerpt_instr[:100]}")
    lines.append(f"  Recent events: {exchange.get('recent_event_count', 0)}")
    return lines


def format_explanation(result: dict[str, Any]) -> list[str]:
    """Format an explainer result into display lines."""
    lines = ["Explanation:"]
    if not result:
        lines.append("  (none)")
        return lines

    # Handle explain_run format
    if "explanation" in result:
        lines.append(f"  {result['explanation'][:200]}")
    if "current_activity" in result:
        lines.append(f"  Activity: {result['current_activity'][:100]}")
    if "recent_progress" in result:
        lines.append(f"  Progress: {result['recent_progress'][:100]}")
    if "next_expected" in result:
        lines.append(f"  Next: {result['next_expected'][:100]}")

    # Handle drift assessment format
    if "status" in result and "reasons" in result:
        lines.append(f"  Drift: {result['status']}")
        for r in result.get("reasons", [])[:3]:
            lines.append(f"    - {r[:80]}")
        if result.get("recommended_action"):
            lines.append(f"  Action: {result['recommended_action'][:100]}")

    conf = result.get("confidence")
    if conf is not None:
        lines.append(f"  Confidence: {conf}")

    return lines


def format_notes(notes: list[dict[str, Any]]) -> list[str]:
    """Format run-scoped notes into display lines."""
    lines = ["Notes:"]
    if not notes:
        lines.append("  (no notes)")
        return lines
    for note in notes[:20]:
        ts = note.get("timestamp", "")[:19]
        author = note.get("author_run_id", "?")[:12]
        content = note.get("content", "")[:80]
        lines.append(f"  {ts} [{author}] {content}")
    return lines


def format_clarification(result: dict[str, Any]) -> list[str]:
    """Format a clarification response into display lines."""
    lines = ["Answer:"]
    if not result:
        lines.append("  (no answer)")
        return lines
    answer = result.get("answer", "")
    for i in range(0, len(answer), 100):
        lines.append(f"  {answer[i:i+100]}")
    evidence = result.get("evidence", [])
    if evidence:
        lines.append("")
        lines.append("Evidence:")
        for e in evidence[:5]:
            lines.append(f"  - {e[:80]}")
    follow_up = result.get("follow_up", "")
    if follow_up:
        lines.append(f"  Follow-up: {follow_up[:100]}")
    conf = result.get("confidence")
    if conf is not None:
        lines.append(f"  Confidence: {conf}")
    return lines


# ── Global-mode formatters (Task 6) ───────────────────────────────

def format_system_banner(snapshot) -> list[str]:
    """One-line (wrapped) banner from ``SystemSnapshot.counts``.

    Mirrors the overview CLI headline so operators see identical
    numbers in both surfaces.
    """
    c = snapshot.counts
    return [
        "System:",
        (
            f"  daemons={c.daemons}  "
            f"live={c.live_sessions}  "
            f"foreground={c.foreground_runs}  "
            f"orphaned={c.orphaned_sessions}  "
            f"completed={c.completed_sessions}"
        ),
        (
            f"  waits_open={c.waits_open}  "
            f"mailbox_new={c.mailbox_new}  "
            f"mailbox_ack={c.mailbox_acknowledged}"
        ),
    ]


def format_system_alerts(alerts) -> list[str]:
    """Bulleted alert list; quiet state renders a single ``(none)`` line."""
    lines = ["Alerts:"]
    if not alerts:
        lines.append("  (none)")
        return lines
    for a in alerts:
        lines.append(f"  • [{a.kind}] {a.summary}")
    return lines


def format_system_timeline(events) -> list[str]:
    """Render a shared cross-run timeline (SystemTimelineEvent[])."""
    lines = ["Recent events:"]
    if not events:
        lines.append("  (none)")
        return lines
    for ev in events[:15]:
        ts = (ev.occurred_at or "")[:19]
        scope_tag = ev.scope or "system"
        lines.append(f"  [{scope_tag}] {ts}  {ev.event_type}  — {ev.summary}")
    return lines


def _is_paused(session) -> bool:
    """Return True for sessions the operator must still unblock.

    Classify off ``top_state`` — the authoritative signal — rather than
    ``pause_reason``.  Legacy paused runs may have an empty reason
    string, which would otherwise hide them from the actionable list.
    """
    return session.top_state == "PAUSED_FOR_HUMAN"


def _session_urgency_key(session) -> tuple[int, str]:
    """Sort key that surfaces the most actionable session first.

    Priority (lower tuple sorts first):
      0: paused_for_human (human must act)
      1: orphaned (no live owner)
      2: mailbox_new > 0
      3: waits_open > 0
      4: everything else live
    Completed / foreground-but-quiet sessions are filtered out upstream.
    """
    if not session.is_completed and _is_paused(session):
        return (0, session.last_update_at or "")
    if session.is_orphaned:
        return (1, session.last_update_at or "")
    ep = session.event_plane or {}
    if int(ep.get("mailbox_new", 0) or 0) > 0:
        return (2, session.last_update_at or "")
    if int(ep.get("waits_open", 0) or 0) > 0:
        return (3, session.last_update_at or "")
    return (4, session.last_update_at or "")


def format_actionable_sessions(snapshot) -> list[str]:
    """List sessions that still need operator attention, most urgent first.

    Completed sessions are filtered out — they live under the overview's
    ``completed`` counter, not the actionable list. A session is
    considered actionable when it's paused, orphaned, has mailbox
    backlog, or has an open wait.
    """
    lines = ["Actionable sessions:"]
    actionable = []
    for s in snapshot.sessions:
        if s.is_completed:
            continue
        ep = s.event_plane or {}
        has_mailbox = int(ep.get("mailbox_new", 0) or 0) > 0
        has_waits = int(ep.get("waits_open", 0) or 0) > 0
        if not (_is_paused(s) or s.is_orphaned or has_mailbox or has_waits):
            continue
        actionable.append(s)
    if not actionable:
        lines.append("  (none)")
        return lines
    actionable.sort(key=_session_urgency_key)
    for s in actionable:
        tags = []
        if _is_paused(s):
            tags.append("paused")
        if s.is_orphaned:
            tags.append("orphaned")
        ep = s.event_plane or {}
        if int(ep.get("mailbox_new", 0) or 0) > 0:
            tags.append(f"mailbox:{int(ep['mailbox_new'])}")
        if int(ep.get("waits_open", 0) or 0) > 0:
            tags.append(f"waits:{int(ep['waits_open'])}")
        label = f"[{' '.join(tags)}]" if tags else ""
        lines.append(
            f"  {s.run_id[-14:]}  {s.top_state:<18} {label}"
        )
    return lines


# ── Curses TUI ────────────────────────────────────────────────────

MIN_WIDTH = 80
MIN_HEIGHT = 20


def _init_colors():
    curses.start_color()
    curses.use_default_colors()
    for i in range(1, 8):
        curses.init_pair(i, i, -1)


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0):
    """Write text, truncating to window width and handling edge cases."""
    max_y, max_x = win.getmaxyx()
    if y >= max_y or x >= max_x:
        return
    available = max_x - x - 1
    if available <= 0:
        return
    try:
        win.addnstr(y, x, text, available, attr)
    except curses.error:
        pass


def _draw_runs_pane_lines(win, lines: list[str], *, header: str = " Overview "):
    """Draw arbitrary text into the left pane (used by global mode)."""
    win.erase()
    max_y, _ = win.getmaxyx()
    _safe_addstr(win, 0, 0, header, curses.A_BOLD | curses.A_REVERSE)
    for i, line in enumerate(lines):
        if i + 1 >= max_y:
            break
        _safe_addstr(win, i + 1, 0, line)
    win.noutrefresh()


def _draw_runs_pane(win, runs: list[dict], selected_idx: int):
    """Draw the left pane with run list."""
    win.erase()
    max_y, max_x = win.getmaxyx()
    _safe_addstr(win, 0, 0, " Runs ", curses.A_BOLD | curses.A_REVERSE)

    for i, run in enumerate(runs):
        if i + 1 >= max_y:
            break
        is_selected = (i == selected_idx)
        line = format_run_line(run, selected=is_selected)
        state = run.get("top_state", "")
        color = STATE_COLORS.get(state, 0)
        attr = curses.color_pair(color)
        if is_selected:
            attr |= curses.A_BOLD
        _safe_addstr(win, i + 1, 0, line, attr)

    win.noutrefresh()


def _draw_detail_pane(win, lines: list[str]):
    """Draw the center pane with snapshot + timeline."""
    win.erase()
    max_y, _ = win.getmaxyx()
    _safe_addstr(win, 0, 0, " Details ", curses.A_BOLD | curses.A_REVERSE)

    for i, line in enumerate(lines):
        if i + 1 >= max_y:
            break
        _safe_addstr(win, i + 1, 0, line)

    win.noutrefresh()


def _draw_right_pane(win, lines: list[str]):
    """Draw the right pane with explanation / drift."""
    win.erase()
    max_y, _ = win.getmaxyx()
    _safe_addstr(win, 0, 0, " Explain ", curses.A_BOLD | curses.A_REVERSE)

    for i, line in enumerate(lines):
        if i + 1 >= max_y:
            break
        _safe_addstr(win, i + 1, 0, line)

    win.noutrefresh()


def _draw_status_bar(win, msg: str):
    """Draw the bottom status/command bar."""
    win.erase()
    _safe_addstr(win, 0, 0, msg, curses.A_REVERSE)
    win.noutrefresh()


def _curses_main(stdscr):
    """Main curses loop."""
    _init_colors()
    curses.curs_set(0)
    stdscr.timeout(500)  # 500ms for responsive job polling

    selected_idx = 0
    runs: list[dict] = []
    detail_lines: list[str] = ["(select a run)"]
    right_lines: list[str] = ["(press 'e' to explain, 'd' for drift)"]
    language = "en"
    status_msg = " j/k:nav  g:global  e:explain  x:exchange  d:drift  c:ask  p:pause  r:resume  l:lang  n:note  N:notes  q:quit "
    default_status = status_msg
    last_refresh = 0.0

    # Task 6: global view mode — 'g' toggles between run-centric and
    # system-overview rendering.  System snapshots are refreshed on the
    # same 3s cadence as the run list to keep the two views in sync.
    mode = "run"
    system_snapshot = None
    last_system_refresh = 0.0

    # Pending async job state (non-blocking)
    pending_job: dict[str, Any] | None = None  # {"job": OperatorJob, "ctx": RunContext, "label": ...}

    while True:
        h, w = stdscr.getmaxyx()
        if h < MIN_HEIGHT or w < MIN_WIDTH:
            stdscr.erase()
            _safe_addstr(stdscr, 0, 0, f"Terminal too small ({w}x{h}). Need {MIN_WIDTH}x{MIN_HEIGHT}.")
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            continue

        # Poll pending job (non-blocking)
        if pending_job is not None:
            try:
                result = poll_job(pending_job["ctx"], pending_job["job"])
                if result.get("status") in ("completed", "failed"):
                    if result.get("status") == "failed":
                        right_lines = [f"Job failed: {result.get('error', 'unknown error')}"]
                    elif pending_job.get("label") == "clarification":
                        right_lines = format_clarification(result.get("result", {}))
                    else:
                        right_lines = format_explanation(result.get("result", {}))
                    status_msg = default_status
                    pending_job = None
                # else: still pending, keep spinner
            except Exception as exc:
                right_lines = [f"Error polling job: {exc}"]
                status_msg = default_status
                pending_job = None

        # Refresh run list periodically
        now = time.time()
        if now - last_refresh > 3.0:
            try:
                runs = collect_runs()
            except Exception:
                runs = []
            last_refresh = now
            if selected_idx >= len(runs):
                selected_idx = max(0, len(runs) - 1)

        # Global-mode snapshot refresh on the same cadence.
        if mode == "global" and now - last_system_refresh > 3.0:
            try:
                from supervisor.operator.system_overview import (
                    load_system_snapshot,
                )

                system_snapshot = load_system_snapshot()
            except Exception:
                system_snapshot = None
            last_system_refresh = now

        # Layout: left=35%, center=35%, right=30%
        left_w = max(w * 35 // 100, 30)
        center_w = max(w * 35 // 100, 25)
        right_w = w - left_w - center_w
        pane_h = h - 1  # leave 1 row for status bar

        # Create sub-windows
        try:
            left_win = stdscr.subwin(pane_h, left_w, 0, 0)
            center_win = stdscr.subwin(pane_h, center_w, 0, left_w)
            right_win = stdscr.subwin(pane_h, right_w, 0, left_w + center_w)
            status_win = stdscr.subwin(1, w, h - 1, 0)
        except curses.error:
            stdscr.erase()
            stdscr.refresh()
            continue

        if mode == "global" and system_snapshot is not None:
            _draw_runs_pane_lines(
                left_win, format_system_banner(system_snapshot)
                + format_system_alerts(system_snapshot.alerts),
                header=" System ",
            )
            _draw_detail_pane(
                center_win, format_actionable_sessions(system_snapshot),
            )
            _draw_right_pane(
                right_win, format_system_timeline(system_snapshot.recent_timeline),
            )
        else:
            _draw_runs_pane(left_win, runs, selected_idx)
            _draw_detail_pane(center_win, detail_lines)
            _draw_right_pane(right_win, right_lines)
        _draw_status_bar(status_win, status_msg[:w - 1])
        curses.doupdate()

        key = stdscr.getch()
        if key == -1:
            # Timeout — just refresh / poll
            continue

        if key in (ord("q"), ord("Q"), 27):
            break

        # g: toggle global / run view.  Force an immediate system-snapshot
        # refresh on entry so the operator isn't staring at stale counts.
        if key in (ord("g"), ord("G")):
            mode = "global" if mode != "global" else "run"
            last_system_refresh = 0.0
            continue

        # Global mode hides the run list; run-action keys below target
        # `selected_idx` against a list the operator cannot see.  Skip
        # them entirely until the operator flips back with `g`.  `l`
        # (language) is a global toggle and stays available.
        if mode == "global" and key != ord("l"):
            continue

        if key in (ord("j"), curses.KEY_DOWN) and runs:
            selected_idx = min(selected_idx + 1, len(runs) - 1)
            detail_lines = ["(loading...)"]
            right_lines = []

        if key in (ord("k"), curses.KEY_UP) and runs:
            selected_idx = max(selected_idx - 1, 0)
            detail_lines = ["(loading...)"]
            right_lines = []

        # Enter or space: load snapshot + timeline
        if key in (10, 32, ord("i")) and runs:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            try:
                result = do_inspect(ctx)
                snap = result.get("snapshot", {})
                detail_lines = format_snapshot(snap) if snap else ["(no snapshot)"]
                tl = result.get("timeline", [])
                detail_lines.extend(format_timeline(tl))
            except Exception as exc:
                detail_lines = [f"Error: {exc}"]

        # e: explain run (always async — never blocks)
        if key == ord("e") and runs and pending_job is None:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            try:
                job = submit_explain(ctx, language=language)
                pending_job = {"job": job, "ctx": ctx, "label": "explain"}
                status_msg = " Explaining... (waiting for result) "
                right_lines = ["(explaining...)"]
            except ActionUnavailable as exc:
                status_msg = f" {exc} "
            except Exception as exc:
                right_lines = [f"Error: {exc}"]

        # x: explain exchange (always async — never blocks)
        if key == ord("x") and runs and pending_job is None:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            try:
                job = submit_explain_exchange(ctx, language=language)
                pending_job = {"job": job, "ctx": ctx, "label": "exchange"}
                status_msg = " Explaining exchange... (waiting for result) "
                right_lines = ["(explaining exchange...)"]
            except ActionUnavailable as exc:
                status_msg = f" {exc} "
            except Exception as exc:
                right_lines = [f"Error: {exc}"]

        # d: drift assessment (always async — never blocks)
        if key == ord("d") and runs and pending_job is None:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            try:
                job = submit_drift(ctx, language=language)
                pending_job = {"job": job, "ctx": ctx, "label": "drift"}
                status_msg = " Assessing drift... (waiting for result) "
                right_lines = ["(assessing drift...)"]
            except ActionUnavailable as exc:
                status_msg = f" {exc} "
            except Exception as exc:
                right_lines = [f"Error: {exc}"]

        # c: clarification — ask a question about the run
        if key == ord("c") and runs and pending_job is None:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            # Use curses line input for question text
            curses.curs_set(1)
            _safe_addstr(status_win, 0, 0, " Ask: " + " " * (w - 7), curses.A_REVERSE)
            status_win.noutrefresh()
            curses.doupdate()
            curses.echo()
            try:
                q_bytes = status_win.getstr(0, 6, w - 7)
                question = q_bytes.decode("utf-8", errors="replace").strip()
            except Exception:
                question = ""
            curses.noecho()
            curses.curs_set(0)
            if question:
                try:
                    job = submit_clarification(ctx, question, language=language)
                    pending_job = {"job": job, "ctx": ctx, "label": "clarification"}
                    status_msg = " Asking... (waiting for answer) "
                    right_lines = ["(asking...)"]
                except ActionUnavailable as exc:
                    status_msg = f" {exc} "
                except Exception as exc:
                    right_lines = [f"Error: {exc}"]
            else:
                status_msg = default_status

        # p: pause
        if key == ord("p") and runs:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            try:
                do_pause(ctx)
                status_msg = f" Paused {run['run_id'][-12:]} "
                last_refresh = 0  # force refresh
            except ActionUnavailable as exc:
                status_msg = f" {exc} "
            except Exception as exc:
                status_msg = f" Pause failed: {exc} "

        # r: resume (auto-starts daemon if needed)
        if key == ord("r") and runs:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            try:
                resp = do_resume(ctx)
                if resp.get("ok"):
                    status_msg = f" Resumed {run['run_id'][-12:]} "
                else:
                    status_msg = f" Resume failed: {resp.get('error', '?')} "
                last_refresh = 0  # force refresh
            except ActionUnavailable as exc:
                status_msg = f" {exc} "
            except Exception as exc:
                status_msg = f" Resume failed: {exc} "

        # l: toggle language (en ↔ zh)
        if key == ord("l"):
            language = "zh" if language == "en" else "en"
            lang_label = "中文" if language == "zh" else "English"
            status_msg = f" Language: {lang_label} "

        # n: operator note (requires daemon)
        if key == ord("n") and runs:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            caps = ctx.capabilities()
            if caps.note_add == ActionMode.UNAVAILABLE:
                status_msg = f" {caps.unavailable_reasons.get('note_add', 'unavailable')} "
            else:
                # Use curses line input for note text
                curses.curs_set(1)
                _safe_addstr(status_win, 0, 0, " Note: " + " " * (w - 8), curses.A_REVERSE)
                status_win.noutrefresh()
                curses.doupdate()
                curses.echo()
                try:
                    note_bytes = status_win.getstr(0, 7, w - 8)
                    note_text = note_bytes.decode("utf-8", errors="replace").strip()
                except Exception:
                    note_text = ""
                curses.noecho()
                curses.curs_set(0)
                if note_text:
                    try:
                        do_note_add(
                            ctx,
                            note_text,
                            title=f"TUI note for {run['run_id'][-12:]}",
                        )
                        status_msg = f" Note saved for {run['run_id'][-12:]} "
                    except ActionUnavailable as exc:
                        status_msg = f" {exc} "
                    except Exception as exc:
                        status_msg = f" Note failed: {exc} "
                else:
                    status_msg = default_status

        # N: view notes for selected run
        if key == ord("N") and runs:
            run = runs[selected_idx]
            ctx = RunContext.from_run_dict(run)
            try:
                notes = do_note_list(ctx)
                right_lines = format_notes(notes)
            except ActionUnavailable as exc:
                right_lines = [f"Notes unavailable: {exc}"]
            except Exception as exc:
                right_lines = [f"Error: {exc}"]


def run_tui():
    """Entry point for the operator TUI."""
    curses.wrapper(_curses_main)
