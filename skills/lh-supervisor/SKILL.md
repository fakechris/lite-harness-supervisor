---
name: lh-supervisor
description: >
  Drive long-running multi-step tasks to completion with deterministic
  verification. Use when the user describes a complex plan, multi-step
  implementation, long-running workflow, or says "run this plan",
  "execute continuously", "don't stop until done", "长任务", "持续执行".
  Generates a spec from the user's goal, starts a tmux sidecar supervisor,
  and follows the checkpoint protocol for automated step progression.
version: 0.1.0
user-invocable: true
---

# Supervisor Skill

You are now in **supervised long-task mode**. A sidecar supervisor will
observe your output, make continue/verify/escalate decisions, and inject
next-step instructions — so you can focus on execution without asking
the user for confirmation at every turn.

## When to activate

- User describes a multi-step implementation plan (3+ steps)
- User says "run this plan", "execute continuously", "don't stop"
- User wants a long task driven to completion with verification

## Step 1: Understand the goal

Ask the user to describe their goal if not already clear. Identify:
- What is the end state?
- What are the intermediate milestones?
- How can each milestone be verified? (test commands, file existence, git state)

## Step 2: Generate the spec

Break the goal into 3-10 sequential steps. Write a spec YAML file:

```yaml
kind: linear_plan
id: <goal-slug>
goal: <one-line goal description>
finish_policy:
  require_all_steps_done: true
  require_verification_pass: true
  require_clean_or_committed_repo: false
policy:
  default_continue: true
  max_retries_per_node: 3
  max_retries_global: 12

steps:
  - id: step_1
    type: task
    objective: <concrete, actionable objective>
    outputs:
      - <expected file paths>
    verify:
      - type: command
        run: <verification command>
        expect: pass  # or: fail, contains:<text>
      - type: artifact
        path: <file that should exist>
        exists: true
```

Save the spec to `.supervisor/specs/<goal-slug>.yaml`.

### Verification types

| Type | Fields | Example |
|------|--------|---------|
| `command` | `run`, `expect` (pass/fail/contains:text) | `run: pytest -q tests/`, `expect: pass` |
| `artifact` | `path`, `exists` (true/false) | `path: src/auth.py`, `exists: true` |
| `git` | `check` (dirty), `expect` (true/false) | `check: dirty`, `expect: true` |
| `workflow` | `require_node_done` (true/false) | `require_node_done: true` |

### Rules for good specs

- Each step MUST have at least one `verify` entry
- Objectives must be concrete and actionable (not "improve X")
- Steps should be ordered by dependency
- Verification commands must be runnable without user input
- Keep steps focused — one clear deliverable per step

## Step 3: Start the supervisor

```bash
# Initialize (idempotent)
thin-supervisor init --force

# Start the sidecar daemon (watches this pane)
thin-supervisor run .supervisor/specs/<goal-slug>.yaml \
  --pane "$(thin-supervisor bridge id)" --daemon
```

## Step 4: Follow the checkpoint protocol

After completing meaningful work, output a checkpoint block. This is
**mandatory** — the supervisor parses these to track progress.

```text
<checkpoint>
status: working | blocked | step_done | workflow_done
current_node: <step_id from spec>
summary: <one-line description of what you did>
evidence:
  - modified: <file path>
  - ran: <command>
  - result: <short result>
candidate_next_actions:
  - <what you'd do next>
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
```

### Status values

| Status | Meaning |
|--------|---------|
| `working` | Still making progress on current step |
| `blocked` | Cannot proceed without external input |
| `step_done` | Current step is complete, ready for verification |
| `workflow_done` | All steps complete |

### When to emit checkpoints

- After completing a step → `status: step_done`
- After significant progress within a step → `status: working`
- When blocked on missing input/credentials → `status: blocked`
- When all steps are done → `status: workflow_done`

## Step 5: Continue working

After emitting a checkpoint, the supervisor will either:
1. **Continue**: inject the next instruction (you'll see it as input)
2. **Verify**: run verification commands, then advance to the next step
3. **Escalate**: do nothing — you'll naturally pause for user input

Just keep working. The supervisor handles the orchestration.

## Important

- Do NOT ask the user "should I continue?" — the supervisor decides
- Do NOT skip verification — every step must pass its verify checks
- Do NOT modify the spec file — the supervisor owns it
- DO emit checkpoints frequently — they are the supervisor's eyes
