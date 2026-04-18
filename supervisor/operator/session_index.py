"""Canonical global session collector.

Produces one normalized SessionRecord per run across every discoverable
worktree.  Every operator read surface (status, dashboard, tui, observe)
must consume this collector so they all see the same session universe.

See docs/plans/2026-04-16-global-observability-plane-for-per-worktree-runtime.md.

Discovery sources (union, deduped by resolved path):
  1. current cwd
  2. `list_known_worktrees()` — persists across daemon/pane shutdown
  3. live daemon registry cwds
  4. live pane-owner registry cwds
  5. `git worktree list` for the current repo, when available (read-only)

Liveness classification:
  - is_live       — a daemon owns this worktree, OR a pane owner holds
                    this run's pane lock
  - is_completed  — top_state in {COMPLETED, FAILED, ABORTED}
  - is_orphaned   — persisted in an active-ish state without an owner

Read-only: the collector never mutates state, never heals runs, never
writes files.  It only surfaces what already exists on disk.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from supervisor.event_plane.store import EventPlaneStore
from supervisor.event_plane.surface import summarize_for_session
from supervisor.global_registry import (
    list_daemons,
    list_known_worktrees,
    list_pane_owners,
)
from supervisor.pause_summary import summarize_state

_RUNTIME_SUBPATH = Path(".supervisor") / "runtime" / "runs"

_LIVE_STATES = {"RUNNING", "GATING", "VERIFYING", "ATTACHED", "RECOVERY_NEEDED"}
_COMPLETED_STATES = {"COMPLETED", "FAILED", "ABORTED"}
# States that are "actionable" — i.e., persisted state the operator can
# still act on.  A run in one of these states without a live controller
# counts as orphaned.  Paused runs are explicitly actionable: the plan's
# incident shape (`run_89576d49897f` paused in a child worktree after
# daemon idle shutdown) must appear as orphaned from root cwd.  ATTACHED
# and RECOVERY_NEEDED are actionable too: a daemon crash mid-attach or
# mid-recovery leaves the run in a state the operator must resume or
# inspect, not hide.
_ACTIONABLE_ORPHAN_STATES = _LIVE_STATES | {"PAUSED_FOR_HUMAN"}


@dataclass(frozen=True)
class SessionRecord:
    """Normalized session record consumed by every operator read surface."""

    run_id: str
    worktree_root: str
    spec_path: str
    controller_mode: str  # "daemon" | "foreground" | "local"
    top_state: str
    current_node: str
    pane_target: str
    daemon_socket: str
    is_live: bool
    is_orphaned: bool
    is_completed: bool
    pause_reason: str
    next_action: str
    last_checkpoint_summary: str
    last_update_at: str
    surface_type: str = ""  # persisted surface ("tmux", "jsonl", "open_relay", …)
    tag: str = ""  # derived display tag; see _derive_tag
    pause_class: str = ""  # business|safety|review|recovery when top_state==PAUSED_FOR_HUMAN
    session_id: str = ""  # persisted session_id (outlives run_id across replay)
    # Passive event-plane summary for this session (see
    # ``event_plane/surface.py``).  Dict-shaped to avoid pulling the
    # operator.models dataclass into the session_index module; callers
    # that want a typed handle can lift it with
    # ``RunEventPlaneSummary(**record.event_plane)``.  ``None`` when the
    # session has no event-plane record or the store is unreadable.
    event_plane: dict | None = None

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


# ── Worktree discovery ──────────────────────────────────────────


def _discover_git_worktrees(cwd: str) -> list[str]:
    """Best-effort: list git worktrees linked to the current repo.

    Read-only.  Returns empty list on any failure (no git, not a repo,
    permission denied).  Enumerates paths only — does not run fetch,
    status, or any other side-effecting subcommand.
    """
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(line[len("worktree "):].strip())
    return paths


def _find_enclosing_worktree_root(cwd: str) -> str:
    """Walk upward from cwd to the nearest ancestor holding a runs dir.

    Operators often invoke commands from a subdirectory of the worktree
    (e.g., `src/` or `tests/`).  Without this climb, `--local` from a
    subdirectory would scan an empty path and return nothing.  Falls
    back to the resolved cwd when no ancestor qualifies, preserving the
    existing behavior for fresh worktrees where `.supervisor/` has not
    yet been created.
    """
    try:
        cur = Path(cwd).resolve()
    except (OSError, RuntimeError):
        return cwd
    start = cur
    while True:
        if (cur / _RUNTIME_SUBPATH).is_dir():
            return str(cur)
        parent = cur.parent
        if parent == cur:
            return str(start)
        cur = parent


def _resolved_worktree_roots(
    *, local_only: bool, cwd: str,
    daemons: list[dict], pane_owners: list[dict],
) -> list[Path]:
    """Build the deduped, resolved union of worktree roots to scan."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(raw: str) -> None:
        if not raw:
            return
        try:
            resolved = Path(raw).resolve()
        except (OSError, RuntimeError):
            return
        if resolved in seen:
            return
        seen.add(resolved)
        roots.append(resolved)

    _add(_find_enclosing_worktree_root(cwd))
    if local_only:
        return roots

    for wt in list_known_worktrees():
        _add(wt)
    for daemon in daemons:
        _add(daemon.get("cwd", ""))
    for owner in pane_owners:
        _add(owner.get("cwd", ""))
    for wt in _discover_git_worktrees(cwd):
        _add(wt)
    return roots


