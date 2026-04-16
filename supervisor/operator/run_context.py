"""Unified run resolution and capability matrix for operator channels.

RunContext is the single source of truth for resolving a run's paths,
config, daemon client, and available actions.  CLI, TUI, and future IM
channels all go through this layer instead of reimplementing resolution.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ActionMode(str, Enum):
    """How an operator action executes for a given run type."""
    SYNC_DAEMON = "sync_daemon"
    SYNC_LOCAL = "sync_local"
    ASYNC_DAEMON = "async_daemon"
    ASYNC_LOCAL = "async_local"
    AUTO_START = "auto_start_daemon"
    UNAVAILABLE = "unavailable"


@dataclass
class RunCapabilities:
    """What actions are available for a run, and how they execute."""
    inspect: ActionMode = ActionMode.UNAVAILABLE
    exchange: ActionMode = ActionMode.UNAVAILABLE
    explain: ActionMode = ActionMode.UNAVAILABLE
    drift: ActionMode = ActionMode.UNAVAILABLE
    pause: ActionMode = ActionMode.UNAVAILABLE
    resume: ActionMode = ActionMode.UNAVAILABLE
    note_add: ActionMode = ActionMode.UNAVAILABLE
    note_list: ActionMode = ActionMode.UNAVAILABLE
    unavailable_reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class RunContext:
    """Single source of truth for a run's resolution context.

    Created via ``RunContext.from_run_dict(run)`` where *run* is the dict
    produced by ``collect_runs()`` (TUI) or equivalent CLI logic.
    """
    run_id: str
    worktree: str
    tag: str          # daemon | foreground | orphaned | paused | completed | local
    top_state: str
    pane_target: str
    socket: str       # daemon socket path, or ""
    spec_path: str    # from state.json (lazy-loaded)
    config_path: str  # <worktree>/.supervisor/config.yaml

    # Derived paths
    state_dir: Path = field(default_factory=lambda: Path("."))
    state_path: Path = field(default_factory=lambda: Path("."))
    session_log_path: Path = field(default_factory=lambda: Path("."))

    # ── constructors ──────────────────────────────────────────────

    @classmethod
    def from_run_dict(cls, run: dict[str, Any]) -> RunContext:
        """Build a RunContext from a run dict (as produced by collect_runs)."""
        run_id = run.get("run_id", "")
        worktree = run.get("worktree", "")
        tag = run.get("tag", "local")
        top_state = run.get("top_state", "UNKNOWN")
        pane_target = run.get("pane_target", "")
        socket = run.get("socket", "")

        # Resolve paths from worktree
        if worktree:
            base = Path(worktree)
        else:
            base = Path(".")
        state_dir = base / ".supervisor" / "runtime" / "runs" / run_id
        config_path = str(base / ".supervisor" / "config.yaml")

        # Lazy-load spec_path from state.json
        spec_path = ""
        state_path = state_dir / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                spec_path = state.get("spec_path", "")
                if not pane_target or pane_target == "?":
                    pane_target = state.get("pane_target", "")
            except (json.JSONDecodeError, OSError):
                pass

        return cls(
            run_id=run_id,
            worktree=worktree,
            tag=tag,
            top_state=top_state,
            pane_target=pane_target,
            socket=socket,
            spec_path=spec_path,
            config_path=config_path,
            state_dir=state_dir,
            state_path=state_path,
            session_log_path=state_dir / "session_log.jsonl",
        )

    # ── data access ───────────────────────────────────────────────

    def load_state(self) -> dict[str, Any]:
        """Read state.json from disk."""
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def load_config(self):
        """Load RuntimeConfig scoped to this run's worktree."""
        from supervisor.config import RuntimeConfig
        path = self.config_path if Path(self.config_path).exists() else None
        return RuntimeConfig.load(path)

    # ── daemon discovery ──────────────────────────────────────────

    def get_client(self):
        """Get a DaemonClient that can reach this run, or None.

        Tries the run's own socket first, then falls back to worktree-match
        via the global daemon registry.
        """
        from supervisor.daemon.client import DaemonClient

        # Direct socket
        if self.socket:
            return DaemonClient(sock_path=self.socket)

        # Worktree-match fallback
        if self.worktree:
            return _find_daemon_by_worktree(self.worktree)

        return None

    def _has_daemon(self) -> bool:
        """Check if a daemon is reachable for this run."""
        client = self.get_client()
        if client is None:
            return False
        try:
            return client.is_running()
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return False

    def ensure_daemon(self):
        """Get a running DaemonClient, auto-starting if needed.

        Returns DaemonClient or raises RuntimeError.
        """
        import time as _time

        from supervisor.daemon.client import DaemonClient

        # Try existing daemon
        client = self.get_client()
        if client is not None:
            try:
                if client.is_running():
                    return client
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                pass

        # Auto-start daemon in the run's worktree
        config = self.load_config()
        _fork_daemon_in_worktree(config, self.worktree)

        # Poll for up to 6 seconds
        sock_path = os.path.join(
            self.worktree or ".", ".supervisor", "daemon.sock",
        )
        client = DaemonClient(sock_path=sock_path)
        for _ in range(30):
            _time.sleep(0.2)
            try:
                if client.is_running():
                    return client
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                pass

        raise RuntimeError("Daemon did not start within 6s")

    # ── capability matrix ─────────────────────────────────────────

    def capabilities(self) -> RunCapabilities:
        """Compute the capability matrix for this run."""
        has_daemon = self._has_daemon()
        return _compute_capabilities(self.tag, self.top_state, has_daemon)


