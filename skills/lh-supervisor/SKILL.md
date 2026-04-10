---
name: lh-supervisor
description: >
  Drive long-running multi-step tasks to completion with deterministic
  verification. Four-stage workflow: Clarify → Plan → Approve → Execute.
  Use when the user describes a complex plan, multi-step implementation,
  long-running workflow, or says "run this plan", "execute continuously",
  "don't stop until done", "长任务", "持续执行".
version: 0.2.0
user-invocable: true
---

# Lite Harness Supervisor

Four-stage supervised execution: **Clarify → Plan → Approve → Execute**.

The supervisor is a tmux sidecar that watches your output, makes
continue/verify/escalate decisions, and injects next-step instructions.
You focus on execution. It handles the orchestration.

---

## Stage 1: Clarify

**Goal**: Eliminate ambiguity before planning. A vague plan wastes more
time than a thorough question.

### Skip condition

If the user's request contains ANY concrete signal, skip directly to
Stage 2 (Plan):

| Signal | Example |
|--------|---------|
| File path | "fix src/auth.py" |
| Function/class name | "refactor validateToken" |
| Issue/PR number | "implement #42" |
| Test command | "make pytest pass" |
| Numbered steps | "1. Add X  2. Test Y" |
| Acceptance criteria | "done when all tests green" |

### Clarify loop (when needed)

Ask ONE question per round. Target the weakest clarity dimension:

1. **Intent** — Why does the user want this?
2. **Outcome** — What does success look like?
3. **Scope** — What is in and out?
4. **Non-goals** — What should NOT be done?
5. **Acceptance criteria** — How do we verify completion?
6. **Decision boundaries** — What can you decide alone vs. must ask?

Rules:
- Explore the codebase FIRST. Never ask users for facts you can discover.
- Stay on one thread until it's clear. Don't rotate dimensions for breadth.
- Exit when all dimensions have clear answers, or user says "enough".

### Artifact

Write to `.supervisor/clarify/<slug>.md`:

```markdown
# Clarification: <slug>

## Intent
<why>

## Desired Outcome
<what success looks like>

## In-Scope
- <item>

## Out-of-Scope / Non-goals
- <item>

## Acceptance Criteria
- [ ] <testable criterion>

## Decision Boundaries
- Agent may decide: <what>
- Must ask user: <what>

## Constraints
- <constraint>

## Codebase Context
- <findings from exploration>
```

---

## Stage 2: Plan + Self-Review

**Goal**: Generate a spec that will work on the first try.

### 2a. Generate the spec

Break the task into 3-10 sequential steps. Each step needs a concrete
objective and at least one verification check.

Write to `.supervisor/specs/<slug>.yaml`:

```yaml
kind: linear_plan
id: <slug>
goal: <one-line goal>
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
    objective: <concrete, actionable>
    verify:
      - type: command
        run: <verification command>
        expect: pass
```

#### Verification types

| Type | Fields | Example |
|------|--------|---------|
| `command` | `run`, `expect` (pass/fail/contains:text) | `run: pytest -q`, `expect: pass` |
| `artifact` | `path`, `exists` (true/false) | `path: src/auth.py`, `exists: true` |
| `git` | `check` (dirty), `expect` (true/false) | `check: dirty`, `expect: true` |
| `workflow` | `require_node_done` (true/false) | `require_node_done: true` |

#### Spec rules

- Each step MUST have at least one `verify` entry
- Objectives must be concrete and actionable (not "improve X")
- Steps ordered by dependency
- Verification commands must be runnable without user input
- One clear deliverable per step

### 2b. Architect self-review

Review your own spec as if you were a senior architect:

1. **Completeness**: Any missing steps? Does the order make sense?
2. **Verification validity**: Does each verify actually prove the step is done?
   (A command that always returns 0 is not verification.)
3. **Simpler alternative**: Is there a shorter path to the same outcome?
4. **Failure scenarios**: What could go wrong at each step?

### 2c. Critic self-review

Now simulate execution:

1. Pick 2-3 representative steps
2. Mentally execute them as if you were the agent
3. Ask: "Do I have ALL the context I need without guessing?"
4. Check: Are acceptance criteria testable and unambiguous?

### Iteration

If either review finds problems:
- Fix the spec
- Re-run both reviews
- Maximum 3 rounds

### Artifacts

Write review results to `.supervisor/plans/<slug>-review.md`:

```markdown
# Plan Review: <slug>

## Architect Findings
- <finding or "No issues found">

## Critic Findings
- <finding or "No issues found">

## Changes Made
- <what was fixed, or "None">

## Verdict
APPROVED / NEEDS_USER_INPUT: <specific questions>
```

---

## Stage 3: Approve + Attach

Present to the user:

```
## Spec Summary

Goal: <goal>
Steps: <count>
Acceptance: <key criteria>

## Self-Review Result

<architect + critic verdict>

## Options

1. ✅ Approve — start supervised execution
2. 🔄 Adjust — tell me what to change (returns to Stage 2)
3. ❌ Reject — cancel
```

Skip condition: User explicitly said "don't ask, just run" or "直接执行".

### Attach immediately after approval

As soon as the user approves, or if approval is skipped, attach the
supervisor BEFORE any implementation work:

```bash
scripts/lh-supervisor-attach.sh <slug>
```

Do not start coding, git cleanup, worktree edits, or long test runs
until this command succeeds.

---

## Stage 4: Execute

### Start execution only after attach succeeds

```bash
scripts/lh-supervisor-attach.sh <slug>
```

### Follow the checkpoint protocol

After completing meaningful work, output a checkpoint block:

```text
<checkpoint>
run_id: <run_id from thin-supervisor status>
checkpoint_seq: <incrementing integer, start from 1>
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

| Status | When to use |
|--------|-------------|
| `working` | Still making progress on current step |
| `blocked` | Cannot proceed without external input |
| `step_done` | Current step complete, ready for verification |
| `workflow_done` | All steps complete |

### Continue working

After a checkpoint, the supervisor will:
1. **Continue** — inject the next instruction
2. **Verify** — run verification commands, advance if passing
3. **Escalate** — do nothing; you pause for user input

---

## Rules

- Do NOT ask "should I continue?" — the supervisor decides
- Do NOT skip verification — every step must pass its verify checks
- Do NOT begin implementation before the attach script succeeds
- Do NOT modify the spec file — the supervisor owns it
- DO emit checkpoints frequently — they are the supervisor's eyes
- DO explore the codebase before asking the user questions
- DO self-review your plan before asking for approval
