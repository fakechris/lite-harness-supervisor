# Session-First Event Plane and Async Review Orchestration

**Goal:** clarify the real concurrency/event model of `thin-supervisor` today, then define a session-first event architecture for asynchronous review workflows such as GitHub bot review, external model/agent review, and pre-implementation plan review.

**Architecture:** keep the current runtime core session-first and deterministic. Do not turn the daemon into a generic multi-agent bus. Instead, add a separate session-scoped event plane for deferred external work: review requests are issued from the supervisor/operator plane, results return later through canonical event ingestion, and only the daemon-owned control plane decides whether to notify, pause, resume, or inject follow-up work.

**Tech Stack:** Python 3.10, `supervisor/daemon/server.py`, `supervisor/loop.py`, `supervisor/operator/*`, `supervisor/notifications.py`, Telegram/Lark command adapters, Unix-socket daemon IPC, JSON/JSONL runtime artifacts, future GitHub/CI/review adapters.

---

## Why This Document Exists

The current repo already has:

- a session-first runtime model
- a deterministic sidecar loop
- IM command adapters for Telegram and Lark
- async explainer/drift jobs
- notification fanout

What it does **not** yet have is a first-class model for:

- "supervisor asked another system to review this"
- "that result will come back later"
- "when it comes back, which session/run should receive it?"
- "should the result wake a human, wake a worker, or just be recorded?"

This becomes necessary for concrete workflows such as:

1. a coding agent pushes a PR and waits for GitHub review-bot output
2. the supervisor explicitly invokes another model/agent for review and waits for the answer
3. a plan or architecture draft must be reviewed before implementation begins

These are not just "more notifications." They are **deferred external tasks** that need correlation, durable storage, and a clear wake/resume policy.

---

## Problem Statement

The current system is strong at **live execution supervision**:

- read worker output
- parse checkpoint
- gate
- inject next instruction
- verify
- pause/recover if needed

It is weaker at **deferred asynchronous collaboration**:

- request external review now
- wait minutes later for a result
- route that result back to the correct session
- decide whether the session should continue, pause, or notify only

Today the IM layer does not solve this because it is designed as a **remote command surface**, not an event mailbox.

That is the core distinction this document freezes.

---

## Current Reality: What The System Actually Is

## 1. The system is session-first, not agent-first

This is already explicit in [docs/ARCHITECTURE.md](/Users/chris/workspace/lite-harness-supervisor/docs/ARCHITECTURE.md:1):

> The system's truth lives in `SessionRun`, not in any process or pane.

That means:

- the primary object is a **run bound to a session/worktree/surface**
- not a generic local "agent identity"
- not a free-floating inbox addressed to an abstract agent

This is why direct analogies to AgentInbox only go so far. AgentInbox is closer to `source/mailbox/activation-first`. `thin-supervisor` is closer to `session/run/control-first`.

## 2. A single run is a synchronous sidecar loop

One active run is driven by [SupervisorLoop.run_sidecar()](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:757).

Its shape is fundamentally synchronous and sequential:

1. read from the execution surface
2. parse checkpoints
3. apply gating / contradiction / recovery logic
4. inject when needed
5. sleep and repeat

For one run, there is not a second competing control loop.

This is important because it means:

- checkpoint handling is ordered
- injection is ordered
- verifier transitions are ordered

## 3. The daemon as a whole is concurrent and threaded

The daemon is **not** a single-threaded event bus and **not** an `asyncio` reactor.

Today it is a multi-threaded process with several lanes:

### Lane A: daemon IPC lane

[DaemonServer.start()](/Users/chris/workspace/lite-harness-supervisor/supervisor/daemon/server.py:136) starts a Unix-socket accept loop.

This lane handles commands such as:

- register
- stop
- resume
- observe
- inspect/explain/drift job submission
- note add/list

This IPC handler is effectively serialized through the daemon's accept loop.

