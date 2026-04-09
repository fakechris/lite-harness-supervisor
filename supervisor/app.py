"""CLI entry point for thin-supervisor."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.config import RuntimeConfig
from supervisor.adapters.transcript_adapter import TranscriptAdapter


SUPERVISOR_DIR = ".supervisor"
CONFIG_FILE = ".supervisor/config.yaml"
RUNTIME_DIR = ".supervisor/runtime"
SPECS_DIR = ".supervisor/specs"


# ------------------------------------------------------------------
# Subcommands
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

    config = RuntimeConfig()
    Path(CONFIG_FILE).write_text(config.default_config_yaml(), encoding="utf-8")

    # Add runtime to .gitignore if present
    gitignore = Path(".gitignore")
    if gitignore.exists():
        content = gitignore.read_text()
        if RUNTIME_DIR not in content:
            with gitignore.open("a") as f:
                f.write(f"\n{RUNTIME_DIR}/\n")

    print(f"Initialized {SUPERVISOR_DIR}/")
    print(f"  config:  {CONFIG_FILE}")
    print(f"  runtime: {RUNTIME_DIR}/")
    print(f"  specs:   {SPECS_DIR}/")
    return 0


def cmd_deinit(args):
    """Remove .supervisor/ directory."""
    base = Path(SUPERVISOR_DIR)
    if not base.exists():
        print(f"{SUPERVISOR_DIR}/ does not exist.")
        return 1

    if not args.force:
        state_file = Path(RUNTIME_DIR) / "state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                if state.get("top_state") not in ("COMPLETED", "FAILED", "ABORTED"):
                    print(f"Active run detected (state={state.get('top_state')}). Use --force to remove anyway.")
                    return 1
            except json.JSONDecodeError:
                print("Warning: state.json is corrupt. Use --force to remove anyway.")
                return 1

    shutil.rmtree(base)
    print(f"Removed {SUPERVISOR_DIR}/")
    return 0


def cmd_run(args):
    """Start the supervisor sidecar loop."""
    spec = load_spec(args.spec_path)
    config = RuntimeConfig.load(args.config or CONFIG_FILE)

    if args.pane:
        config.pane_target = args.pane

    store = StateStore(config.runtime_dir)
    state = store.load_or_init(spec)
    loop = SupervisorLoop(
        store,
        judge_model=config.judge_model,
        judge_temperature=config.judge_temperature,
        judge_max_tokens=config.judge_max_tokens,
    )

    # --event-file mode: process a single event (for testing / offline use)
    if args.event_file:
        return _run_event_file(args.event_file, spec, state, store, loop)

    # --dry-run: just show initial state
    if args.dry_run:
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return 0

    # Live sidecar mode: requires a pane target
    if not config.pane_target:
        print("Error: --pane <target> is required for live sidecar mode.")
        print("  Example: thin-supervisor run plan.yaml --pane codex")
        print("  Use --dry-run to see initial state without connecting to a pane.")
        return 1

    from supervisor.terminal.adapter import TerminalAdapter
    terminal = TerminalAdapter(config.pane_target)

    # Verify tmux connectivity
    diag = terminal.doctor()
    if not diag["ok"]:
        print(f"tmux issues: {diag['issues']}")
        return 1

    print(f"Sidecar started: watching pane '{config.pane_target}' for spec '{spec.id}'")
    print(f"  poll interval: {config.poll_interval_sec}s")
    print(f"  state: {config.runtime_dir}/state.json")

    # Check for existing daemon
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text().strip())
            os.kill(existing_pid, 0)  # check if alive
            print(f"Error: supervisor daemon already running (PID {existing_pid}).")
            print("  Use 'thin-supervisor stop' first.")
            return 1
        except (ProcessLookupError, PermissionError, ValueError):
            pid_path.unlink(missing_ok=True)  # stale or inaccessible PID

    if args.daemon:
        _daemonize()
        # Child continues here — redirect logging to file
        log_path = Path(config.runtime_dir) / "supervisor.log"
        logging.basicConfig(
            filename=str(log_path), level=logging.INFO, force=True,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

    try:
        final_state = loop.run_sidecar(
            spec, state, terminal,
            poll_interval=config.poll_interval_sec,
            read_lines=config.read_lines,
        )
        if not args.daemon:
            print(f"\nRun finished: {final_state.top_state.value}")
            print(json.dumps(final_state.to_dict(), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        store.save(state)
        if not args.daemon:
            print(f"\nInterrupted. State saved. Resume with: thin-supervisor run {args.spec_path} --pane {config.pane_target}")
    finally:
        if args.daemon:
            pid_path.unlink(missing_ok=True)

    return 0


def cmd_status(args):
    """Print current run state."""
    config = RuntimeConfig.load(args.config or CONFIG_FILE)
    state_path = Path(config.runtime_dir) / "state.json"
    if not state_path.exists():
        print("No active run found.")
        return 1

    try:
        state = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        print("Error: state.json is corrupt.")
        return 1
    print(f"Run:     {state.get('run_id', '?')}")
    print(f"Spec:    {state.get('spec_id', '?')}")
    print(f"State:   {state.get('top_state', '?')}")
    print(f"Node:    {state.get('current_node_id', '?')}")
    print(f"Attempt: {state.get('current_attempt', 0)}")
    done = state.get("done_node_ids", [])
    print(f"Done:    {', '.join(done) if done else '(none)'}")
    esc = state.get("human_escalations", [])
    if esc:
        print(f"Escalations: {len(esc)}")
        for e in esc[-3:]:
            print(f"  - {e.get('reason', '?')}")
    return 0


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _run_event_file(event_file: str, spec, state, store, loop):
    """Process a single event from a JSON file (testing / offline mode)."""
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
        store.append_decision(decision)
        loop.apply_decision(spec, state, decision)

    if state.top_state == TS.VERIFYING:
        verification = loop.verify_current_node(spec, state)
        store.append_event({"type": "verification_finished", "payload": verification})
        loop.apply_verification(spec, state, verification)

    store.save(state)
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------
# Bridge subcommand — tmux pane operations
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

    # Commands that need a target
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
            adapter.read()  # satisfy guard
            adapter.type_text(" ".join(args.extra))
        elif action == "keys":
            if not args.extra:
                print("error: key argument(s) required", file=sys.stderr)
                return 1
            adapter.read()  # satisfy guard
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
# Daemon helpers
# ------------------------------------------------------------------

PID_FILE = ".supervisor/runtime/supervisor.pid"


def _daemonize():
    """Fork to background, write PID file."""
    pid = os.fork()
    if pid > 0:
        # Parent — print PID and exit
        print(f"Supervisor daemon started (PID {pid})")
        Path(PID_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(PID_FILE).write_text(str(pid))
        sys.exit(0)
    # Child — detach
    os.setsid()
    # Redirect stdio to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)


def cmd_stop(args):
    """Stop the supervisor daemon."""
    import signal
    import time as _time

    pid_path = Path(PID_FILE)
    if not pid_path.exists():
        print("No daemon PID file found.")
        return 1

    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        print("Error: PID file is corrupt.")
        pid_path.unlink(missing_ok=True)
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}")
        # Wait for process to exit (up to 5s)
        exited = False
        for _ in range(50):
            try:
                os.kill(pid, 0)
                _time.sleep(0.1)
            except ProcessLookupError:
                exited = True
                break
        if exited:
            pid_path.unlink(missing_ok=True)
            print("Daemon stopped.")
        else:
            print(f"Warning: PID {pid} did not exit within 5s. PID file retained.")
            return 1
    except ProcessLookupError:
        print(f"Process {pid} not found (already stopped?).")
        pid_path.unlink(missing_ok=True)
    except PermissionError:
        print(f"Error: no permission to signal PID {pid}.")
        return 1
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

    # run
    p_run = sub.add_parser("run", help="Start supervisor sidecar loop")
    p_run.add_argument("spec_path", help="Path to spec YAML file")
    p_run.add_argument("--pane", default=None, help="tmux pane target (label or %%id)")
    p_run.add_argument("--config", default=None, help="Path to config YAML")
    p_run.add_argument("--event-file", default=None, help="Process a single JSON event (testing)")
    p_run.add_argument("--dry-run", action="store_true", help="Show initial state without starting loop")
    p_run.add_argument("--daemon", "-d", action="store_true", help="Run as background daemon")

    # stop
    sub.add_parser("stop", help="Stop the supervisor daemon")

    # status
    p_status = sub.add_parser("status", help="Show current run state")
    p_status.add_argument("--config", default=None, help="Path to config YAML")

    # bridge
    p_bridge = sub.add_parser("bridge", help="Tmux pane operations")
    p_bridge.add_argument("bridge_action", choices=["read", "type", "keys", "list", "id", "doctor", "name"],
                          help="Bridge action")
    p_bridge.add_argument("target", nargs="?", default=None, help="Pane target (label or %%id)")
    p_bridge.add_argument("extra", nargs="*", help="Additional arguments")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.command == "init":
        sys.exit(cmd_init(args))
    elif args.command == "deinit":
        sys.exit(cmd_deinit(args))
    elif args.command == "run":
        sys.exit(cmd_run(args))
    elif args.command == "stop":
        sys.exit(cmd_stop(args))
    elif args.command == "status":
        sys.exit(cmd_status(args))
    elif args.command == "bridge":
        sys.exit(cmd_bridge(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
