"""CLI entry point for thin-supervisor."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import sys
import time as _time
from pathlib import Path

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.config import RuntimeConfig
from supervisor.adapters.transcript_adapter import TranscriptAdapter
from supervisor.global_registry import find_pane_owner, list_daemons


SUPERVISOR_DIR = ".supervisor"
CONFIG_FILE = ".supervisor/config.yaml"
RUNTIME_DIR = ".supervisor/runtime"
SPECS_DIR = ".supervisor/specs"


# ------------------------------------------------------------------
# init / deinit
# ------------------------------------------------------------------


def cmd_init(args):
    """Create .supervisor/ directory with default config."""
    base = Path(SUPERVISOR_DIR)
    if base.exists() and not args.force:
        print(f"{SUPERVISOR_DIR}/ already exists. Use --force to overwrite.")
        return 1

    base.mkdir(parents=True, exist_ok=True)
    Path(RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
    Path(SPECS_DIR).mkdir(parents=True, exist_ok=True)
    Path(f"{SUPERVISOR_DIR}/clarify").mkdir(parents=True, exist_ok=True)
    Path(f"{SUPERVISOR_DIR}/plans").mkdir(parents=True, exist_ok=True)

    config = RuntimeConfig()
    Path(CONFIG_FILE).write_text(config.default_config_yaml(), encoding="utf-8")

    gitignore = Path(".gitignore")
    if gitignore.exists():
        content = gitignore.read_text()
        if RUNTIME_DIR not in content:
            with gitignore.open("a") as f:
                f.write(f"\n{RUNTIME_DIR}/\n")

    print(f"Initialized {SUPERVISOR_DIR}/")
    return 0


def cmd_deinit(args):
    """Remove .supervisor/ directory."""
    base = Path(SUPERVISOR_DIR)
    if not base.exists():
        print(f"{SUPERVISOR_DIR}/ does not exist.")
        return 1

    if not args.force:
        # Check for active daemon
        from supervisor.daemon.client import DaemonClient
        client = DaemonClient()
        if client.is_running():
            print("Daemon is running. Use 'thin-supervisor daemon stop' first, or --force.")
            return 1

    shutil.rmtree(base)
    print(f"Removed {SUPERVISOR_DIR}/")
    return 0


# ------------------------------------------------------------------
# daemon start / stop
# ------------------------------------------------------------------


def _fork_daemon(config: RuntimeConfig) -> int:
    """Fork a daemon process. Returns child PID to parent, 0 to child (never returns)."""
    from supervisor.daemon.server import DaemonServer

    pid = os.fork()
    if pid > 0:
        return pid  # parent

    # Child — detach
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    log_path = Path(RUNTIME_DIR) / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path), level=logging.INFO, force=True,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    server = DaemonServer(config)
    server.start()
    sys.exit(0)


def cmd_daemon_start(args):
    """Start the supervisor daemon (single process, multi-run)."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if client.is_running():
        pid = client.daemon_pid()
        print(f"Daemon already running (PID {pid}).")
        return 1

    config = RuntimeConfig.load(args.config or CONFIG_FILE)
    pid = _fork_daemon(config)
    print(f"Daemon started (PID {pid})")
    return 0


def cmd_daemon_stop(args):
    """Stop the supervisor daemon."""
    from supervisor.daemon.client import DaemonClient, PID_PATH

    client = DaemonClient()
    pid = client.daemon_pid()
    if not pid:
        print("No daemon PID file found.")
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to daemon (PID {pid})")
        exited = False
        for _ in range(50):
            try:
                os.kill(pid, 0)
                _time.sleep(0.1)
            except ProcessLookupError:
                exited = True
                break
        if exited:
            Path(PID_PATH).unlink(missing_ok=True)
            print("Daemon stopped.")
        else:
            print(f"Warning: PID {pid} did not exit within 5s.")
            return 1
    except ProcessLookupError:
        print(f"Process {pid} not found (already stopped?).")
        Path(PID_PATH).unlink(missing_ok=True)
    except PermissionError:
        print(f"Error: no permission to signal PID {pid}.")
        return 1
    return 0


