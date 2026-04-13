# Getting Started

This guide walks you through setting up thin-supervisor from scratch. Pick the combination that matches your setup:

- **Execution Surface**: tmux (default), open-relay, or JSONL observation mode
- **AI Agent**: Codex, Claude Code, OpenCode, Droid, or any CLI agent

---

## Step 0: Install thin-supervisor

```bash
pip install thin-supervisor

# Verify
thin-supervisor --help
```

You should see top-level commands including `init`, `daemon`, `run`, `status`, `list`, `observe`, `note`, `session`, and `bridge`.

---

## Part A: tmux + Codex

The most common setup. Codex runs in a visible tmux pane, supervisor watches from the side.

### 1. Install the Codex Skill

```bash
# From the thin-supervisor repo
cp -r skills/thin-supervisor-codex ~/.codex/skills/thin-supervisor
```

This teaches Codex the checkpoint protocol and the 4-stage workflow (Clarify → Plan → Approve → Execute).
The default is now clarify-first: the skill should produce a draft spec, ask you to confirm it, then approve and attach.

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
If the directory already exists but is missing `config.yaml`, run `thin-supervisor init --repair` to restore the scaffold without overwriting the current config.

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

> Implement a login feature with tests. Use /thin-supervisor to run this as a supervised long task.

Or invoke the skill directly:

> /thin-supervisor

The Skill will:
1. Clarify your requirements or run a contract-confirmation pass if the request already looks concrete
2. Generate a draft spec YAML with verification steps
3. Self-review the plan (architect + critic passes)
4. Ask you to approve
5. Mark the spec approved with `thin-supervisor spec approve --spec .supervisor/specs/<slug>.yaml --by human`
6. Attach immediately with `scripts/thin-supervisor-attach.sh <slug>`
7. Start emitting checkpoints as it works

If a spec is still in `approval.status: draft`, execution commands reject it until you approve it.

### 6. Monitor

In another terminal:

```bash
# See all active runs
thin-supervisor status

# See every registered daemon across worktrees
thin-supervisor ps

# Detailed active-run view
thin-supervisor list

# See who owns a specific pane
thin-supervisor pane-owner %0

# Observe a specific run without attaching to the pane
thin-supervisor observe <run_id>

# Shared collaboration notes
thin-supervisor note add "handoff: waiting on staging token" --type handoff
thin-supervisor note list

# Export or analyze a completed run
thin-supervisor run export <run_id>
thin-supervisor run summarize <run_id>
thin-supervisor run replay <run_id>
thin-supervisor run postmortem <run_id>

# Run offline eval suites
thin-supervisor eval list
thin-supervisor eval run --suite approval-core --json
thin-supervisor eval run --suite routing-core --json
thin-supervisor eval run --suite escalation-core --json
thin-supervisor eval run --suite finish-gate-core --json
thin-supervisor eval replay --run-id <run_id> --json
thin-supervisor eval compare --suite approval-core --candidate-policy builtin-approval-strict-v1 --json
thin-supervisor eval canary --run-id <run_id> --json
thin-supervisor eval expand --suite approval-core --output .supervisor/evals/approval-core-synth.jsonl
thin-supervisor eval propose --suite approval-core --objective reduce_false_approval --json
thin-supervisor eval review-candidate --candidate-id <candidate_id> --json
thin-supervisor learn friction summarize --run-id <run_id> --json
thin-supervisor eval run --suite approval-core --save-report

# Watch the daemon log
tail -f .supervisor/runtime/daemon.log

# Read what the agent is outputting
thin-supervisor bridge read work:0 50
```

`--save-report` writes eval artifacts under `.supervisor/evals/reports/`. With `thin-supervisor eval propose`, the same run also writes a candidate-lineage manifest under `.supervisor/evals/candidates/`. Use `thin-supervisor eval review-candidate` to turn that manifest into a human promotion-review summary.

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
If you want to improve the system from past runs instead of only watching the live pane, use `run export`, `run summarize`, `run replay`, and `run postmortem` against the finished `run_id`.
If you want to validate clarify/approval behavior offline before changing the skill, start with `thin-supervisor eval run --suite approval-core`.
- `thin-supervisor eval run --suite routing-core`
  Validate deterministic `step_done/workflow_done -> VERIFY_STEP` routing.
- `thin-supervisor eval run --suite escalation-core`
  Validate deterministic `blocked -> ESCALATE_TO_HUMAN` behavior.
- `thin-supervisor eval run --suite finish-gate-core`
  Validate finish-gate and reviewer-gate completion rules.
- `thin-supervisor eval replay --run-id <run_id>`
  Check whether a policy candidate would regress historical supervisor behavior.
- `thin-supervisor eval compare --suite approval-core`
  Get a quick baseline-vs-candidate summary on the golden suite.
- `thin-supervisor eval expand --suite approval-core`
  Generate synthetic variants with provenance metadata.
- `thin-supervisor eval propose --suite approval-core`
  Recommend a constrained candidate policy with failure-case advisory.

### 7.5 Real canary protocol

Offline eval is necessary but not sufficient. Once a candidate looks good offline, run real canaries in this order:

1. `3-5` shadow canaries
   Keep the current default behavior in charge, but save eval evidence:
   ```bash
   thin-supervisor eval run --suite approval-core --save-report
   thin-supervisor eval compare --suite approval-core --candidate-policy <candidate> --save-report
   ```