### Lane B: one worker thread per active run

Every active run is given its own thread in [DaemonServer._run_worker()](/Users/chris/workspace/lite-harness-supervisor/supervisor/daemon/server.py:382).

That thread owns the sidecar loop for that run.

### Lane C: operator transport threads

Operator IM adapters have their own inbound transport threads:

- Telegram polling thread in [supervisor/adapters/telegram_command.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/adapters/telegram_command.py:69)
- Lark callback server thread in [supervisor/adapters/lark_command.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/adapters/lark_command.py:191)

These threads do **not** directly mutate worker state or inject into panes.

### Lane D: async operator jobs

Expensive operator actions such as explain/drift/clarification run in background threads:

- daemon-side [JobTracker](/Users/chris/workspace/lite-harness-supervisor/supervisor/operator/jobs.py:37)
- IM-side [AsyncJobPoller](/Users/chris/workspace/lite-harness-supervisor/supervisor/operator/command_dispatch.py:502)

These jobs are asynchronous, but they are not a general event plane. They are bounded background jobs for operator UX.

---

## Current Synchronization Model

The system is already using a real synchronization model, even if it is not yet described as one.

### What is synchronized today

1. **Per-run execution ownership**
   - only one worker thread drives one run's sidecar loop

2. **Daemon registry operations**
   - [DaemonServer._lock](/Users/chris/workspace/lite-harness-supervisor/supervisor/daemon/server.py:120) protects `_runs`, register/resume/reap bookkeeping

3. **Pane ownership**
   - pane locks prevent two active runs from controlling the same pane

4. **State persistence**
   - [StateStore.save()](/Users/chris/workspace/lite-harness-supervisor/supervisor/storage/state_store.py:82) uses atomic replace
   - `session_log.jsonl` is append-only

5. **Inbound IM transport ownership**
   - [OperatorChannelHost](/Users/chris/workspace/lite-harness-supervisor/supervisor/operator/channel_host.py:181) ensures only one process owns inbound polling/server transport per credential set

### What is asynchronous today

1. operator transport delivery
2. operator explanation/drift/clarification jobs
3. multiple active runs inside the same daemon

### What is explicitly *not* happening today

1. IM channels do not directly talk to the worker
2. IM channels do not inject supervisor instructions into panes
3. external systems do not yet push structured review results into a session-scoped mailbox
4. the daemon does not yet expose a general-purpose external event ingest API

---

## Why IM Does Not Yet Need Inbox / Subscription

This is the easiest place to get confused.

At first glance Telegram/Lark look "event-like" because they are asynchronous and message-based. But their current role is narrower:

- they are **command adapters**
- over a **canonical operator action layer**
- for a **session/run-first control plane**

That is why today's IM layer works without inbox/subscription:

1. the operator explicitly issues a command
2. the adapter resolves a run through [RunContext](/Users/chris/workspace/lite-harness-supervisor/supervisor/operator/run_context.py:42)
3. the canonical operator action executes
4. the result returns synchronously or as a bounded async job

That is not the same as:

- subscribe to external sources
- materialize per-consumer unread items
- ack processing later

So the absence of inbox/subscription in IM today is not a flaw. It reflects the current product scope:

> **operator commands, not deferred external-event orchestration**

---

## Current Collision Model: What Can And Cannot "Clash"

The user's concern is correct: if we have worker execution, operator commands, and future external review callbacks, we need to know what can collide.

## 1. User ↔ supervisor and supervisor ↔ agent do not use the same lane

These two conversations are separated:

- **user/operator ↔ supervisor** uses CLI/TUI/IM -> daemon IPC
- **supervisor ↔ worker** uses sidecar loop -> execution surface read/inject

That separation is good. It prevents IM from becoming a parallel control plane.

## 2. Pause/resume can race in time, but not in ownership

Example:

1. run thread injects a next-step instruction
2. operator sends `/pause`
3. daemon marks the run's `stop_event`
4. run loop exits at the next interruption check