# ------------------------------------------------------------------
# run register / foreground / stop
# ------------------------------------------------------------------


def _ensure_daemon(config_path: str | None = None) -> "DaemonClient":
    """Ensure daemon is running, auto-start if needed. Returns client."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if client.is_running():
        return client

    print("Daemon not running. Starting...")
    config = RuntimeConfig.load(config_path or CONFIG_FILE)
    _fork_daemon(config)

    for _ in range(30):
        _time.sleep(0.2)
        if client.is_running():
            return client
    print("Error: daemon did not start within 6s.")
    sys.exit(1)


def _resolve_target_and_surface(args, config):
    """Resolve target and surface_type from args + config, with validation."""
    target = getattr(args, "target", None) or getattr(args, "pane", None) or ""
    surface = getattr(args, "surface", None) or getattr(config, "surface_type", "tmux")

    if not target:
        print("Error: --pane or --target is required.")
        return None, None

    # Validate target format for surface type
    if surface == "jsonl" and not (target.endswith(".jsonl") or Path(target).exists()):
        print(f"Warning: jsonl surface expects a .jsonl file path, got '{target}'")
    elif surface == "tmux" and target.endswith(".jsonl"):
        print(f"Warning: tmux surface got a .jsonl path — did you mean --surface jsonl?")

    return target, surface


def cmd_run_register(args):
    """Register a new run with the daemon."""
    config = RuntimeConfig.load(getattr(args, "config", None) or CONFIG_FILE)
    target, surface = _resolve_target_and_surface(args, config)
    if not target:
        return 1
    pane_target = target

    client = _ensure_daemon(args.config)
    spec_path = os.path.abspath(args.spec)
    workspace_root = os.getcwd()

    result = client.register(spec_path, pane_target, workspace_root=workspace_root, surface_type=surface)
    if result.get("ok"):
        print(f"Run registered: {result['run_id']}")
        print(f"  spec: {spec_path}")
        print(f"  target: {pane_target} ({surface})")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
        return 1
    return 0


def cmd_run_foreground(args):
    """Run a single sidecar in foreground (no daemon needed)."""
    spec = load_spec(args.spec)
    config = RuntimeConfig.load(args.config or CONFIG_FILE)

    target, surface_type = _resolve_target_and_surface(args, config)
    if not target:
        return 1
    pane_target = target

    # Per-run isolated directory
    import uuid
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run_dir = str(Path(RUNTIME_DIR) / "runs" / run_id)
    store = StateStore(run_dir)
    state = store.load_or_init(
        spec,
        spec_path=os.path.abspath(args.spec),
        pane_target=pane_target,
        surface_type=surface_type,
        workspace_root=os.getcwd(),
    )

    from supervisor.adapters.surface_factory import create_surface
    terminal = create_surface(surface_type, pane_target)

    diag = terminal.doctor()
    if not diag["ok"]:
        print(f"Surface issues: {diag['issues']}")
        return 1

    from supervisor.domain.models import WorkerProfile
    worker = WorkerProfile(
        provider=config.worker_provider,
        model_name=config.worker_model,
        trust_level=config.worker_trust_level,
    )
    loop = SupervisorLoop(
        store,
        judge_model=config.judge_model,
        judge_temperature=config.judge_temperature,
        judge_max_tokens=config.judge_max_tokens,
        worker_profile=worker,
    )

    print(f"Foreground sidecar: run={run_id} pane={pane_target} spec={spec.id}")

    try:
        final_state = loop.run_sidecar(
            spec, state, terminal,
            poll_interval=config.poll_interval_sec,
            read_lines=config.read_lines,
        )
        print(f"\nRun finished: {final_state.top_state.value}")
    except KeyboardInterrupt:
        store.save(state)
        print("\nInterrupted. State saved.")

    return 0


def cmd_run_resume(args):
    """Resume a paused or crashed run."""
    config = RuntimeConfig.load(getattr(args, "config", None) or CONFIG_FILE)
    target, surface = _resolve_target_and_surface(args, config)
    if not target:
        return 1

    client = _ensure_daemon(getattr(args, "config", None))
    spec_path = os.path.abspath(args.spec)

    result = client.resume(spec_path, target, surface_type=surface)
    if result.get("ok"):
        print(f"Run resumed: {result['run_id']} (from {result.get('resumed_from', '?')})")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
        return 1
    return 0


def cmd_run_review(args):
    """Record reviewer acknowledgement for a paused run."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if not client.is_running():
        print("Daemon not running.")
        return 1

    result = client.ack_review(args.run_id, reviewer=args.by)
    if result.get("ok"):
        print(f"Review recorded for {result['run_id']} by {args.by}")
        print(f"  top_state: {result.get('top_state', 'UNKNOWN')}")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
        return 1
    return 0


