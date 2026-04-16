# Telegram Command Channel Requirements

## Goal

Upgrade Telegram from a one-way notification adapter into a real operator command
channel for `thin-supervisor`.

The target user experience is:

1. A run pauses, blocks, or completes.
2. Telegram pushes a summary to the operator.
3. The operator can inspect, ask questions, pause, resume, and add notes from
   Telegram without opening the terminal.
4. All operator actions flow through the same canonical operator APIs used by
   TUI/CLI.

Telegram must be a **channel adapter**, not a parallel control plane.

## Current State

Today Telegram only supports outbound notifications:

- `notify(event)` pushes alerts
- no inbound updates
- no run selection flow
- no command parsing
- no pause/resume
- no explain/drift/clarification requests

This is below the architecture described in:

- `docs/plans/2026-04-15-operator-channel-and-explainer-architecture.md`

## Non-Goals

This phase should **not**:

- replace TUI as the primary local operator surface
- add worker-direct chat
- bypass the supervisor state machine
- invent a second run model
- require a separate operator event log

## Product Principles

1. Telegram is an **adapter** over canonical operator actions.
2. Remote commands must be **explicit, auditable, and reversible**.
3. The bot must prefer **run-scoped actions** over free-form chat.
4. Human questions go to the **explainer/mediator plane first**, not directly to
   the worker.
5. Dangerous or ambiguous commands must fail closed with a clear explanation.

## Supported User Jobs

### Job 1: Get alerted

When a run changes state in a way an operator cares about, Telegram should send:

- run id
- state
- reason
- next action
- worktree
- quick actions

### Job 2: Inspect the current run

From Telegram the operator should be able to:

- list runs
- inspect a run snapshot
- inspect recent exchange
- inspect timeline
- view notes

### Job 3: Ask what the run is doing

From Telegram the operator should be able to ask:

- what is it doing?
- why is it paused?
- has it drifted?
- explain the latest exchange
- answer this question about the run

These actions must use the canonical operator APIs:

- `explain_run`
- `explain_exchange`
- `assess_drift`
- `request_clarification`

### Job 4: Intervene

From Telegram the operator should be able to:

- pause a run
- resume a run
- add a run-scoped operator note

Phase 1 should not include arbitrary supervisor instruction injection.

## Required Command Surface

Telegram should support both:

1. **button-first interactions** for common actions
2. **slash-like text commands** for power users

### Minimum command set

- `/runs`
- `/run <run_id>`
- `/inspect <run_id>`
- `/exchange <run_id>`
- `/explain <run_id>`
- `/drift <run_id>`
- `/ask <run_id> <question>`
- `/pause <run_id>`
- `/resume <run_id>`
- `/note <run_id> <content>`
- `/notes <run_id>`
- `/help`

### Minimum button set on alert cards

- `Inspect`
- `Explain`
- `Drift`
- `Ask`
- `Pause`
- `Resume`
- `Notes`

Buttons should map to the same backend action layer as text commands.

## Canonical Backend Contract

Telegram must not talk to tmux, JSONL transcript sources, or daemon internals
directly. It must call the same operator plane used by TUI.

Required backend calls:

- `list_runs()`
- `get_run_snapshot(run_id)`
- `get_run_timeline(run_id, limit=...)`
- `get_recent_exchange(run_id)`
- `explain_run(run_id, language=...)`
- `explain_exchange(run_id, language=...)`
- `assess_drift(run_id, language=...)`
- `request_clarification(run_id, question=..., language=...)`
- `pause_run(run_id)`
- `resume_run(run_id)`
- `add_operator_note(run_id, note=...)`
- `list_operator_notes(run_id)`

If an action is unsupported for a run type, Telegram must show the same
unavailable reason as TUI/CLI.

## Run Identity and Selection

Telegram cannot assume the operator knows a full run id.

The adapter must support:

- recent runs list
- short run ids in UI
- stable callback payloads that reference full run ids
- worktree display
- current state display
- current node display

When multiple runs exist, the bot must force explicit selection before
destructive actions.

## Clarification Routing Semantics

Telegram clarification must follow the same contract as the operator plane.

### Phase 1 behavior

`/ask` goes to the explainer/mediator layer only.

This means:

- answer is based on run state, timeline, spec context, and codebase signals
- answer is recorded as clarification sideband events
- no worker interruption occurs

### Phase 2 behavior

Later, if product decides to support worker-mediated clarification, Telegram must
still route through supervisor:

1. operator asks question
2. supervisor records clarification request
3. supervisor decides whether to answer directly or inject a bounded clarification
   instruction to the worker
4. worker reply is recorded as clarification response

Telegram must never talk to the worker directly.

## State and Event Recording

Telegram command actions must be auditable.

At minimum the system must record:

- command received
- operator identity
- target run id
- action name
- action result
- resulting sideband events

