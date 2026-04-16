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
import json
import time
from pathlib import Path
from typing import Any

from supervisor.daemon.client import DaemonClient
from supervisor.global_registry import list_daemons, list_known_worktrees, list_pane_owners
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
from supervisor.pause_summary import summarize_state

# Default runtime dir — same as app.py
_RUNTIME_DIR = Path(".supervisor/runtime")


# ── Data collection ────────────────────────────────────────────────

def collect_runs(daemons: list[dict] | None = None) -> list[dict]:
    """Collect all runs from daemons, foreground owners, and local disk state.

    Includes orphaned and completed runs from disk so the TUI covers
    the same scope as the existing CLI status/dashboard commands.
    """
    items: list[dict] = []
    seen: set[str] = set()

    if daemons is None:
        daemons = list_daemons()

    for daemon in daemons:
        sock = daemon.get("socket", "")
        if not sock:
            continue
        try:
            client = DaemonClient(sock_path=sock)
            result = client.status()
            if result.get("ok"):
                for r in result.get("runs", []):
                    rid = r["run_id"]
                    if rid in seen:
                        continue
                    seen.add(rid)
                    items.append({
                        "run_id": rid,
                        "tag": "daemon",
                        "top_state": r.get("top_state", "?"),
                        "current_node": r.get("current_node", ""),
                        "pane_target": r.get("pane_target", "?"),
                        "worktree": daemon.get("cwd", ""),
                        "socket": sock,
                    })
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            pass

    for owner in list_pane_owners():
        if owner.get("controller_mode") != "foreground":
            continue
        rid = owner.get("run_id", "?")
        if rid in seen:
            continue
        seen.add(rid)
        items.append({
            "run_id": rid,
            "tag": "foreground",
            "top_state": "RUNNING",
            "current_node": "",
            "pane_target": owner.get("pane_target", "?"),
            "worktree": owner.get("cwd", ""),
            "socket": "",
        })

    # Scan local disk state for orphaned/completed runs not in daemon registry
    _collect_local_runs(items, seen)

    return items


def _collect_local_runs(items: list[dict], seen: set[str]) -> None:
    """Scan on-disk run directories for orphaned/completed runs.

    Scans the current cwd's runtime dir plus any worktree dirs from
    the global registry (daemons + pane owners).
    """
    runtime_dirs: list[tuple[Path, str]] = [(_RUNTIME_DIR / "runs", "")]
    # Also scan worktrees known from registry
    for daemon in list_daemons():
        wt = daemon.get("cwd", "")
        if wt:
            d = Path(wt) / ".supervisor" / "runtime" / "runs"
            if d.is_dir() and d.resolve() != runtime_dirs[0][0].resolve():
                runtime_dirs.append((d, wt))
    for owner in list_pane_owners():
        wt = owner.get("cwd", "")
        if wt:
            d = Path(wt) / ".supervisor" / "runtime" / "runs"
            if d.is_dir() and not any(d.resolve() == rd.resolve() for rd, _ in runtime_dirs):
                runtime_dirs.append((d, wt))
    # Also scan worktrees from persistent registry (covers dead daemon/pane cases)
    for wt in list_known_worktrees():
        d = Path(wt) / ".supervisor" / "runtime" / "runs"
        if d.is_dir() and not any(d.resolve() == rd.resolve() for rd, _ in runtime_dirs):
            runtime_dirs.append((d, wt))

    for runs_dir, worktree in runtime_dirs:
        if not runs_dir.is_dir():
            continue
        _scan_runs_dir(runs_dir, worktree, items, seen)


def _scan_runs_dir(runs_dir: Path, worktree: str, items: list[dict], seen: set[str]) -> None:
    """Scan a single runs directory for on-disk state files."""
    # Collect run dirs with their state.json mtime for accurate recency sort.
    # Directory mtime doesn't update when state.json content changes.
    run_dirs: list[tuple[Path, float]] = []
    for run_dir in runs_dir.iterdir():
        state_path = run_dir / "state.json"
        if state_path.exists():
            try:
                run_dirs.append((run_dir, state_path.stat().st_mtime))
            except OSError:
                pass
    run_dirs.sort(key=lambda t: t[1], reverse=True)

    for run_dir, _ in run_dirs:
        state_path = run_dir / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rid = state.get("run_id", "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        top = state.get("top_state", "UNKNOWN")
        summary = summarize_state(state)
        # Determine tag
        if top in ("COMPLETED", "FAILED", "ABORTED"):
            tag = "completed"
        elif top in ("RUNNING", "GATING", "VERIFYING"):
            tag = "orphaned"
        elif top == "PAUSED_FOR_HUMAN":
            tag = "paused"
        else:
            tag = "local"
        items.append({
            "run_id": rid,
            "tag": tag,
            "top_state": top,
            "current_node": state.get("current_node_id", ""),
            "pane_target": state.get("pane_target", "?"),
            "worktree": worktree or state.get("workspace_root", ""),
            "socket": "",
            "status_reason": summary.get("status_reason", ""),
        })


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
    status_msg = " j/k:nav  e:explain  x:exchange  d:drift  c:ask  p:pause  r:resume  l:lang  n:note  N:notes  q:quit "
    default_status = status_msg
    last_refresh = 0.0

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