def cmd_run_stop(args):
    """Stop a specific run."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if not client.is_running():
        print("Daemon not running.")
        return 1

    result = client.stop_run(args.run_id)
    if result.get("ok"):
        print(f"Run {args.run_id} stopped.")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
        return 1
    return 0


# ------------------------------------------------------------------
# status
# ------------------------------------------------------------------


def _find_local_run_summaries() -> list[dict]:
    """Read persisted local run state outside the daemon's active registry."""
    runtime_dir = Path(RUNTIME_DIR)
    summaries: list[dict] = []

    legacy_state = runtime_dir / "state.json"
    if legacy_state.exists():
        try:
            summaries.append(json.loads(legacy_state.read_text()))
        except json.JSONDecodeError:
            pass

    runs_dir = runtime_dir / "runs"
    if runs_dir.exists():
        for run_dir in sorted(runs_dir.iterdir()):
            state_path = run_dir / "state.json"
            if not state_path.exists():
                continue
            try:
                summaries.append(json.loads(state_path.read_text()))
            except json.JSONDecodeError:
                continue

    return summaries


def _print_local_state_hint() -> None:
    summaries = _find_local_run_summaries()
    if not summaries:
        return

    print("Local state found:")
    for state in summaries:
        print(
            "  "
            f"{state.get('run_id', '?')} "
            f"{state.get('top_state', '?')} "
            f"node={state.get('current_node_id', '') or '?'} "
            f"pane={state.get('pane_target', '?')}"
        )
    print("  These are persisted local state files, not daemon-managed active runs.")