2. For each real supervised task, capture:
   ```bash
   thin-supervisor run summarize <run_id>
   thin-supervisor run postmortem <run_id>
   thin-supervisor eval replay --run-id <run_id> --save-report
   ```
   Once you have a small batch, aggregate it with:
   ```bash
   thin-supervisor eval canary --run-id <run_a> --run-id <run_b> --save-report
   ```
3. If the shadow canaries stay clean, move to `10-20` limited rollout runs.

Any user-visible regression should become a friction event immediately so it feeds the next offline loop.

If the spec requires reviewer sign-off (`acceptance.must_review_by`), the run pauses at the finish gate until you acknowledge it:

```bash
thin-supervisor run review <run_id> --by human
```

For all other `PAUSED_FOR_HUMAN` cases, the recovery command is usually:

```bash
thin-supervisor run resume --spec .supervisor/specs/<slug>.yaml --pane <target> [--surface ...]
```

Daemon mode has no standalone GUI, so pause visibility comes from:
- the supervised pane itself via the default `tmux_display` notification channel
- `thin-supervisor status` / `thin-supervisor list`, which now print `reason` and `next`
- `.supervisor/runtime/notifications.jsonl` for durable notification audit logs

The default notification config is:

```yaml
notification_channels:
  - kind: tmux_display
  - kind: jsonl
pause_handling_mode: notify_then_ai
max_auto_interventions: 2
```

Later channels such as Feishu or Telegram should implement the same interface used by `supervisor/notifications.py`.
In `notify_then_ai` mode, thin-supervisor does not stop at the first human pause candidate. It first emits the notification, then lets the agent attempt a bounded automatic recovery for selected situations such as blocked checkpoints, repeated node mismatch, and retry-budget exhaustion. Reviewer-gated finish pauses still remain human-controlled.

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
cp -r skills/thin-supervisor ~/.claude/skills/
```

### 2. Same steps as Part A

Replace `codex` with `claude` in step 5:

```bash
claude
```

Then invoke `/thin-supervisor` or describe your task. Everything else works the same.

The installed skill now separates:
- immutable contract rules in `references/contract.md`
- optimizable behavior hints in `strategy/*.md`

If you are tuning the skill, change the strategy fragments first. Do not mutate the contract file unless the execution rules themselves changed.

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
  --pane abc123 \
  --surface open_relay
```

(Here `--pane` is the session target — for open-relay it's the oly session ID.)

### 5. Monitor

```bash
thin-supervisor status

# Read oly session output directly
oly logs abc123 --tail 50

# Or inspect the supervisor's normalized run view
thin-supervisor observe <run_id>
```

### 6. Attach to watch

```bash
# Full interactive attach (you can type directly)
oly attach abc123
```

---

## Part D: JSONL Observation Mode

Use this when the agent already writes a native transcript file and you want the supervisor to observe checkpoints without controlling the terminal surface directly.

### What JSONL mode is good for

- Codex or Claude sessions where transcript files already exist
- Read-only monitoring of a session running somewhere else
- Debugging checkpoint emission without depending on tmux capture

### Important limitation

JSONL mode is currently **observation-only**. The supervisor can persist next-step instructions to `.supervisor/runtime/instructions/<session>.txt`, but delivery depends on the agent-side skill or hook path checking that file. Do not treat JSONL mode as a full replacement for tmux or open-relay interactive delivery.

### 1. Resolve the current transcript

Inside the agent session:

```bash
thin-supervisor session detect
thin-supervisor session jsonl
thin-supervisor session list
```

`session jsonl` now prefers the active session ID when available and only falls back to the newest transcript if it cannot identify the live session.

### 2. Register a run against the transcript

```bash
thin-supervisor daemon start
thin-supervisor run register \
  --spec .supervisor/specs/my-plan.yaml \
  --pane "$(thin-supervisor session jsonl)" \
  --surface jsonl
```

### 3. Observe progress

```bash
thin-supervisor status
thin-supervisor observe <run_id>
tail -f "$(thin-supervisor session jsonl)"
```

If the agent keeps reporting a completed node after the supervisor advances, the run now pauses with a human escalation instead of hanging silently.

---

## Part E: Any CLI Agent (OpenCode, Droid, Amp, etc.)

thin-supervisor works with **any CLI agent** that runs in a terminal. The agent just needs to follow the checkpoint protocol.

### Option 1: Agent has a skill system

If your agent supports skills (like Codex's `~/.codex/skills/` or Claude's `~/.claude/skills/`), copy the appropriate Skill:

```bash
# Adapt the Codex skill for your agent
cp -r skills/thin-supervisor-codex ~/.your-agent/skills/thin-supervisor
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
codex  # ... invoke /thin-supervisor

# Terminal 3: tmux session for project B
tmux new -s project-b
claude  # ... invoke /thin-supervisor

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

### JSONL mode attached to the wrong transcript

```bash
thin-supervisor session detect
thin-supervisor session jsonl
thin-supervisor session list
```

If `session detect` is empty, confirm the agent exports a session ID or pass the explicit transcript path to `--pane`.

### Run is paused waiting for review

```bash
thin-supervisor status
thin-supervisor run review <run_id> --by human
```

Use `--by stronger_reviewer` if the spec explicitly requires that reviewer tier.