# ── Per-worktree scanning ───────────────────────────────────────


def _iter_run_states(worktree: Path):
    """Yield (run_id, state_dict, last_update_at) for every run under worktree."""
    runs_dir = worktree / _RUNTIME_SUBPATH
    if not runs_dir.is_dir():
        return
    entries: list[tuple[Path, float]] = []
    try:
        children = list(runs_dir.iterdir())
    except OSError:
        return
    for run_dir in children:
        state_path = run_dir / "state.json"
        if not state_path.exists():
            continue
        try:
            mtime = state_path.stat().st_mtime
        except OSError:
            continue
        entries.append((state_path, mtime))
    entries.sort(key=lambda t: t[1], reverse=True)
    for state_path, mtime in entries:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run_id = state.get("run_id", "")
        if not run_id:
            continue
        last_update_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        yield run_id, state, last_update_at


# ── Classification ──────────────────────────────────────────────


def _derive_tag(*, is_live: bool, is_completed: bool, is_orphaned: bool,
                top_state: str, controller_mode: str,
                pause_class: str = "") -> str:
    # Paused trumps liveness: a paused run needs operator attention, so
    # the tag must call that out even when a daemon is still supervising
    # it.  Completed trumps everything else (terminal state is stable).
    if is_completed:
        return "completed"
    if top_state == "PAUSED_FOR_HUMAN":
        # Split by pause_class so operators can distinguish business
        # blockers from recovery exhaustion at a glance.  `paused` alone
        # remains the fallback when no class is recorded (legacy runs).
        return f"paused-{pause_class}" if pause_class else "paused"
    # Attach-boundary and recovery states take precedence over the
    # live-controller label: operators watching `status` need to see
    # "attached; awaiting first execution" / "supervisor recovering" even
    # when the daemon owns the run.
    if top_state == "ATTACHED":
        return "attached"
    if top_state == "RECOVERY_NEEDED":
        return "recovery"
    if is_live:
        return "daemon" if controller_mode == "daemon" else "foreground"
    if is_orphaned:
        return "orphaned"
    return "local"


def _liveness(
    run_id: str,
    worktree: Path,
    top_state: str,
    *,
    daemon_by_cwd: dict[Path, dict],
    fg_runs: dict[str, dict],
) -> tuple[bool, bool, bool, str]:
    """Return (is_live, is_orphaned, is_completed, daemon_socket).

    A daemon that owns a worktree supervises every run in that worktree,
    including paused ones — so paused runs must retain their
    daemon_socket when a daemon is present.  Without this, operator
    commands (inspect, resume, explain) cannot reach the daemon for a
    paused run, which is exactly the most common command target.

    A run is orphaned when it is in any actionable state (RUNNING,
    GATING, VERIFYING, or PAUSED_FOR_HUMAN) but no controller owns it.
    Paused runs without a live daemon still need operator attention;
    they must surface as orphaned, not hidden.
    """
    if top_state in _COMPLETED_STATES:
        return False, False, True, ""

    fg = fg_runs.get(run_id)
    if fg is not None and fg.get("controller_mode") == "foreground":
        return True, False, False, ""

    daemon = daemon_by_cwd.get(worktree)
    if daemon is not None:
        return True, False, False, daemon.get("socket", "")

    is_orphaned = top_state in _ACTIONABLE_ORPHAN_STATES
    return False, is_orphaned, False, ""


# ── Public API ──────────────────────────────────────────────────