def cmd_list(args):
    """List all active runs with detailed state."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if not client.is_running():
        print("Daemon not running.")
        return 1

    result = client.list_runs()
    if not result.get("ok"):
        print(f"Error: {result.get('error', 'unknown')}")
        return 1

    runs = result.get("runs", [])
    if not runs:
        print("No active runs.")
        _print_local_state_hint()
        return 0

    print(f"{'RUN_ID':<20} {'PANE':<18} {'STATE':<15} {'NODE':<20} {'DONE'}")
    for r in runs:
        done = ", ".join(r.get("done_nodes", [])) or "(none)"
        print(f"{r['run_id']:<20} {r['pane_target']:<18} {r['top_state']:<15} {r.get('current_node', ''):<20} {done}")
    return 0


def _list_global_daemons() -> list[dict]:
    return list_daemons()


def _find_global_pane_owner(pane_target: str) -> dict | None:
    return find_pane_owner(pane_target)


def cmd_ps(args):
    """List all globally registered daemon processes."""
    daemons = _list_global_daemons()
    if not daemons:
        print("No registered daemons.")
        return 0

    print(f"{'PID':<8} {'RUNS':<6} {'STARTED':<28} {'SOCKET':<32} {'CWD'}")
    for daemon in daemons:
        print(
            f"{daemon.get('pid', '?'):<8} "
            f"{daemon.get('active_runs', 0):<6} "
            f"{daemon.get('started_at', ''):<28} "
            f"{daemon.get('socket', ''):<32} "
            f"{daemon.get('cwd', '')}"
        )
    return 0


def cmd_pane_owner(args):
    """Show global owner metadata for a pane lock."""
    owner = _find_global_pane_owner(args.pane)
    if not owner:
        print(f"No owner found for pane {args.pane}.")
        return 1

    print(f"Pane:     {owner.get('pane_target', args.pane)}")
    print(f"Run:      {owner.get('run_id', '?')}")
    print(f"PID:      {owner.get('pid', '?')}")
    print(f"CWD:      {owner.get('cwd', '?')}")
    print(f"Socket:   {owner.get('socket', '?')}")
    print(f"Spec:     {owner.get('spec_path', '?')}")
    print(f"Attached: {owner.get('acquired_at', '?')}")
    return 0


def cmd_observe(args):
    """Read-only observation of a specific run."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if not client.is_running():
        print("Daemon not running.")
        return 1

    result = client.observe(args.run_id)
    if not result.get("ok"):
        print(f"Error: {result.get('error', 'unknown')}")
        return 1

    state = result.get("state", {})
    print(f"Run:     {result['run_id']}")
    print(f"Spec:    {state.get('spec_id', '?')}")
    print(f"State:   {state.get('top_state', '?')}")
    print(f"Node:    {state.get('current_node_id', '?')}")
    print(f"Attempt: {state.get('current_attempt', 0)}")
    done = state.get("done_node_ids", [])
    print(f"Done:    {', '.join(done) if done else '(none)'}")

    events = result.get("recent_events", [])
    if events:
        print(f"\nRecent events ({len(events)}):")
        for e in events:
            print(f"  [{e.get('event_type', '?')}] {e.get('timestamp', '')[:19]}")
    return 0


def cmd_note(args):
    """Shared notes for cross-run collaboration."""
    from supervisor.daemon.client import DaemonClient

    client = DaemonClient()
    if not client.is_running():
        print("Daemon not running. Start with: thin-supervisor daemon start")
        return 1

    if args.note_action == "add":
        content = " ".join(args.content) if args.content else ""
        if not content:
            print("Error: note content required.")
            return 1
        result = client.note_add(
            content,
            note_type=args.type or "context",
            author_run_id=args.run or "human",
        )
        if result.get("ok"):
            print(f"Note added: {result['note_id']}")
        else:
            print(f"Error: {result.get('error', 'unknown')}")
            return 1

    elif args.note_action == "list":
        result = client.note_list(
            note_type=args.type or "",
            run_id=args.run or "",
        )
        if not result.get("ok"):
            print(f"Error: {result.get('error', 'unknown')}")
            return 1
        notes = result.get("notes", [])
        if not notes:
            print("No notes.")
            return 0
        for n in notes:
            print(f"[{n['note_id']}] ({n['note_type']}) {n['timestamp'][:19]}")
            print(f"  by: {n['author_run_id']}")
            print(f"  {n['content'][:120]}")
            print()

    return 0


# ------------------------------------------------------------------
# skill install
# ------------------------------------------------------------------


