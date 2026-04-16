"""Operator TUI — first channel implementation.

Three-pane layout:
  Left:   run list
  Center: selected run snapshot + timeline
  Right:  explanation / drift / next action
  Bottom: command line

Uses curses for terminal rendering. Falls back gracefully
if terminal is too small.
"""
from __future__ import annotations

import curses
import time
from typing import Any

from supervisor.daemon.client import DaemonClient
from supervisor.global_registry import list_daemons, list_pane_owners


# ── Data collection ────────────────────────────────────────────────

def collect_runs(daemons: list[dict] | None = None) -> list[dict]:
    """Collect all runs from daemons and foreground pane owners."""
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

    return items


def get_client_for_run(run: dict) -> DaemonClient | None:
    """Get a DaemonClient that can reach this run."""
    sock = run.get("socket", "")
    if sock:
        return DaemonClient(sock_path=sock)
    return None


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
    status_msg = " j/k:nav  e:explain  d:drift  p:pause  r:resume  q:quit "
    default_status = status_msg
    last_refresh = 0.0

    # Pending async job state (non-blocking)
    pending_job: dict[str, Any] | None = None  # {"client": ..., "job_id": ..., "label": ...}

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
                result = pending_job["client"].get_job(pending_job["job_id"])
                if result.get("status") in ("completed", "failed"):
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
            client = get_client_for_run(run)
            if client:
                try:
                    snap_resp = client.get_snapshot(run["run_id"])
                    if snap_resp.get("ok"):
                        detail_lines = format_snapshot(snap_resp)
                    else:
                        detail_lines = [f"Error: {snap_resp.get('error', '?')}"]

                    tl_resp = client.get_timeline(run["run_id"], limit=15)
                    if tl_resp.get("ok"):
                        detail_lines.extend(format_timeline(tl_resp.get("events", [])))
                except Exception as exc:
                    detail_lines = [f"Error: {exc}"]
            else:
                detail_lines = ["(no daemon connection for this run)"]

        # e: explain run (non-blocking)
        if key == ord("e") and runs and pending_job is None:
            run = runs[selected_idx]
            client = get_client_for_run(run)
            if client:
                try:
                    resp = client.explain_run(run["run_id"])
                    if resp.get("ok") and resp.get("job_id"):
                        pending_job = {"client": client, "job_id": resp["job_id"], "label": "explain"}
                        status_msg = " Explaining... (waiting for result) "
                        right_lines = ["(explaining...)"]
                    else:
                        right_lines = [f"Error: {resp.get('error', '?')}"]
                except Exception as exc:
                    right_lines = [f"Error: {exc}"]

        # d: drift assessment (non-blocking)
        if key == ord("d") and runs and pending_job is None:
            run = runs[selected_idx]
            client = get_client_for_run(run)
            if client:
                try:
                    resp = client.assess_drift(run["run_id"])
                    if resp.get("ok") and resp.get("job_id"):
                        pending_job = {"client": client, "job_id": resp["job_id"], "label": "drift"}
                        status_msg = " Assessing drift... (waiting for result) "
                        right_lines = ["(assessing drift...)"]
                    else:
                        right_lines = [f"Error: {resp.get('error', '?')}"]
                except Exception as exc:
                    right_lines = [f"Error: {exc}"]

        # p: pause
        if key == ord("p") and runs:
            run = runs[selected_idx]
            client = get_client_for_run(run)
            if client:
                try:
                    client.stop_run(run["run_id"])
                    status_msg = f" Paused {run['run_id'][-12:]} "
                    last_refresh = 0  # force refresh
                except Exception as exc:
                    status_msg = f" Pause failed: {exc} "

        # r: resume
        if key == ord("r") and runs:
            run = runs[selected_idx]
            status_msg = f" Resume not yet wired for TUI (use CLI) "


def run_tui():
    """Entry point for the operator TUI."""
    curses.wrapper(_curses_main)
