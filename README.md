# thin-supervisor

A thin tmux sidecar that drives AI coding agents (Claude Code, Codex, Open Code) through long-running multi-step tasks with deterministic verification.

The agent runs in a visible, interactive tmux pane. The supervisor watches from the side — reading output, making continue/verify/escalate decisions, and injecting next-step instructions. You stay in your familiar agent UI the entire time.

## Quick Start

```bash
pip install thin-supervisor

# In your project directory
thin-supervisor init

# Write a spec (or use the /supervisor skill to generate one)
thin-supervisor run .supervisor/specs/my-plan.yaml --pane codex
```

## Architecture

```text
┌──────────────────────────┐  ┌─────────────────────┐
│  Agent Pane (visible)    │  │ Supervisor (sidecar) │
│  Claude Code / Codex     │  │ reads pane output    │
│  user interacts here     │  │ parses checkpoints   │
│                          │  │ gates decisions      │
│  <checkpoint>            │──│ runs verifiers       │
│  status: step_done       │  │ injects next step    │
│  </checkpoint>           │  │                      │
└──────────────────────────┘  └─────────────────────┘
         tmux session
```

**Flow**: Agent works → emits `<checkpoint>` → supervisor reads pane → gate decides (continue/verify/escalate) → verifier runs → supervisor injects next instruction → agent continues.

## Spec Format

Specs define the task plan and verification criteria:

```yaml
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
```

### Verification Types

| Type | Fields | Description |
|------|--------|-------------|
| `command` | `run`, `expect` | Run a shell command. `expect`: `pass`, `fail`, `contains:<text>` |
| `artifact` | `path`, `exists` | Check if a file exists |
| `git` | `check`, `expect` | Check git state (e.g., `check: dirty`) |
| `workflow` | `require_node_done` | Check if current node is marked done |

## CLI Commands

| Command | Description |
|---------|-------------|
| `thin-supervisor init` | Create `.supervisor/` directory with config |
| `thin-supervisor run <spec> --pane <target>` | Start sidecar watching a tmux pane |
| `thin-supervisor status` | Show current run state |
| `thin-supervisor deinit` | Remove `.supervisor/` directory |

## Configuration

Config lives in `.supervisor/config.yaml`:

```yaml
pane_target: "codex"          # tmux pane label or %id
poll_interval_sec: 2.0        # how often to read pane
read_lines: 100               # lines to capture per read

# LLM judge (null = offline stub mode)
judge_model: null              # e.g., anthropic/claude-haiku-4-5-20251001
judge_temperature: 0.1
judge_max_tokens: 512
```

Override with environment variables: `SUPERVISOR_PANE_TARGET`, `SUPERVISOR_JUDGE_MODEL`, etc.

## Checkpoint Protocol

Agents must emit structured checkpoints for the supervisor to parse:

```text
<checkpoint>
status: working | blocked | step_done | workflow_done
current_node: step_id
summary: one-line description
evidence:
  - modified: path/to/file
  - ran: pytest -q
candidate_next_actions:
  - next thing to do
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
```

## Skill Integration

Install the Claude Code skill for automatic spec generation:

```bash
cp -r skills/supervisor ~/.claude/skills/
```

Then invoke `/supervisor` in Claude Code to generate a spec from natural language and start supervised execution.

## Key Invariants

1. The agent never asks the user for confirmation — the supervisor decides
2. The supervisor never modifies the repo — only the agent does
3. `finish` requires all verifiers to pass — no "verbal completion"
4. Default strategy is `continue` — only escalate when genuinely blocked
5. All escalations include a machine-readable reason

## Development

```bash
git clone https://github.com/fakechris/lite-harness-supervisor
cd lite-harness-supervisor
pip install -e ".[dev]"
pytest -q
```

## License

MIT