So:

- the run may have already seen the injected text
- but there is still only one owner of execution state

This is a **timing race**, not an ownership race.

## 3. Read-only operator jobs do not fight the run loop

Explain/drift/clarification jobs read run state and logs, but they do not directly advance or inject the run.

That is why they can safely be asynchronous background jobs.

## 4. The missing clash policy is for deferred external events

This is the real gap.

If a GitHub review result arrives while a run is active, today there is no canonical answer to:

- which session owns it?
- should it interrupt the worker?
- should it wait until a safe point?
- should it notify the operator only?

That is exactly the problem the future event plane must solve.

---

## The Review Problem Is A Deferred External Task Problem

All three target scenarios share the same shape.

### Scenario 1: GitHub automated review

1. worker finishes implementation and pushes branch/PR
2. supervisor or operator requests GitHub review / waits for bot review
3. result comes back later as review comments / check failures / approval
4. system must correlate that result to the right session/run
5. coding agent must be resumed or notified to act

### Scenario 2: proactive external model/agent review

1. supervisor/operator explicitly launches a review with another model or agent
2. request becomes an external task
3. result returns later
4. the original coding session must be told whether to fix, continue, or wait

### Scenario 3: plan/architecture review before implementation

1. plan/spec is written
2. another reviewer agent/model/human reviews it asynchronously
3. feedback returns
4. only then should the implementation session proceed

The common pattern is:

> **issue now, wait later, correlate result, wake the right session at the right policy boundary**

That is larger than IM and smaller than a general multi-agent platform.

---

## The Needed Abstraction: A Session-First Event Plane

If this system grows to support asynchronous review well, the right abstraction is not "agent inbox" in the abstract.

The right abstraction is:

> **a session-scoped, daemon-owned event plane for deferred external work**

That means:

- events attach to a session/run first
- not to an abstract agent identity first
- all control consequences still route through the daemon-owned control plane

### Session identity note

The current repo is already philosophically session-first, but it does **not** yet expose one frozen, cross-surface `session_id` object everywhere.

Today the closest ingredients are:

- run identity (`run_id`)
- execution surface identity (for example tmux session/pane or transcript session id)
- worktree/runtime-root identity
- transcript discovery helpers in `supervisor/session_detect.py`

For the future event plane, this must be made explicit:

> **`session_id` should become the stable correlation target for deferred external work, while `run_id` remains optional for pre-run or plan-stage review flows.**

Without this, the system would drift into ad hoc correlation by:

- `run_id` only
- worktree path only
- provider-side refs only

That would be too fragile for long-lived asynchronous review workflows.

---

## Proposed First-Class Objects

These are not implemented yet. They are the architectural contract for future work.

### `ExternalTaskRequest`

A request issued by the supervisor/operator plane to another system.

Suggested fields:

- `request_id`
- `session_id`
- `run_id` (optional for pre-run plan review)
- `phase`: `plan | execute | post_implement | finish`
- `task_kind`: `review | ci_wait | approval_wait | consultation`
- `provider`: `github | codex | claude | human | telegram | lark | future`
- `target_ref`: PR number, review thread, external job id, etc.
- `blocking_policy`: `block_session | notify_only | advisory_only`
- `created_at`
- `status`: `pending | in_flight | completed | failed | expired`

### `ExternalTaskResult`

A normalized result that returns later from an external system.

Suggested fields:

- `result_id`
- `request_id`
- `session_id`
- `run_id`
- `provider`
- `result_kind`: `review_comments | approval | change_request | ci_failure | ci_success | analysis`
- `summary`
- `payload`
- `occurred_at`

### `SessionMailboxItem`

A durable, session-scoped item representing deferred external work that has arrived and has not yet been fully acted on.

Suggested fields:

- `mailbox_item_id`
- `session_id`
- `run_id`
- `source_kind`: `external_review | operator_note | future_external_event`
- `request_id`
- `summary`
- `payload`
- `delivery_status`: `new | surfaced | acknowledged | consumed`
- `requires_wake`: boolean

### `WakeDecision`

A small control object that decides how a session should be notified after a mailbox item lands.

Suggested values:

- `notify_operator`
- `wake_worker`
- `defer`
- `record_only`

This is intentionally separate from worker instruction injection.

### `SessionWait`

A durable wait record saying that a session/run is intentionally waiting on an external result.

Suggested fields:

- `session_id`
- `run_id`
- `wait_kind`: `external_review | ci | approval`
- `request_id`
- `entered_at`
- `deadline_at`
- `resume_policy`

---

## Architectural Rule: Wakeup And Execution Are Separate

This rule is load-bearing.

The system must separate:

1. **event delivery / wakeup**
2. **execution control**

Why:

- external review results are often lightweight facts
- worker execution injection is heavy control semantics
- mixing them would recreate the same fragility the runtime has been trying to remove

So the contract should be:

- event plane says: "there is something new for this session"
- runtime control plane decides: "does that become operator notification, pause, or worker follow-up instruction?"

This is the exact point where our design should remain different from AgentInbox:

- we may want a mailbox-like layer
- but we should **not** downgrade current-node worker control to generic inbox wakeup

---

## Proposed Layered Architecture For Async Review

## Layer 1: External Review Source Drivers

Examples:

- GitHub PR review poller/webhook
- CI result poller/webhook
- external model review driver
- delegated reviewer-agent adapter

Responsibilities:

- talk to the external system
- transform raw provider output into canonical request/result records
- never mutate run state directly

## Layer 2: Awaited External Task Registry

The daemon needs a registry of "what this session is waiting for."

Responsibilities:

- create `ExternalTaskRequest`
- correlate returning `ExternalTaskResult`
- track outstanding `SessionWait`
- detect timeout / expiry / duplicate delivery

This is the minimum thing we need before jumping to a full shared-source/subscription system.

## Layer 3: Session Mailbox / Event Store

Once a result returns, it must land durably before any wake policy runs.

Responsibilities:

- persist a `SessionMailboxItem`
- append canonical session event(s)
- preserve source payload for audit/replay
- allow later ack/consumption if needed

This is the closest place where an "Inbox" concept becomes useful in our architecture.

But it should be a **session mailbox**, not an abstract agent inbox.

## Layer 4: Wake / Resume Policy

After the mailbox item lands, the system decides what to do.

Responsibilities:

- notify operator only
- wake worker when safe
- keep waiting
- require human approval before resume

This layer must remain daemon-owned and policy-driven.

---

## How The Three Review Scenarios Fit This Model

## Scenario 1: GitHub automated review after implementation

### Proposed flow

1. worker completes implementation and pushes PR
2. supervisor/operator issues `ExternalTaskRequest(task_kind=review, provider=github, blocking_policy=block_session)`
3. session enters a wait record, such as `SessionWait(wait_kind=external_review)`
4. GitHub review bot/checks return later
5. provider adapter emits `ExternalTaskResult`
6. daemon ingests result, appends canonical events, stores `SessionMailboxItem`
7. wake policy decides:
   - if result is "changes requested", wake worker or operator depending on policy
   - if result is "approved", unblock finish/merge path

### Important rule

GitHub callbacks/pollers must **not** directly inject "fix these comments" into the pane.

They must:

1. land as a session-scoped external result
2. let the daemon decide how/when the coding session continues

## Scenario 2: proactive model/agent review

### Proposed flow

1. supervisor/operator explicitly launches an external review job
2. external reviewer receives a durable request id and correlation target
3. result returns later as `ExternalTaskResult`
4. result lands in the same session mailbox/event plane
5. wake policy chooses:
   - operator notify only
   - worker wake with a synthesized review-fix instruction
   - pause until explicit approval

