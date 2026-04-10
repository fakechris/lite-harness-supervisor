# thin-supervisor

**Long-running AI coding tasks fail silently.** The agent asks "should I continue?", you're not watching, and the task stalls. Or worse — the agent says "done" but didn't actually pass the tests.

thin-supervisor fixes this. It sits alongside your existing coding agent (Claude Code, Codex, or any CLI agent) in a tmux pane, watches what the agent does, and makes structured decisions: continue, verify, retry, branch, escalate, or finish. You stay in your familiar agent UI. The supervisor handles the rest.

```text
┌────────────────────────────┐  ┌──────────────────────────┐
│  Your Agent (visible pane) │  │  Supervisor (sidecar)    │
│  Claude Code / Codex       │  │  reads pane output       │
│                            │  │  parses checkpoints      │
│  ... working ...           │  │  gates decisions         │
│                            │  │  runs verifiers          │
│  <checkpoint>              │──│  injects next step       │
│  status: step_done         │  │                          │
│  </checkpoint>             │  │  state: RUNNING → VERIFY │
└────────────────────────────┘  └──────────────────────────┘
                 tmux session
```

## When to use this

| Scenario | Without supervisor | With supervisor |
|----------|-------------------|-----------------|
| 10-step implementation plan | Agent asks permission at every step | Runs to completion, verifies each step |
| Test-driven workflow | Agent says "done" without running tests | Verifier runs tests, rejects if failing |
| Agent asks "should I continue?" | You miss it, task stalls for hours | Supervisor auto-answers, keeps going |
| Dangerous operation detected | Agent proceeds silently | Supervisor escalates to you |

## Core Concepts

thin-supervisor is built around 6 first-class objects:

| Object | Question it answers | What it is |
|--------|-------------------|------------|
| **WorkflowSpec** | What should be done? | YAML task definition with steps, verification criteria, and finish policy |
| **SessionRun** | Who is this run? | Identity + durable event history — survives crashes, enables recovery |
| **ExecutionSurface** | How do we talk to the agent? | Protocol for read/inject/cwd — tmux is the first implementation |
| **CheckpointEvent** | What did the agent just report? | Structured status with seq tracking, evidence, and needs |
| **SupervisorDecision** | What does the control plane think? | Typed gate decision with confidence, reasoning, and causality link |
| **HandoffInstruction** | What should the agent do next? | Composed instruction with full traceability to the triggering decision |

These form a **causality chain**: every instruction traces back to the decision that caused it, which traces back to the checkpoint that triggered it.

```text
CheckpointEvent(seq=3) → SupervisorDecision(triggered_by_seq=3) → HandoffInstruction(triggered_by_decision=X)
```

## Quick Start

> **Full guide**: See [docs/getting-started.md](docs/getting-started.md) for step-by-step instructions covering tmux/open-relay + Codex/Claude/OpenCode/Droid.

```bash
# Install
pip install thin-supervisor

# Initialize in your project
cd your-project
thin-supervisor init

# Write a spec (or let the Skill generate one)
cat > .supervisor/specs/my-plan.yaml << 'EOF'
kind: linear_plan
id: my_feature
goal: implement feature X with tests
finish_policy:
  require_all_steps_done: true
  require_verification_pass: true
policy:
  default_continue: true
  max_retries_per_node: 3

steps:
  - id: write_tests
    type: task
    objective: write failing tests for feature X
    verify:
      - type: artifact
        path: tests/test_feature_x.py
        exists: true

  - id: implement
    type: task
    objective: implement feature X until tests pass
    verify:
      - type: command
        run: pytest -q tests/test_feature_x.py
        expect: pass

  - id: final_check
    type: task
    objective: run full test suite
    verify:
      - type: command
        run: pytest -q
        expect: pass
EOF

# Start your agent in a tmux pane, then start the supervisor
thin-supervisor run .supervisor/specs/my-plan.yaml --pane agent --daemon
```

## What happens next