def collect_sessions(*, local_only: bool = False) -> list[SessionRecord]:
    """Return one SessionRecord per run across every discoverable worktree.

    `local_only=True` restricts to the current cwd (skipping
    known_worktrees, daemon cwds, pane-owner cwds, and git worktrees).
    """
    cwd = os.getcwd()
    # Snapshot live registries once to avoid TOCTOU: a daemon that was
    # present during worktree discovery must still be counted during
    # liveness classification, or its runs would spuriously surface as
    # orphaned if the daemon exits between the two reads.
    daemons = list_daemons()
    pane_owners = list_pane_owners()
    roots = _resolved_worktree_roots(
        local_only=local_only, cwd=cwd,
        daemons=daemons, pane_owners=pane_owners,
    )

    daemon_by_cwd: dict[Path, dict] = {}
    for daemon in daemons:
        raw = daemon.get("cwd", "")
        if not raw:
            continue
        try:
            daemon_by_cwd[Path(raw).resolve()] = daemon
        except (OSError, RuntimeError):
            continue

    fg_runs: dict[str, dict] = {}
    for owner in pane_owners:
        rid = owner.get("run_id", "")
        if rid:
            fg_runs[rid] = owner

    records: list[SessionRecord] = []
    seen: set[str] = set()
    # One EventPlaneStore per worktree.  `collect_sessions` is a
    # read-only scan, so we cache the store by runtime root to avoid
    # reopening it for every run in the same worktree.
    ep_store_cache: dict[Path, EventPlaneStore] = {}

    for worktree in roots:
        runtime_root = worktree / ".supervisor" / "runtime"
        for run_id, state, last_update_at in _iter_run_states(worktree):
            if run_id in seen:
                continue
            seen.add(run_id)
            top_state = state.get("top_state", "UNKNOWN")
            persisted_mode = state.get("controller_mode", "") or "local"
            is_live, is_orphaned, is_completed, socket = _liveness(
                run_id,
                worktree,
                top_state,
                daemon_by_cwd=daemon_by_cwd,
                fg_runs=fg_runs,
            )
            # Prefer the live snapshot over persisted state: a stale
            # controller_mode in state.json must not override who actually
            # owns the run right now, or status/dashboard/tui would
            # mis-bucket live runs.
            if run_id in fg_runs and fg_runs[run_id].get("controller_mode") == "foreground":
                controller_mode = "foreground"
            elif worktree in daemon_by_cwd:
                controller_mode = "daemon"
            else:
                controller_mode = persisted_mode
            summary = summarize_state(state)
            pclass = summary.get("pause_class", "") or ""
            tag = _derive_tag(
                is_live=is_live,
                is_completed=is_completed,
                is_orphaned=is_orphaned,
                top_state=top_state,
                controller_mode=controller_mode,
                pause_class=pclass,
            )
            session_id = state.get("session_id", "") or ""
            ep_summary: dict | None = None
            if session_id:
                try:
                    ep = ep_store_cache.get(runtime_root)
                    if ep is None:
                        ep = EventPlaneStore(str(runtime_root))
                        ep_store_cache[runtime_root] = ep
                    ep_summary = summarize_for_session(ep, session_id)
                except OSError:
                    ep_summary = None
            records.append(
                SessionRecord(
                    run_id=run_id,
                    worktree_root=str(worktree),
                    spec_path=state.get("spec_path", ""),
                    controller_mode=controller_mode,
                    top_state=top_state,
                    current_node=state.get("current_node_id", ""),
                    pane_target=state.get("pane_target", ""),
                    daemon_socket=socket,
                    is_live=is_live,
                    is_orphaned=is_orphaned,
                    is_completed=is_completed,
                    pause_reason=summary.get("pause_reason", ""),
                    next_action=summary.get("next_action", ""),
                    last_checkpoint_summary=summary.get("status_reason", ""),
                    last_update_at=last_update_at,
                    surface_type=state.get("surface_type", "") or "",
                    tag=tag,
                    pause_class=pclass,
                    session_id=session_id,
                    event_plane=ep_summary,
                )
            )
    # Global recency sort: the operator asking "what's running?" cares
    # about the most recently touched sessions first, regardless of
    # which worktree they live in.  Without this, a stale run in cwd
    # would mask a hot run in a child worktree.
    records.sort(key=lambda r: r.last_update_at, reverse=True)
    return records


def find_session(run_id: str) -> SessionRecord | None:
    """Return the canonical record for `run_id` or None if not found."""
    if not run_id:
        return None
    for rec in collect_sessions():
        if rec.run_id == run_id:
            return rec
    return None