def cmd_skill_install(args):
    """Auto-detect agent and install appropriate skill."""
    import shutil

    # Try editable install path first, then pip install path
    skill_src = Path(__file__).resolve().parent.parent / "skills"
    if not skill_src.exists():
        # pip install: skills may be in the package data or repo checkout
        # Fall back to downloading from GitHub
        print("Skills not found locally. Install from repo:")
        print("  git clone https://github.com/fakechris/thin-supervisor")
        print("  cp -r thin-supervisor/skills/thin-supervisor-codex ~/.codex/skills/thin-supervisor")
        print("  cp -r thin-supervisor/skills/thin-supervisor ~/.claude/skills/thin-supervisor")
        return 1
    installed = []

    # Codex
    codex_home = Path.home() / ".codex"
    if codex_home.exists():
        dest = codex_home / "skills" / "thin-supervisor"
        src = skill_src / "thin-supervisor-codex"
        if src.exists():
            shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
            installed.append(f"Codex: {dest}")

    # Claude Code
    claude_home = Path.home() / ".claude"
    if claude_home.exists():
        dest = claude_home / "skills" / "thin-supervisor"
        src = skill_src / "thin-supervisor"
        if src.exists():
            shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
            installed.append(f"Claude Code: {dest}")

    if installed:
        print("Skills installed:")
        for i in installed:
            print(f"  ✅ {i}")
        print("\nInvoke with /thin-supervisor in your agent.")
    else:
        print("No agent detected (~/.codex or ~/.claude not found).")
        print("Install manually: cp -r skills/thin-supervisor-codex ~/.codex/skills/thin-supervisor")
        return 1
    return 0


# ------------------------------------------------------------------
# session detect / jsonl / sessions
# ------------------------------------------------------------------


def cmd_session(args):
    """Session detection commands."""
    from supervisor.session_detect import (
        detect_agent,
        detect_session_id,
        find_jsonl_for_session,
        find_latest_jsonl,
        list_sessions,
    )

    if args.session_action == "detect":
        agent = detect_agent()
        sid = detect_session_id(agent)
        if sid:
            print(sid)
        else:
            print(f"error: could not detect session ID (agent={agent})", file=sys.stderr)
            return 1

    elif args.session_action == "jsonl":
        agent = detect_agent()
        sid = detect_session_id(agent)
        path = find_jsonl_for_session(sid, agent) if sid else None
        if path is None:
            path = find_latest_jsonl(agent)
        if path:
            print(str(path))
        else:
            print(f"error: no JSONL transcript found (agent={agent})", file=sys.stderr)
            return 1

    elif args.session_action == "list":
        sessions = list_sessions()
        if not sessions:
            print("No sessions found.")
            return 0
        print(f"{'AGENT':<8} {'CWD':<40} {'MODIFIED'}")
        for s in sessions:
            cwd = s.get("cwd", "") or "(unknown)"
            mod = s.get("modified", "")[:19]
            print(f"{s['agent']:<8} {cwd:<40} {mod}")

    return 0


def cmd_status(args):
    """Show all run states."""
    from supervisor.daemon.client import DaemonClient

    # Try daemon first
    client = DaemonClient()
    if client.is_running():
        result = client.status()
        if result.get("ok"):
            runs = result.get("runs", [])
            if not runs:
                print("Daemon running, no active runs.")
                _print_local_state_hint()
                return 0
            print(f"{'RUN_ID':<20} {'PANE':<20} {'STATE':<18} {'NODE'}")
            for r in runs:
                print(f"{r['run_id']:<20} {r['pane_target']:<20} {r['top_state']:<18} {r.get('current_node', '')}")
            return 0

    # Fallback: scan run directories
    runs_dir = Path(RUNTIME_DIR) / "runs"
    if not runs_dir.exists():
        # Legacy: check old-style state.json
        state_path = Path(RUNTIME_DIR) / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                print(f"Run:   {state.get('run_id', '?')}")
                print(f"State: {state.get('top_state', '?')}")
                print(f"Node:  {state.get('current_node_id', '?')}")
                return 0
            except json.JSONDecodeError:
                print("Error: state.json is corrupt.")
                return 1
        print("No runs found. Daemon not running.")
        return 1

    found = False
    for run_dir in sorted(runs_dir.iterdir()):
        state_path = run_dir / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if not found:
                    print(f"{'RUN_ID':<20} {'PANE':<20} {'STATE':<18} {'NODE'}")
                    found = True
                print(f"{state.get('run_id', '?'):<20} {state.get('pane_target', '?'):<20} {state.get('top_state', '?'):<18} {state.get('current_node_id', '')}")
            except json.JSONDecodeError:
                continue

    if not found:
        print("No runs found.")
        return 1
    return 0