1. Supervisor reads the agent's pane output every 2 seconds
2. Agent emits a `<checkpoint>` block after completing work
3. Supervisor parses the checkpoint and makes a gate decision:
   - **Continue** — agent is making progress, don't interrupt
   - **Verify** — agent says step is done, run the verifier
   - **Retry** — verification failed, inject retry instruction with failure details
   - **Branch** — decision node in workflow, select a path
   - **Escalate** — missing credentials, dangerous action, or low confidence — pause for human
   - **Finish** — all steps done, all verifiers pass, finish policy satisfied
4. If continuing or retrying, supervisor injects the next instruction into the pane
5. Everything is logged to `session_log.jsonl` — append-only, durable, recoverable

## Checkpoint Protocol

Agents must emit structured checkpoints for the supervisor to parse:

```text
<checkpoint>
run_id: <run_id from thin-supervisor status>
checkpoint_seq: <incrementing integer, start from 1>
status: working | blocked | step_done | workflow_done
current_node: <step_id>
summary: <one-line description>
evidence:
  - modified: <file path>
  - ran: <command>
  - result: <short result>
candidate_next_actions:
  - <next action>
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
```

The Codex/Claude Code Skills teach agents this protocol automatically.

## Verification Types

| Type | Fields | Description |
|------|--------|-------------|
| `command` | `run`, `expect` | Run a shell command. `expect`: `pass`, `fail`, `contains:<text>` |
| `artifact` | `path`, `exists` | Check if a file exists |
| `git` | `check`, `expect` | Check git state (e.g., `check: dirty`, `expect: false`) |
| `workflow` | `require_node_done` | Check if current node is marked done |

All verifiers run in the agent's working directory (pane cwd), not the supervisor's.

## CLI

```bash
thin-supervisor init [--force]          # Create .supervisor/ directory
thin-supervisor run <spec> --pane <target> [--daemon]  # Start sidecar
thin-supervisor stop                    # Stop daemon
thin-supervisor status                  # Show current run state
thin-supervisor deinit [--force]        # Remove .supervisor/
thin-supervisor bridge <action> [args]  # Tmux pane operations
```

### Bridge subcommands

```bash
thin-supervisor bridge read <pane> [lines]   # Capture pane output
thin-supervisor bridge type <pane> <text>     # Send text (no Enter)
thin-supervisor bridge keys <pane> <key>...   # Send special keys
thin-supervisor bridge list                   # Show all panes
thin-supervisor bridge id                     # Current pane ID
thin-supervisor bridge doctor                 # Check tmux connectivity
```

## Configuration

`.supervisor/config.yaml`:

```yaml
pane_target: "agent"              # tmux pane label or %id
poll_interval_sec: 2.0            # seconds between pane reads
read_lines: 100                   # terminal lines to capture

# LLM judge (null = offline stub mode, rules-only)
judge_model: null                 # e.g., anthropic/claude-haiku-4-5-20251001
judge_temperature: 0.1
judge_max_tokens: 512
```

Override with environment variables: `SUPERVISOR_PANE_TARGET`, `SUPERVISOR_JUDGE_MODEL`, etc.

## Design Philosophy

Inspired by [Anthropic's Scaling Managed Agents](https://www.anthropic.com/engineering/managed-agents):

1. **The system's memory lives in SessionRun, not in the model's context.** Crashes don't lose history. Everything is in `session_log.jsonl`.

2. **The execution surface is just a "hand", not the system.** Today it's tmux. Tomorrow it could be a PTY wrapper or a remote session. The `SessionAdapter` protocol keeps the supervisor decoupled.

3. **Harnesses change, primitives don't.** The current sidecar loop is one harness. The 6 first-class objects (WorkflowSpec, SessionRun, ExecutionSurface, CheckpointEvent, SupervisorDecision, HandoffInstruction) are the stable interface.

4. **Verification is deterministic, not verbal.** "Done" means the verifier passed, not that the agent said so.

## Skill Integration

Install for Claude Code:
```bash
cp -r skills/lh-supervisor ~/.claude/skills/
```

Install for Codex:
```bash
cp -r skills/lh-supervisor-codex ~/.codex/skills/lh-supervisor
```

Invoke with `/lh-supervisor` to auto-generate a spec and start supervised execution.

## Development

```bash
git clone https://github.com/fakechris/lite-harness-supervisor
cd lite-harness-supervisor
pip install -e ".[dev]"
pytest -q  # 74 tests
```

## License

MIT