# ── module-level helpers ──────────────────────────────────────────


def _find_daemon_by_worktree(worktree: str):
    """Find a running daemon whose cwd matches the given worktree."""
    from supervisor.daemon.client import DaemonClient
    from supervisor.global_registry import list_daemons

    wt_resolved = str(Path(worktree).resolve())
    for daemon in list_daemons():
        daemon_cwd = daemon.get("cwd", "")
        if daemon_cwd and str(Path(daemon_cwd).resolve()) == wt_resolved:
            daemon_sock = daemon.get("socket", "")
            if daemon_sock:
                try:
                    client = DaemonClient(sock_path=daemon_sock)
                    if client.is_running():
                        return client
                except (ConnectionRefusedError, FileNotFoundError, OSError):
                    pass
    return None


def _fork_daemon_in_worktree(config, worktree: str) -> int:
    """Fork a daemon process in the given worktree directory.

    Extracted from app.py:_fork_daemon — same logic, but cwd-aware.
    """
    import logging
    import sys

    from supervisor.daemon.server import DaemonServer

    cwd = worktree or "."
    pid = os.fork()
    if pid > 0:
        return pid  # parent

    # Child — detach
    os.setsid()
    os.chdir(cwd)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    log_path = Path(".supervisor") / "runtime" / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path), level=logging.INFO, force=True,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    server = DaemonServer(config)
    server.start()
    sys.exit(0)


