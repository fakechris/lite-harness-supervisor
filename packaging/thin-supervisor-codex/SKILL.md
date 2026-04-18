---
name: thin-supervisor
description: >
  Drive long-running multi-step tasks to completion with deterministic
  verification. Four-stage workflow: Clarify → Plan → Approve → Execute.
  Use when the user describes a complex plan, multi-step implementation,
  long-running workflow, or says "run this plan", "execute continuously",
  "don't stop until done".
version: 0.3.3
user-invocable: true
---

# Lite Harness Supervisor (Codex)

Four-stage supervised execution: **Clarify → Plan → Approve → Execute**.

The supervisor is a tmux sidecar that watches your output, makes
continue/verify/escalate decisions, and injects next-step instructions.
You focus on execution. It handles the orchestration.

## RPI Mapping

This workflow maps to the common `Research -> Plan -> Implement` pattern
like this:

- `Clarify` = `Research`
- `Plan + Self-Review` = `Plan`
- `Approve` = the explicit human/attach gate between planning and implementation
- `Execute` = `Implement`

That boundary matters. Research and planning may explore, summarize, and
shape the task, but they do **not** begin implementation. Implementation
starts only after approval and successful attach.

---

## Contract vs Strategy Loading

Always read `references/contract.md` before planning or execution. It is
the frozen behavior contract and must not be optimized away.

Strategy fragments are the only safe optimization surface:

- `strategy/approval-boundary.md`
- `strategy/finish-proof.md`
- `strategy/escalation.md`
- `strategy/pause-ux.md`

Load strategy fragments only when they are relevant to the current step.

---

## Context Loading

- When writing or revising a spec, read `references/spec-writing-guide.md`
- When deciding whether the user has approved execution, read `strategy/approval-boundary.md`
- When deciding supervision style or worker trust posture, read `references/supervision-modes.md`
- When verification fails or a retry plan is needed, read `references/debugging-playbook.md`
- When a supervised run is already active and you need the exact execution format, read `references/worker-checkpoint-protocol.md`
- When shaping checkpoint evidence or deciding whether `workflow_done` is justified, read `strategy/finish-proof.md`
- When deciding whether to escalate or continue, read `references/escalation-rules.md`
- When blocked or choosing between continue / retry / escalate behavior, read `strategy/escalation.md`
- When the supervisor pauses or completes and you need to explain that state to the user, read `strategy/pause-ux.md`
- When a supervised run completes, read `references/improve.md`

## Sub-Agent Boundary

Sub-agents may assist with read-only investigation or non-authoritative
summaries. They must **not** become the authoritative executor for an
active supervised run.

During an active supervised run, keep these in the main worker:

- current-node implementation
- checkpoint emission
- structured semantic declarations
- run-state mutation (`spec approve`, `run register`, `run resume`, `run review`, `run stop`)

---

## Stage 0: Preflight

Before entering clarify/plan, run a read-only environment check:

```bash
thin-supervisor bootstrap
```

This detects:
- whether you are inside tmux
- whether the pane is already occupied by another run
- whether .supervisor/ needs initialization
- whether the daemon is running

**If bootstrap fails** (not in tmux, pane locked, etc.), stop and tell
the user what needs to be fixed before starting any work.

**If an active run already exists** for this pane/project, do NOT start
a new clarify/plan cycle. Instead:
- Show the user the active run's status
- Ask whether to observe, resume, or stop it

Only proceed to Stage 1 if the environment is ready and no conflicting
run exists.

---

## Stage 1: Clarify

**Default behavior**: always start with a clarify pass before planning or
attaching. Do not jump directly from a user request to execution.

Explore the codebase first. Never ask for facts you can discover.

If the request is still ambiguous, ask **ONE question per round** until the
task contract is clear:
1. Intent — Why?
2. Outcome — What does success look like?
3. Scope — What's in/out?
4. Non-goals — What NOT to do?
5. Acceptance criteria — How to verify?
6. Decision boundaries — What can you decide alone?

If the request is already concrete, do a **contract confirmation pass**
instead of skipping clarify: summarize your inferred goal, scope,
non-goals, and acceptance criteria, then ask the user to confirm or
correct that understanding before planning.

Write clarification to `.supervisor/clarify/<slug>.md`.

---

## Stage 2: Plan + Self-Review

Before generating the spec, re-check `references/contract.md` and
`strategy/approval-boundary.md` so the planned approval flow is explicit.

### 2a. Generate spec

Write to `.supervisor/specs/<slug>.yaml`:

```yaml
kind: linear_plan
id: <slug>
goal: <one-line goal>
approval:
  required: true
  status: draft
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

Approval semantics are governed by `references/contract.md`. Do not ask
for a second confirmation after the user has already approved.

Show user: spec summary + acceptance criteria + self-review verdict.
User chooses: Approve / Adjust / Reject.

Do **not** attach or begin implementation until the user explicitly
approves the spec.

The following count as **explicit approval** and must NOT trigger a second
confirmation question:
- "可以"
- "同意"
- "开始吧"
- "按这个来"
- "就这么做"
- "approve"
- "approved"
- any clear equivalent that means "yes, start with this spec"

Once the user has already approved in the conversation, do not ask again.
Immediately mark the spec approved and continue to attach.

As soon as the user approves, bootstrap the environment and attach the
supervisor BEFORE any implementation work:

```bash
thin-supervisor bootstrap
thin-supervisor spec approve --spec .supervisor/specs/<slug>.yaml --by human
thin-supervisor run register --spec .supervisor/specs/<slug>.yaml --pane "$(thin-supervisor bridge id)"
```

`thin-supervisor bootstrap` auto-detects tmux, initializes `.supervisor/`
if missing, starts the daemon if needed, and validates the execution surface.

Execution commands will reject a draft spec. This is intentional: the
approval step is part of the contract, not optional ceremony.

Do not start coding, git cleanup, worktree edits, or long test runs
until these commands succeed.

---

## Stage 4: Execute

If attach already succeeded in Stage 3, do not run bootstrap or register again.

### Follow the checkpoint protocol

Read `references/worker-checkpoint-protocol.md` for:

- the exact checkpoint block shape
- the v2 structured semantic fields
- the first-checkpoint rule for newly injected nodes
- continue / block / finish semantics

When deciding whether a checkpoint contains enough finish evidence, also
load `strategy/finish-proof.md`.

---

## Rules

- The immutable execution rules live in `references/contract.md`
- Do NOT ask "should I continue?" — the supervisor decides
- Do NOT skip verification
- Do NOT begin implementation before the attach script succeeds
- DO emit checkpoints frequently
- DO explore codebase before asking user questions
- DO self-review your plan before approval
