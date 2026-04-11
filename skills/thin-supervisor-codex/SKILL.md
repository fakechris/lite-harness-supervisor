---
name: thin-supervisor
description: >
  Drive long-running multi-step tasks to completion with deterministic
  verification. Four-stage workflow: Clarify → Plan → Approve → Execute.
  Use when the user describes a complex plan, multi-step implementation,
  long-running workflow, or says "run this plan", "execute continuously",
  "don't stop until done".
version: 0.2.0
user-invocable: true
---

# Lite Harness Supervisor (Codex)

Four-stage supervised execution: **Clarify → Plan → Approve → Execute**.

The supervisor is a tmux sidecar that watches your output, makes
continue/verify/escalate decisions, and injects next-step instructions.
You focus on execution. It handles the orchestration.

---

## Stage 1: Clarify

**Skip** if the user's request has concrete signals (file path, function
name, issue number, test command, acceptance criteria). Go to Stage 2.

Otherwise, ask ONE question per round to clarify:
1. Intent — Why?
2. Outcome — What does success look like?
3. Scope — What's in/out?
4. Non-goals — What NOT to do?
5. Acceptance criteria — How to verify?
6. Decision boundaries — What can you decide alone?

Explore the codebase first. Never ask for facts you can discover.

Write clarification to `.supervisor/clarify/<slug>.md`.

---

## Stage 2: Plan + Self-Review

### 2a. Generate spec

Write to `.supervisor/specs/<slug>.yaml`:

```yaml
kind: linear_plan
id: <slug>
goal: <one-line goal>
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
    objective: <concrete, actionable>
    verify:
      - type: command
        run: <verification command>
        expect: pass
```

Verification types: `command` (run/expect), `artifact` (path/exists),
`git` (check/expect), `workflow` (require_node_done).

Each step MUST have at least one verify entry. Objectives must be
concrete. One deliverable per step.

### 2b. Self-review

**Architect pass**: completeness, verify validity, simpler alternative, failure scenarios.

**Critic pass**: simulate 2-3 steps mentally — does agent have enough context?

If problems found → fix spec, re-review (max 3 rounds).

Write review to `.supervisor/plans/<slug>-review.md`.

---

## Stage 3: Approve + Attach

Show user: spec summary + acceptance criteria + self-review verdict.
User chooses: Approve / Adjust / Reject.

Skip if user said "just run it".

As soon as the user approves, or if approval is skipped, attach the
supervisor BEFORE any implementation work:

```bash
scripts/thin-supervisor-attach.sh <slug>
```

Do not start coding, git cleanup, worktree edits, or long test runs
until this command succeeds.

---

## Stage 4: Execute

If attach already succeeded in Stage 3, do not run it again.
Only use this command when execution starts from a spec that is not yet attached:

```bash
scripts/thin-supervisor-attach.sh <slug>
```

### Checkpoint protocol

```text
<checkpoint>
run_id: <run_id from thin-supervisor status>
checkpoint_seq: <incrementing integer, start from 1>
status: working | blocked | step_done | workflow_done
current_node: <step_id from spec>
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

---

## Rules

- Do NOT ask "should I continue?" — the supervisor decides
- Do NOT skip verification
- Do NOT begin implementation before the attach script succeeds
- DO emit checkpoints frequently
- DO explore codebase before asking user questions
- DO self-review your plan before approval
