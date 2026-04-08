"""CLI entry point for thin-supervisor."""
from __future__ import annotations

import argparse
import json
import logging
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

    try:
        final_state = loop.run_sidecar(
            spec, state, terminal,
            poll_interval=config.poll_interval_sec,
            read_lines=config.read_lines,
        )
        print(f"\nRun finished: {final_state.top_state.value}")
        print(json.dumps(final_state.to_dict(), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        store.save(state)
        print(f"\nInterrupted. State saved. Resume with: thin-supervisor run {args.spec_path} --pane {config.pane_target}")

    return 0


def cmd_status(args):
    """Print current run state."""
    config = RuntimeConfig.load(args.config or CONFIG_FILE)
    state_path = Path(config.runtime_dir) / "state.json"
    if not state_path.exists():
        print("No active run found.")
        return 1

    state = json.loads(state_path.read_text())
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
        event["payload"]["checkpoint"] = adapter.parse_checkpoint(event["payload"]["text"])
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

    # status
    p_status = sub.add_parser("status", help="Show current run state")
    p_status.add_argument("--config", default=None, help="Path to config YAML")

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
    elif args.command == "status":
        sys.exit(cmd_status(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