Use the existing canonical logs and event model.
Do not create a new Telegram-specific source-of-truth JSONL.

New or required event types:

- `operator_command_received`
- `operator_command_rejected`
- `operator_command_completed`
- `clarification_request`
- `clarification_response`
- `operator_note`

## Authentication and Authorization

Telegram introduces a larger blast radius than TUI.

The bot must support:

1. bot token configuration
2. allowlist of authorized chat ids / user ids
3. optional mapping from Telegram user to operator identity
4. rejection of unauthorized commands

Out of scope for phase 1:

- multi-tenant RBAC
- granular per-repo permissions

But phase 1 must at least fail closed.

## Concurrency and Delivery Rules

Telegram is asynchronous and unreliable relative to a local TUI.

Therefore:

- commands must be idempotent where practical
- duplicate callback delivery must be tolerated
- long-running actions must reply immediately with an acknowledgement and then
  follow up with the result
- command handlers must not block the webhook polling loop on LLM execution

All expensive operations must run as async jobs:

- explain
- explain exchange
- drift
- clarification

Telegram should:

1. acknowledge receipt
2. show a "working" message
3. edit or reply with final result

## Message Design Requirements

Each Telegram run summary should include:

- short run id
- state
- node
- reason if paused/blocked
- next recommended action
- worktree

Each command response should be:

- concise by default
- expandable by follow-up commands
- language-aware

## Language Requirements

Telegram should support:

- operator default language
- per-command override later if needed

For phase 1:

- inherit a channel-level default language, defaulting to `zh`
- pass `language` through to operator APIs

This is important because Telegram is a remote human-facing channel, not a raw
machine log.

## Error Handling Requirements

Examples of required error behavior:

- unknown run id -> show nearest matching runs or "run not found"
- unsupported action -> show why unavailable
- daemon unreachable -> surface "daemon unavailable" clearly
- command timeout -> tell user the job is still pending or failed
- unauthorized user -> deny with no action taken

No silent failures.

## Interaction Model

### Recommended mode

Telegram should use a hybrid interaction model:

- push alerts as normal messages
- use inline buttons for common actions
- use text commands for detailed queries and note entry

### Why not buttons only

Because:

- clarification questions need text input
- notes need text input
- power users need direct addressing of runs

### Why not text only

Because:

- most operators will use alert-driven interventions
- pause/resume/explain should be one tap from the alert

## Implementation Slices

### Slice 1: Read-only command channel

Implement:

- auth
- `/runs`
- `/run`
- `/inspect`
- `/exchange`
- `/explain`
- `/drift`

No pause/resume yet.

### Slice 2: Safe interventions

Implement:

- `/pause`
- `/resume`
- `/note`
- `/notes`

### Slice 3: Clarification

Implement:

- `/ask`
- clarification event recording
- answer formatting

### Slice 4: Polished alert cards

Implement:

- alert buttons
- post-action message updates
- run selection improvements

## Acceptance Criteria

Phase 1 is done when:

1. An authorized operator can receive a pause alert in Telegram.
2. From Telegram, the operator can inspect, explain, drift-check, pause, resume,
   and note a run without opening TUI/CLI.
3. Every Telegram action maps onto canonical operator APIs rather than bespoke
   Telegram-only logic.
4. Clarification requests are recorded in session history.
5. Unsupported actions fail with clear reasons.
6. Unauthorized users cannot trigger control actions.
7. Long-running explainer actions do not block inbound command handling.

## Testing Plan

### Unit tests

- command parsing
- auth checks
- callback payload decoding
- action routing
- language propagation
- unavailable reason propagation

### Integration tests

- alert -> inspect -> explain flow
- alert -> pause flow
- paused run -> resume flow
- ask clarification flow
- note add / note list flow
- unauthorized user flow
- duplicate callback delivery

### Scenario tests

1. Operator receives a `human_pause` alert at night, asks "为什么暂停了？",
   gets a Chinese answer, then resumes.
2. Operator receives a drift warning, asks for exchange explanation, then adds a
   note for later follow-up.
3. Operator taps resume on a completed run and gets a clear "completed" denial.
4. Operator tries to operate on an orphaned run with no resumable state and gets
   a precise error.

## Open Product Decisions

These still need explicit product decisions:

1. Should Telegram be read-write in phase 1, or should pause/resume wait for a
   second phase?
2. Should clarification remain explainer-only for Telegram in phase 1?
3. Should Telegram default to `zh` for explanation language?
4. Should the bot support multiple repos/worktrees in one chat, or require
   explicit project scoping?

## Recommended Decision

My recommendation:

- phase 1 should be **read-write**
- clarification should stay **explainer-only**
- default language should be **zh**
- multi-worktree support should exist, but every action must display worktree
  explicitly before intervention

That gives operators real value quickly without opening a second control plane.