### Important rule

This should not require the external reviewer to share the same pane or even the same daemon.

All that matters is:

- stable correlation
- durable return path
- daemon-owned resume policy

## Scenario 3: plan/architecture review before implementation

This is slightly different because it may happen **before** a supervised execute run starts.

### Proposed flow

1. planning session creates a plan/spec
2. plan review is issued as an `ExternalTaskRequest`
3. result comes back later
4. mailbox item is attached to the planning session/spec context
5. only after approval/changes are resolved does attach/execute proceed

### Important rule

This means the event plane should be able to attach to:

- a live run
- or a pre-run session/spec context

So `run_id` may be optional, but `session_id` should not be.

---

## Proposed Synchronization Rules For The Future Event Plane

If we build this, the following rules should be frozen.

### Rule 1: external adapters never mutate run state directly

No GitHub poller, CI webhook, Telegram callback, or external reviewer adapter should directly:

- call `terminal.inject()`
- rewrite `state.json`
- advance current node

They may only submit canonical events into the daemon-owned control plane.

### Rule 2: mailbox write precedes wake policy

An arriving external result must be durably written before any notification or resume decision is made.

This prevents:

- lost review results
- wake-without-audit
- duplicated recovery logic

### Rule 3: worker wake is always mediated

Waking a coding worker is not the same as receiving an external result.

The daemon must decide:

- whether the current run is at a safe interruption boundary
- whether the result is advisory or blocking
- whether the result should become a worker instruction or only a human notification

### Rule 4: session-first correlation beats provider-first routing

The event plane should correlate:

- `request_id -> session_id/run_id`

before it worries about provider-specific routing.

This keeps the system aligned with the existing architecture.

### Rule 5: explicit waiting beats implicit silence

If the supervisor is waiting on an external review result, that should be represented explicitly as a wait record or dedicated runtime/session state.

Otherwise the system will look idle and ambiguous rather than intentionally blocked.

---

## Current Gaps Relative To This Design

Today the repo still lacks:

1. a daemon-owned external task/request registry
2. a session-scoped mailbox item model
3. a canonical external-event ingest API
4. a dedicated wait model for "waiting on review/CI"
5. a wake policy that separates external-result arrival from worker control injection

What the repo **already has** that this design can reuse:

1. session-first run model
2. daemon IPC as canonical control entrypoint
3. operator channel adapters that already respect the control-plane boundary
4. durable runtime artifacts (`state.json`, `session_log.jsonl`)
5. notification layer for human/operator alerting
6. async operator jobs for non-blocking explanation/diagnosis

So this is not a greenfield system. It is an extension of a system that already has most of the execution-side pieces.

---

## Recommended Implementation Direction

Do **not** start by importing the full AgentInbox object model.

Start with the minimum session-first extension:

### Slice A: document and freeze current concurrency semantics

- daemon lanes
- run thread ownership
- IM adapter boundaries
- worker/control separation

### Slice B: add canonical external task records

- `ExternalTaskRequest`
- `ExternalTaskResult`
- `SessionWait`

### Slice C: add session mailbox and ingest API

- durable mailbox item storage
- daemon `external_event_ingest` path
- correlation + dedup

### Slice D: add wake/resume policy for review flows

- notify operator
- wake worker
- defer
- record only

### Slice E: integrate the first real source

Likely first candidates:

1. GitHub review/check result return path
2. supervisor-issued external model review result return path

Only after those work should the system consider more general shared-source/subscription abstractions.

---

## One-Line Design Summary

`thin-supervisor` should not become an agent-first event bus.

It should become:

> **a session-first deterministic runtime with a separate daemon-owned event plane for deferred external work**

That is the architecture that best fits:

- the current codebase
- the current IM/channel philosophy
- the future asynchronous review workflows
- and the requirement that human/operator and worker interactions must not collide unpredictably.