# ------------------------------------------------------------------
# bridge (unchanged)
# ------------------------------------------------------------------


def cmd_bridge(args):
    """Tmux pane operations (read, type, keys, list, id, doctor)."""
    from supervisor.terminal.adapter import TerminalAdapter, TerminalAdapterError

    action = args.bridge_action

    if action == "id":
        pane_id = os.environ.get("TMUX_PANE", "")
        if pane_id:
            print(pane_id)
        else:
            print("error: not inside a tmux pane", file=sys.stderr)
            return 1
        return 0

    if action == "doctor":
        adapter = TerminalAdapter(args.target or "%0")
        info = adapter.doctor()
        print(f"Socket:  {info['socket']}")
        print(f"Panes:   {info['pane_count']}")
        if info["issues"]:
            for issue in info["issues"]:
                print(f"  Issue: {issue}")
        print(f"Status:  {'OK' if info['ok'] else 'ISSUES FOUND'}")
        return 0 if info["ok"] else 1

    if action == "list":
        adapter = TerminalAdapter("%0")
        try:
            panes = adapter.list_panes()
        except TerminalAdapterError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"{'TARGET':<8} {'SESSION:WIN':<15} {'SIZE':<12} {'PROCESS':<12} {'LABEL':<12} {'CWD'}")
        for p in panes:
            print(f"{p.pane_id:<8} {p.session_window:<15} {p.size:<12} {p.process:<12} {p.label or '-':<12} {p.cwd}")
        return 0

    if not args.target:
        print("error: target pane required (label or %id)", file=sys.stderr)
        return 1

    adapter = TerminalAdapter(args.target)

    try:
        if action == "read":
            if args.extra:
                try:
                    lines = int(args.extra[0])
                    if lines <= 0:
                        raise ValueError
                except ValueError:
                    print(f"error: lines must be a positive integer, got '{args.extra[0]}'", file=sys.stderr)
                    return 1
            else:
                lines = 50
            text = adapter.read(lines=lines)
            print(text, end="")
        elif action == "type":
            if not args.extra:
                print("error: text argument required", file=sys.stderr)
                return 1
            adapter.read()
            adapter.type_text(" ".join(args.extra))
        elif action == "keys":
            if not args.extra:
                print("error: key argument(s) required", file=sys.stderr)
                return 1
            adapter.read()
            adapter.send_keys(*args.extra)
        elif action == "name":
            if not args.extra:
                print("error: label argument required", file=sys.stderr)
                return 1
            adapter.name_pane(args.extra[0])
        else:
            print(f"error: unknown bridge action '{action}'", file=sys.stderr)
            return 1
    except TerminalAdapterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


# ------------------------------------------------------------------
# Migration shim for removed legacy syntax: thin-supervisor run <spec> --pane <p> [--daemon]
# ------------------------------------------------------------------


def cmd_run_legacy(args):
    """Legacy entrypoint kept only to print a migration error."""
    print("Legacy run syntax has been removed.")
    print("Use one of:")
    print("  thin-supervisor run register --spec <spec> --pane <pane>")
    print("  thin-supervisor run foreground --spec <spec> --pane <pane>")
    return 1


