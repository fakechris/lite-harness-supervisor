# Getting Started

This guide walks you through setting up thin-supervisor from scratch. Pick the combination that matches your setup:

- **Execution Surface**: tmux (default) or open-relay
- **AI Agent**: Codex, Claude Code, OpenCode, Droid, or any CLI agent

---

## Step 0: Install thin-supervisor

```bash
pip install thin-supervisor

# Verify
thin-supervisor --help
```

You should see: `{init,deinit,daemon,run,stop,status,bridge}`

---

## Part A: tmux + Codex

The most common setup. Codex runs in a visible tmux pane, supervisor watches from the side.

### 1. Install the Codex Skill

```bash
# From the thin-supervisor repo
cp -r skills/lh-supervisor-codex ~/.codex/skills/lh-supervisor
```

This teaches Codex the checkpoint protocol and the 4-stage workflow (Clarify → Plan → Approve → Execute).

### 2. Start tmux

```bash
tmux new -s work
```

### 3. Initialize supervisor in your project

```bash
cd your-project
thin-supervisor init
```

This creates `.supervisor/` with config, specs, and runtime directories.

### 4. Start the supervisor daemon

```bash
thin-supervisor daemon start
```

The daemon runs in the background, ready to accept runs from any tmux session.

### 5. Launch Codex and invoke the skill

In the tmux pane:

```bash
codex
```

Inside Codex, say something like:

> Implement a login feature with tests. Use /lh-supervisor to run this as a supervised long task.

Or invoke the skill directly:

> /lh-supervisor

The Skill will:
1. Clarify your requirements (if vague)
2. Generate a spec YAML with verification steps
3. Self-review the plan (architect + critic passes)
4. Ask you to approve
5. Register the run with the daemon: `thin-supervisor run register --spec ... --pane ...`
6. Start emitting checkpoints as it works

### 6. Monitor

In another terminal:

```bash
# See all active runs
thin-supervisor status

# Watch the daemon log
tail -f .supervisor/runtime/daemon.log

# Read what the agent is outputting
thin-supervisor bridge read work:0 50
```

### 7. What happens during execution

```
Agent works on step 1
    ↓ emits <checkpoint status: step_done ...>
Supervisor reads pane, parses checkpoint
    ↓ gate decision: VERIFY_STEP
Supervisor runs verifier (e.g., pytest)
    ↓ verification: ok=True
Supervisor advances to step 2
    ↓ injects next objective into pane
Agent receives instruction, starts step 2
    ↓ ... repeats until COMPLETED
```

If verification fails, supervisor injects a retry instruction with failure details.
If agent is blocked, supervisor escalates to you (pauses and waits).

### 8. Stop

```bash
# Stop a specific run
thin-supervisor run stop <run_id>

# Stop the daemon
thin-supervisor daemon stop
```

---

## Part B: tmux + Claude Code

Almost identical to Codex. The only difference is where the Skill is installed.

### 1. Install the Claude Code Skill

```bash
cp -r skills/lh-supervisor ~/.claude/skills/
```

### 2. Same steps as Part A

Replace `codex` with `claude` in step 5:

```bash
claude
```

Then invoke `/lh-supervisor` or describe your task. Everything else works the same.

---

## Part C: open-relay + Any Agent

Use this when you want sessions that survive terminal disconnects, or when you're running agents remotely.

### Prerequisites