def _compute_capabilities(
    tag: str, top_state: str, has_daemon: bool,
) -> RunCapabilities:
    """Hardcoded capability matrix — one place, all run types."""
    caps = RunCapabilities()
    reasons: dict[str, str] = {}

    if tag == "daemon":
        # Daemon-managed run (active or other-worktree) — full daemon access
        caps.inspect = ActionMode.SYNC_DAEMON
        caps.exchange = ActionMode.SYNC_DAEMON
        caps.explain = ActionMode.ASYNC_DAEMON
        caps.drift = ActionMode.ASYNC_DAEMON
        caps.pause = ActionMode.SYNC_DAEMON
        caps.resume = ActionMode.SYNC_DAEMON
        caps.note_add = ActionMode.SYNC_DAEMON
        caps.note_list = ActionMode.SYNC_DAEMON

    elif tag == "foreground":
        caps.inspect = ActionMode.SYNC_LOCAL
        caps.exchange = ActionMode.SYNC_LOCAL
        caps.explain = ActionMode.ASYNC_LOCAL
        caps.drift = ActionMode.ASYNC_LOCAL
        caps.pause = ActionMode.UNAVAILABLE
        caps.resume = ActionMode.UNAVAILABLE
        caps.note_add = ActionMode.UNAVAILABLE
        caps.note_list = ActionMode.UNAVAILABLE
        reasons["pause"] = "foreground run"
        reasons["resume"] = "foreground run"
        reasons["note_add"] = "no daemon"
        reasons["note_list"] = "no daemon"

    elif tag == "orphaned":
        if has_daemon:
            caps.inspect = ActionMode.SYNC_DAEMON
            caps.exchange = ActionMode.SYNC_DAEMON
            caps.explain = ActionMode.ASYNC_DAEMON
            caps.drift = ActionMode.ASYNC_DAEMON
            caps.pause = ActionMode.SYNC_DAEMON
            caps.resume = ActionMode.SYNC_DAEMON
            caps.note_add = ActionMode.SYNC_DAEMON
            caps.note_list = ActionMode.SYNC_DAEMON
        else:
            caps.inspect = ActionMode.SYNC_LOCAL
            caps.exchange = ActionMode.SYNC_LOCAL
            caps.explain = ActionMode.ASYNC_LOCAL
            caps.drift = ActionMode.ASYNC_LOCAL
            caps.pause = ActionMode.UNAVAILABLE
            caps.resume = ActionMode.AUTO_START
            caps.note_add = ActionMode.UNAVAILABLE
            caps.note_list = ActionMode.UNAVAILABLE
            reasons["pause"] = "no daemon"
            reasons["note_add"] = "no daemon"
            reasons["note_list"] = "no daemon"

    elif tag == "paused":
        if has_daemon:
            caps.inspect = ActionMode.SYNC_DAEMON
            caps.exchange = ActionMode.SYNC_DAEMON
            caps.explain = ActionMode.ASYNC_DAEMON
            caps.drift = ActionMode.ASYNC_DAEMON
            caps.pause = ActionMode.UNAVAILABLE
            caps.resume = ActionMode.SYNC_DAEMON
            caps.note_add = ActionMode.SYNC_DAEMON
            caps.note_list = ActionMode.SYNC_DAEMON
            reasons["pause"] = "already paused"
        else:
            caps.inspect = ActionMode.SYNC_LOCAL
            caps.exchange = ActionMode.SYNC_LOCAL
            caps.explain = ActionMode.ASYNC_LOCAL
            caps.drift = ActionMode.ASYNC_LOCAL
            caps.pause = ActionMode.UNAVAILABLE
            caps.resume = ActionMode.AUTO_START
            caps.note_add = ActionMode.UNAVAILABLE
            caps.note_list = ActionMode.UNAVAILABLE
            reasons["pause"] = "no daemon"
            reasons["note_add"] = "no daemon"
            reasons["note_list"] = "no daemon"

    elif tag == "completed":
        caps.inspect = ActionMode.SYNC_LOCAL
        caps.exchange = ActionMode.SYNC_LOCAL
        caps.explain = ActionMode.ASYNC_LOCAL
        caps.drift = ActionMode.ASYNC_LOCAL
        caps.pause = ActionMode.UNAVAILABLE
        caps.resume = ActionMode.UNAVAILABLE
        caps.note_add = ActionMode.UNAVAILABLE
        caps.note_list = ActionMode.UNAVAILABLE
        reasons["pause"] = "completed"
        reasons["resume"] = "completed"
        reasons["note_add"] = "no daemon"
        reasons["note_list"] = "no daemon"

    else:
        # "local" or unknown tag — treat like completed
        caps.inspect = ActionMode.SYNC_LOCAL
        caps.exchange = ActionMode.SYNC_LOCAL
        caps.explain = ActionMode.ASYNC_LOCAL
        caps.drift = ActionMode.ASYNC_LOCAL
        caps.pause = ActionMode.UNAVAILABLE
        caps.resume = ActionMode.UNAVAILABLE
        caps.note_add = ActionMode.UNAVAILABLE
        caps.note_list = ActionMode.UNAVAILABLE
        reasons["pause"] = "no daemon"
        reasons["resume"] = "local run"
        reasons["note_add"] = "no daemon"
        reasons["note_list"] = "no daemon"

    caps.unavailable_reasons = reasons
    return caps


class ActionUnavailable(Exception):
    """Raised when an operator action is not supported for the current run."""
    pass