def _run_event_file(event_file, spec, state, store, loop):
    event = json.loads(Path(event_file).read_text())
    if event.get("type") == "agent_output" and "text" in event.get("payload", {}):
        adapter = TranscriptAdapter()
        cp = adapter.parse_checkpoint(event["payload"]["text"])
        event["payload"]["checkpoint"] = cp.to_dict() if cp else {}
    store.append_event(event)
    loop.handle_event(state, event)
    from supervisor.domain.enums import TopState as TS
    if state.top_state == TS.GATING:
        decision = loop.gate(spec, state)
        store.append_decision(decision.to_dict())
        loop.apply_decision(spec, state, decision)
    if state.top_state == TS.VERIFYING:
        verification = loop.verify_current_node(spec, state)
        store.append_event({"type": "verification_finished", "payload": verification})
        loop.apply_verification(spec, state, verification)
    store.save(state)
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="thin-supervisor",
        description="Thin tmux sidecar supervisor for AI coding agent workflows",
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Initialize .supervisor/ in current project")
    p_init.add_argument("--force", action="store_true")

    # deinit
    p_deinit = sub.add_parser("deinit", help="Remove .supervisor/ directory")
    p_deinit.add_argument("--force", action="store_true")

    # daemon
    p_daemon = sub.add_parser("daemon", help="Manage the supervisor daemon")
    daemon_sub = p_daemon.add_subparsers(dest="daemon_action")
    p_daemon_start = daemon_sub.add_parser("start", help="Start daemon")
    p_daemon_start.add_argument("--config", default=None)
    daemon_sub.add_parser("stop", help="Stop daemon")

    # run (with subcommands)
    p_run = sub.add_parser("run", help="Manage supervisor runs")
    run_sub = p_run.add_subparsers(dest="run_action")

    p_register = run_sub.add_parser("register", help="Register a new run with the daemon")
    p_register.add_argument("--spec", required=True, help="Path to spec YAML")
    p_register.add_argument("--pane", default=None, help="Surface target (tmux pane, oly session, or jsonl path)")
    p_register.add_argument("--target", default=None, help="Alias for --pane")
    p_register.add_argument("--surface", default=None, help="Override surface type (tmux|open_relay|jsonl)")
    p_register.add_argument("--config", default=None)

    p_foreground = run_sub.add_parser("foreground", help="Run sidecar in foreground")
    p_foreground.add_argument("--spec", required=True, help="Path to spec YAML")
    p_foreground.add_argument("--pane", default=None, help="Surface target")
    p_foreground.add_argument("--target", default=None, help="Alias for --pane")
    p_foreground.add_argument("--surface", default=None, help="Override surface type")
    p_foreground.add_argument("--config", default=None)

    p_run_stop = run_sub.add_parser("stop", help="Stop a specific run")
    p_run_stop.add_argument("run_id", help="Run ID to stop")

    p_resume = run_sub.add_parser("resume", help="Resume a paused/crashed run")
    p_resume.add_argument("--spec", required=True, help="Path to spec YAML")
    p_resume.add_argument("--pane", default=None, help="Surface target")
    p_resume.add_argument("--target", default=None, help="Alias for --pane")
    p_resume.add_argument("--surface", default=None, help="Surface type override")
    p_resume.add_argument("--config", default=None)

    p_review = run_sub.add_parser("review", help="Record reviewer acknowledgement")
    p_review.add_argument("run_id", help="Run ID to mark as reviewed")
    p_review.add_argument("--by", required=True, choices=["human", "stronger_reviewer"])

    # Removed legacy syntax is still parsed so we can print a migration error.
    p_run.add_argument("spec_path", nargs="?", default=None, help=argparse.SUPPRESS)
    p_run.add_argument("--pane", default=None, help=argparse.SUPPRESS)
    p_run.add_argument("--config", default=None, help=argparse.SUPPRESS)
    p_run.add_argument("--event-file", default=None, help=argparse.SUPPRESS)
    p_run.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p_run.add_argument("--daemon", "-d", action="store_true", help=argparse.SUPPRESS)

    # list
    sub.add_parser("list", help="List all active runs (detailed)")

    # ps
    sub.add_parser("ps", help="List all registered daemons across worktrees")

    # pane-owner
    p_pane_owner = sub.add_parser("pane-owner", help="Show which run owns a pane")
    p_pane_owner.add_argument("pane", help="tmux pane target")

    # observe
    p_observe = sub.add_parser("observe", help="Read-only observation of a run")
    p_observe.add_argument("run_id", help="Run ID to observe")

    # note
    p_note = sub.add_parser("note", help="Shared notes for cross-run collaboration")
    note_sub = p_note.add_subparsers(dest="note_action")
    p_note_add = note_sub.add_parser("add", help="Add a note")
    p_note_add.add_argument("content", nargs="*", help="Note content")
    p_note_add.add_argument("--type", default="context", help="Note type: context|finding|handoff|warning|question")
    p_note_add.add_argument("--run", default="", help="Author run ID")
    p_note_list = note_sub.add_parser("list", help="List notes")
    p_note_list.add_argument("--type", default="", help="Filter by type")
    p_note_list.add_argument("--run", default="", help="Filter by author run ID")

    # skill
    p_skill = sub.add_parser("skill", help="Skill management")
    skill_sub = p_skill.add_subparsers(dest="skill_action")
    skill_sub.add_parser("install", help="Auto-detect agent and install skill")

    # session
    p_session = sub.add_parser("session", help="Session detection")
    session_sub = p_session.add_subparsers(dest="session_action")
    session_sub.add_parser("detect", help="Detect current session ID")
    session_sub.add_parser("jsonl", help="Find current session JSONL path")
    session_sub.add_parser("list", help="List all discoverable sessions")

    # status (legacy, still works)
    p_status = sub.add_parser("status", help="Show all run states")
    p_status.add_argument("--config", default=None)

    # stop (legacy alias for daemon stop)
    sub.add_parser("stop", help="Stop the supervisor daemon (alias for daemon stop)")

    # bridge
    p_bridge = sub.add_parser("bridge", help="Tmux pane operations")
    p_bridge.add_argument("bridge_action", choices=["read", "type", "keys", "list", "id", "doctor", "name"])
    p_bridge.add_argument("target", nargs="?", default=None)
    p_bridge.add_argument("extra", nargs="*")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.command == "init":
        sys.exit(cmd_init(args))
    elif args.command == "deinit":
        sys.exit(cmd_deinit(args))
    elif args.command == "daemon":
        if args.daemon_action == "start":
            sys.exit(cmd_daemon_start(args))
        elif args.daemon_action == "stop":
            sys.exit(cmd_daemon_stop(args))
        else:
            print("Usage: thin-supervisor daemon {start|stop}")
            sys.exit(1)
    elif args.command == "run":
        if args.run_action == "register":
            sys.exit(cmd_run_register(args))
        elif args.run_action == "foreground":
            sys.exit(cmd_run_foreground(args))
        elif args.run_action == "stop":
            sys.exit(cmd_run_stop(args))
        elif args.run_action == "resume":
            sys.exit(cmd_run_resume(args))
        elif args.run_action == "review":
            sys.exit(cmd_run_review(args))
        elif args.spec_path:
            # Legacy mode
            sys.exit(cmd_run_legacy(args))
        else:
            print("Usage: thin-supervisor run {register|foreground|stop}")
            sys.exit(1)
    elif args.command == "stop":
        sys.exit(cmd_daemon_stop(args))
    elif args.command == "list":
        sys.exit(cmd_list(args))
    elif args.command == "ps":
        sys.exit(cmd_ps(args))
    elif args.command == "pane-owner":
        sys.exit(cmd_pane_owner(args))
    elif args.command == "observe":
        sys.exit(cmd_observe(args))
    elif args.command == "note":
        if args.note_action in ("add", "list"):
            sys.exit(cmd_note(args))
        else:
            print("Usage: thin-supervisor note {add|list}")
            sys.exit(1)
    elif args.command == "skill":
        if args.skill_action == "install":
            sys.exit(cmd_skill_install(args))
        else:
            print("Usage: thin-supervisor skill {install}")
            sys.exit(1)
    elif args.command == "session":
        if args.session_action in ("detect", "jsonl", "list"):
            sys.exit(cmd_session(args))
        else:
            print("Usage: thin-supervisor session {detect|jsonl|list}")
            sys.exit(1)
    elif args.command == "status":
        sys.exit(cmd_status(args))
    elif args.command == "bridge":
        sys.exit(cmd_bridge(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
