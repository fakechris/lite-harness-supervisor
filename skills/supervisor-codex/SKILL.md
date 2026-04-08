---
name: supervisor
description: >
  Drive long-running multi-step tasks to completion with deterministic
  verification. Use when the user describes a complex plan, multi-step
  implementation, long-running workflow, or says "run this plan",
  "execute continuously", "don't stop until done".
  Generates a spec from the user's goal, starts a tmux sidecar supervisor,
  and follows the checkpoint protocol for automated step progression.
version: 0.1.0
user-invocable: true
---

# Supervisor Skill (Codex)

You are now in **supervised long-task mode**. A sidecar supervisor watches
your output, makes continue/verify/escalate decisions, and injects
next-step instructions so you can focus on execution.

## When to activate

- User describes a multi-step implementation plan (3+ steps)
- User says "run this plan", "execute continuously", "don't stop"
- User wants a long task driven to completion with verification

## Step 1: Understand the goal

Ask the user to describe their goal if not already clear. Identify:
- What is the end state?
- What are the intermediate milestones?
- How can each milestone be verified?

## Step 2: Generate the spec

Break the goal into 3-10 sequential steps. Write a spec YAML file:

```yaml
kind: linear_plan
id: <goal-slug>
goal: <one-line goal description>
finish_policy:
  require_all_steps_done: true
  require_verification_pass: true
policy:
  default_continue: true
  max_retries_per_node: 3
  max_retries_global: 12

steps:
  - id: step_1
    type: task
    objective: <concrete, actionable objective>
    verify:
      - type: command
        run: <verification command>
        expect: pass
```

Save the spec to `.supervisor/specs/<goal-slug>.yaml`.

### Verification types

| Type | Fields | Example |
|------|--------|---------|
| `command` | `run`, `expect` (pass/fail/contains:text) | `run: pytest -q tests/`, `expect: pass` |
| `artifact` | `path`, `exists` (true/false) | `path: src/auth.py`, `exists: true` |
| `git` | `check` (dirty), `expect` (true/false) | `check: dirty`, `expect: true` |

### Rules

- Each step MUST have at least one `verify` entry
- Objectives must be concrete and actionable
- Verification commands must be runnable without user input

## Step 3: Start the supervisor

```bash
thin-supervisor init 2>/dev/null
thin-supervisor run .supervisor/specs/<goal-slug>.yaml \
  --pane "$(thin-supervisor bridge id)" --daemon
```

## Step 4: Follow the checkpoint protocol

After completing meaningful work, you MUST output a checkpoint block:

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

### When to emit checkpoints

- After completing a step → `status: step_done`
- After significant progress → `status: working`
- When blocked → `status: blocked`
- When all steps done → `status: workflow_done`

## Step 5: Continue working

The supervisor will either:
1. **Continue**: inject the next instruction
2. **Verify**: run verification commands, then advance
3. **Escalate**: do nothing — you pause for user input

## Rules

- Do NOT ask the user "should I continue?" — the supervisor decides
- Do NOT skip verification
- DO emit checkpoints frequently