Install [open-relay](https://github.com/slaveOftime/open-relay):

```bash
# macOS
brew install slaveOftime/tap/open-relay

# Or from source
cargo install --git https://github.com/slaveOftime/open-relay
```

### 1. Start the open-relay daemon

```bash
oly daemon start --detach
```

### 2. Start an agent session

```bash
# Start Codex in an oly session
oly start --title "my-task" --cwd /path/to/project codex

# Or Claude Code
oly start --title "my-task" --cwd /path/to/project claude
```

Note the session ID from the output (e.g., `abc123`).

### 3. Configure supervisor for open-relay

```bash
cd /path/to/project
thin-supervisor init

# Edit config to use open-relay
cat > .supervisor/config.yaml << 'EOF'
surface_type: "open_relay"
surface_target: ""
poll_interval_sec: 2.0
read_lines: 100
judge_model: null
runtime_dir: ".supervisor/runtime"
EOF
```

### 4. Register the run

```bash
thin-supervisor daemon start

# Register with the oly session ID
thin-supervisor run register \
  --spec .supervisor/specs/my-plan.yaml \
  --pane abc123
```

(Here `--pane` is the session target — for open-relay it's the oly session ID.)

### 5. Monitor

```bash
thin-supervisor status

# Read oly session output directly
oly logs abc123 --tail 50

# Or through supervisor bridge
thin-supervisor bridge read abc123 50
```

### 6. Attach to watch

```bash
# Full interactive attach (you can type directly)
oly attach abc123
```

---

## Part D: Any CLI Agent (OpenCode, Droid, Amp, etc.)

thin-supervisor works with **any CLI agent** that runs in a terminal. The agent just needs to follow the checkpoint protocol.

### Option 1: Agent has a skill system

If your agent supports skills (like Codex's `~/.codex/skills/` or Claude's `~/.claude/skills/`), copy the appropriate Skill:

```bash
# Adapt the Codex skill for your agent
cp -r skills/lh-supervisor-codex ~/.your-agent/skills/lh-supervisor
```

The Skill teaches the agent the checkpoint protocol and the 4-stage workflow.

### Option 2: Agent reads AGENTS.md

Most AI coding agents read `AGENTS.md` in the project root. thin-supervisor's `AGENTS.md` contains the checkpoint protocol. If your agent reads it, it will know how to emit checkpoints.

```bash
# AGENTS.md is already in the repo — just make sure it's in your project
cp AGENTS.md /path/to/your-project/
```

### Option 3: Manual prompting

If your agent doesn't support skills or AGENTS.md, you can manually instruct it:

> After each significant action, output a checkpoint block:
> ```
> <checkpoint>
> status: step_done
> current_node: step_1
> summary: what you did
> evidence:
>   - modified: file.py
> needs:
>   - none
> question_for_supervisor:
>   - none
> </checkpoint>
> ```

Then start the supervisor watching that agent's tmux pane or oly session.

---

## Foreground Mode (Debugging)

For debugging, you can skip the daemon and run supervisor in foreground:

```bash
# Single run, visible output, Ctrl+C to stop
thin-supervisor run foreground \
  --spec .supervisor/specs/my-plan.yaml \
  --pane work:0
```

This is useful when:
- Debugging supervisor behavior
- Testing a new spec
- Running a one-off task

---

## Writing a Spec Manually

If you don't want to use the Skill's auto-generation, write a spec YAML:

```yaml
kind: linear_plan
id: my-feature
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
    objective: run full test suite and commit
    verify:
      - type: command
        run: pytest -q
        expect: pass
```

Save to `.supervisor/specs/my-feature.yaml`.

---

## Multiple Concurrent Runs

The daemon supports multiple agents across different tmux sessions:

```bash
# Terminal 1: start daemon
thin-supervisor daemon start

# Terminal 2: tmux session for project A
tmux new -s project-a
codex  # ... invoke /lh-supervisor

# Terminal 3: tmux session for project B
tmux new -s project-b
claude  # ... invoke /lh-supervisor

# Check all runs
thin-supervisor status
# RUN_ID              PANE                 STATE              NODE
# run_abc123          project-a:0.0        RUNNING            implement
# run_def456          project-b:0.0        VERIFYING          write_tests
```

Each run has isolated state in `.supervisor/runtime/runs/<run_id>/`.

---

## Troubleshooting

### Supervisor not parsing checkpoints

```bash
# Check what the supervisor sees
thin-supervisor bridge read <pane> 50

# Look for <checkpoint> tags in the output
# If none: agent is not following the protocol
# Fix: ensure Skill is installed or AGENTS.md is present
```

### Daemon won't start

```bash
# Check if already running
thin-supervisor status

# Check for stale socket
ls -la .supervisor/daemon.sock

# Force clean
thin-supervisor daemon stop
rm -f .supervisor/daemon.sock .supervisor/daemon.pid
thin-supervisor daemon start
```

### tmux bridge not working

```bash
# Diagnose
thin-supervisor bridge doctor

# Check if you're inside tmux
echo $TMUX

# List all panes
thin-supervisor bridge list
```

### open-relay session not found

```bash
# List oly sessions
oly ls

# Check daemon
oly daemon start --detach
```
