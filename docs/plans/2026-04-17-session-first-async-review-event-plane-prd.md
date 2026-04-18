# Session-First Async Review Event Plane Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** add the smallest session-first event plane that lets `thin-supervisor` issue asynchronous review work, wait for results, correlate those results back to the right session/run, and then let the daemon-owned control plane decide whether to notify, defer, pause, or wake the coding worker.

**Architecture:** keep the current runtime core deterministic and session-first. Add a separate deferred-work path for external review and CI results: (1) register an external task request, (2) persist a session-scoped wait, (3) ingest the returning result into a durable mailbox/event store, and (4) run a daemon-owned wake policy that decides what happens next. Do not turn adapters into parallel control planes, and do not let external sources inject directly into worker panes.

**Tech Stack:** Python 3.10, `supervisor/daemon/server.py`, `supervisor/daemon/client.py`, `supervisor/loop.py`, `supervisor/operator/*`, `supervisor/storage/state_store.py`, `supervisor/notifications.py`, future GitHub connector / review driver adapters, pytest.

---

## Problem Statement

The current system is strong at synchronous live supervision:

- observe one worker
- parse checkpoints
- gate / verify / recover
- inject the next instruction

It is weak at deferred collaboration:

- ask another system to review something
- wait minutes later for the answer
- route the answer back to the right session
- decide whether to wake the worker or only notify the operator

That gap shows up in three real workflows:

1. **GitHub automated review**
   - implementation is pushed
   - GitHub review bot / checks return later
   - coding session must resume and fix issues

2. **Proactive external review**
   - supervisor/operator asks another model or agent to review code
   - result returns later
   - coding session must be told whether to change anything

3. **Plan / architecture review**
   - spec or plan is reviewed before implementation
   - feedback returns later
   - only then should execution proceed

The current IM layer does not solve this problem because it is a **command adapter**, not a mailbox or external event plane.

---

## First Release Scope

This PRD intentionally does **not** build a general event platform.

### In scope for the first implementation line

1. **Active-run asynchronous review return path**
   - request external review while a run/session already exists
   - persist the request
   - persist the returning result
   - decide notify / wake / defer

2. **Two concrete review sources**
   - GitHub review/check return path
   - supervisor-issued external review return path

3. **Operator visibility**
   - the operator can see that a session is waiting on review
   - the operator can inspect mailbox items and task status

### Deferred to follow-on slices

1. generic shared-source/subscription framework
2. agent-first identity model
3. worker-direct chat or chat-style review threads
4. a rich inbox UI
5. full pre-run plan-review UX

The object model should support later plan-stage review, but the first implementation target is the **active-run review return path**.

---

## Identity Model

This PRD uses two distinct identifiers. Both must be treated as first-class:

- **`session_id`** — a durable logical correlation key for a task or intent.
  A single session_id may be associated with 0..N run_ids across its lifetime.
  The session outlives any individual run. It is the key external review
  results correlate to.

- **`run_id`** — one execution attempt / controller instance.
  A run is short-lived; it is started, may be paused/resumed, and eventually
  terminates. A new attempt at the same logical task gets a new run_id under
  the same session_id.

Relationship:

- every run belongs to exactly one session
- a session may currently have zero active runs (e.g., plan phase, or review
  arrived after the last run terminated)
- external task requests and results correlate to session_id first; run_id
  is recorded when known but is optional

This identity model is load-bearing for the rest of the PRD. Rule 1
("Session-first remains primary") depends on it.

---

## Frozen Design Rules

### Rule 1: Session-first remains primary

Correlation keys must resolve to a session first.

For first release:

- every active run gets a durable `session_id`
- `run_id` remains the execution identifier
- external work is correlated by `session_id` and optionally `run_id`

Do not build this around an abstract local agent identity.

### Rule 2: External adapters never mutate run state directly

No GitHub adapter, CI adapter, IM callback, or external reviewer adapter may directly:

- call `terminal.inject()`
- update `state.json`
- advance a node
- bypass daemon IPC

They may only submit canonical request/result records to the daemon-owned event plane.

### Rule 3: Durable write precedes wake policy

When an external result arrives, the order must be:

1. persist the result
2. persist / update mailbox item
3. append canonical session event(s)
4. then compute wake policy

Never notify or inject before durable write.

### Rule 4: Wakeup and execution are separate

An external result arriving is **not** the same thing as a worker instruction.

The event plane may say:

- "this session has a new review result"

Only the daemon-owned wake policy may decide:

- notify operator
- wake worker
- defer until safe
- record only

In v1, the sidecar loop (`supervisor/loop.py`) remains passive with respect
to the event plane. It does not scan the mailbox, does not evaluate wake
decisions, and does not inject review-follow-up instructions on its own.
All wake decisions are made by the daemon-owned wake policy and routed
through existing daemon-to-worker mechanisms. `loop.py` may read wait state
only to render accurate status; it never acts on it.

### Rule 5: Wait must be explicit

If a session is waiting on external review, the system should say so explicitly.

Do not represent this as silence or hidden state.

### Rule 6: First release is active-run at request time, session-scoped at return time

The first shipped workflow issues external review requests from an active run.
However, result ingestion must not require the run to still be alive.

- request side: v1 workflows start from an active run
- return side: results must correlate to session_id and land durably even if
  no run is currently attached
- plan-stage review (no run_id at request time) is supported by the object
  model but not the shipped UX

This means lifecycle ownership is session-scoped, not run-scoped. The mailbox
and waits belong to the session.

---

## Proposed First-Class Objects

### `ExternalTaskRequest`

A request created when the system asks another system to do deferred work.

Required fields:

- `request_id`
- `session_id`
- `run_id` *(optional — present iff the request was issued from an active run)*
- `phase`: `execute | post_implement | finish | plan`
- `task_kind`: `review | ci_wait | approval_wait | consultation`
- `provider`: `github | external_model | external_agent | future`
- `target_ref`
- `blocking_policy`: `block_session | notify_only | advisory_only`
- `status`: `pending | in_flight | completed | failed | expired`
- `created_at`
- `updated_at`

### `ExternalTaskResult`

The normalized result that comes back later.

Required fields:

- `result_id`
- `request_id`
- `session_id`
- `run_id` *(optional — recorded if known at correlation time; may be absent if the originating run has ended)*
- `provider`
- `result_kind`: `review_comments | approval | change_request | ci_failure | ci_success | analysis`
- `summary`
- `payload`
- `occurred_at`

### `SessionWait`

A durable record that the session is waiting for an external result.

Required fields:

- `wait_id`
- `session_id`
- `run_id` *(optional — waits are session-scoped; run_id is recorded if one is attached)*
- `request_id`
- `wait_kind`
- `status`: `waiting | satisfied | expired | cancelled`
- `resume_policy`
- `entered_at`
- `resolved_at`
- `deadline_at` *(see Expiry Lifecycle below for who sets and sweeps this)*

### `SessionMailboxItem`

A durable item representing new deferred work that has arrived for a session.

Required fields:

- `mailbox_item_id`
- `session_id`
- `run_id` *(optional — mailbox items are session-scoped)*
- `request_id`
- `source_kind`
- `summary`
- `payload`
- `delivery_status`: `new | surfaced | acknowledged | consumed`
- `wake_decision`
- `created_at`
- `updated_at`

### `WakeDecision`

The daemon-owned control outcome after a result lands.

Allowed values:

- `notify_operator`
- `wake_worker`
- `defer`
- `record_only`

---

## Persistence Model

First release should reuse the current runtime-root storage style rather than introducing a database.

### New shared artifacts under `.supervisor/runtime/shared/`

- `external_tasks.jsonl`
  - append-only request/result lifecycle records
- `session_waits.jsonl`
  - durable wait records
- `session_mailbox.jsonl`
  - durable mailbox items and delivery-state transitions

### Existing per-run artifacts to reuse

- `state.json`
- `session_log.jsonl`

### Required event recording

Every significant external-task transition must also append a canonical run/session event such as:

- `external_task_requested`
- `external_task_result_received`
- `session_wait_entered`
- `session_wait_resolved`
- `session_mailbox_item_created`
- `session_mailbox_item_surfaced`
- `session_mailbox_item_consumed`
- `wake_decision_applied`

This keeps replay/postmortem aligned with the new plane.

---

## Expiry Lifecycle

`ExternalTaskRequest.status` and `SessionWait.status` both support an `expired`
value. To keep that from becoming paper state, v1 defines:

- `SessionWait.deadline_at` is set at wait creation from the request's
  `blocking_policy` and `task_kind`. Default deadlines per task_kind are
  defined in the wake-policy module.
- A daemon-owned sweep (runs on daemon tick, no new thread required) checks
  open waits every N seconds; any past-deadline wait transitions to
  `status=expired` and emits `session_wait_expired` plus a mailbox item with
  `wake_decision=notify_operator`.
- Expiry never auto-wakes a worker; the operator must decide.
- The associated `ExternalTaskRequest` transitions to `expired` when its wait
  expires with no result.

Follow-on slices can add per-request overrides and operator commands to
extend or cancel deadlines.

---

## Runtime / UX Contract For First Release

### Session states do not become a second state machine

Do **not** add a second top-level execution state machine for event handling.

Instead:

- keep the current run `TopState` model
- add session wait/mailbox records beside it
- derive operator-visible "waiting on review" metadata from those records

This avoids turning the runtime into two competing control systems.

### Operator visibility requirements

At minimum:

- `status`, `observe`, or a targeted review command can show outstanding waits
- operator can list mailbox items for a run/session
- operator can see whether a result has been surfaced, acknowledged, or consumed

### Worker wake requirements

Worker wake is daemon-mediated and bounded:

- if the run is not in a safe state, result is recorded and surfaced to operator only
- if the run is paused waiting for review and policy permits, wake the worker with a synthesized review-follow-up instruction
- if the result is advisory only, record and notify but do not resume automatically

---

## Review-Follow-Up Instruction Synthesis

When the wake policy decides `wake_worker` in response to a review result,
the worker needs a concrete instruction. v1 commits to the simplest option
that preserves auditability:

- v1: **structured passthrough**. The synthesized instruction is a templated
  wrapper around the normalized `ExternalTaskResult.summary` and `payload`.
  No LLM call on the synthesis path. The template is version-pinned and
  recorded in the session event so replays are deterministic.
- The template includes: provider, result_kind, summary, and a ref back to
  the `mailbox_item_id`.
- The synthesized instruction is persisted as a mailbox_item payload field
  before injection, so audit/replay can reconstruct exactly what the worker
  saw.

LLM-based summarization and operator-customizable templates are explicit
follow-on work. v1 must not silently invoke a model on the wake path.

---

## Product Flows

## Flow A: GitHub automated review after implementation

1. worker completes implementation and pushes PR
2. supervisor/operator records an `ExternalTaskRequest(provider=github, task_kind=review)`
3. system records `SessionWait(wait_kind=external_review)`
4. GitHub source driver polls or receives later result
5. driver submits `ExternalTaskResult`
6. daemon persists result and creates mailbox item
7. wake policy chooses:
   - `wake_worker` if changes must be addressed now and the run is resumable
   - `notify_operator` if human triage is required
   - `record_only` for non-blocking approvals

## Flow B: proactive external model/agent review

1. supervisor/operator asks another model/agent to review the work
2. request is registered as `ExternalTaskRequest(provider=external_model|external_agent)`
3. session wait is entered
4. result later returns through the same ingest path
5. daemon decides whether to synthesize a fix-up instruction, notify only, or defer

## Flow C: plan / architecture review

First release will not ship full plan-stage UX, but the same objects must support it later:

- request can exist with `phase=plan`
- `run_id` may be empty
- `session_id` remains the stable correlation key

Follow-on slices can add pre-run planning sessions on top of the same event plane.

---

## Files And Modules

### New modules

- Create: `supervisor/event_plane/models.py`
- Create: `supervisor/event_plane/store.py`
- Create: `supervisor/event_plane/ingest.py`
- Create: `supervisor/event_plane/wake_policy.py`
- Create: `supervisor/review_sources/base.py`
- Create: `supervisor/review_sources/github.py`
- Create: `supervisor/review_sources/external_review.py`

### Existing modules to modify

- Modify: `supervisor/domain/models.py`
- Modify: `supervisor/storage/state_store.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/daemon/client.py`
- Modify: `supervisor/operator/run_context.py`
- Modify: `supervisor/operator/actions.py`
- Modify: `supervisor/operator/api.py`
- Modify: `supervisor/notifications.py`
- Modify: `supervisor/app.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `README.md`

### Test files

- Create: `tests/test_event_plane_models.py`
- Create: `tests/test_event_plane_store.py`
- Create: `tests/test_event_plane_ingest.py`
- Create: `tests/test_wake_policy.py`
- Create: `tests/test_review_sources.py`
- Modify: `tests/test_daemon.py`
- Modify: `tests/test_app_cli.py`
- Modify: `tests/test_sidecar_loop.py`
- Modify: `tests/test_run_history.py`

---

## Task 1a: Define session identity semantics

**Files:**
- Modify: `supervisor/domain/models.py`
- Modify: `supervisor/storage/state_store.py`
- Test: `tests/test_domain_models.py` (or nearest existing home)

**Step 1: Write the failing test**

Cover:

- a session is a first-class durable record with its own id (not merely a field on `SupervisorState`)
- sessions persist across runs — creating a new run does not create a new session by default
- a session record survives after all its runs have terminated

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_domain_models.py -k session_identity`
Expected: FAIL because no session record type exists.

**Step 3: Write minimal implementation**

Introduce the session object/record and its durable storage. Run attachment
is Task 1b's concern — this task only establishes the session entity.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_domain_models.py -k session_identity`
Expected: PASS

**Scope note:** this task introduces the session object/record only. Run
attachment is Task 1b.

---

## Task 1b: Run adoption / inheritance of session identity

**Files:**
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/operator/run_context.py`
- Test: `tests/test_daemon.py`

**Step 1: Write the failing tests**

Cover:

- a newly registered run adopts an existing session_id if one applies
  (e.g., same worktree + same spec_id with an open session); otherwise it
  creates a new one
- resumed runs preserve the session_id they were previously attached to
- operator surfaces expose session_id as part of resolved run/session context
- a session with zero active runs is still resolvable by session_id

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_daemon.py -k session_id`
Expected: FAIL because run registration does not yet attach to sessions.

**Step 3: Write minimal implementation**

Wire run registration and resume paths to adopt or create a session_id.
Surface session_id in operator context and run listings.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_daemon.py -k session_id`
Expected: PASS

---

## Task 2: Add event-plane domain objects and durable store

**Files:**
- Create: `supervisor/event_plane/models.py`
- Create: `supervisor/event_plane/store.py`
- Modify: `supervisor/storage/state_store.py`
- Test: `tests/test_event_plane_models.py`
- Test: `tests/test_event_plane_store.py`

**Step 1: Write the failing tests**

Cover:

- `ExternalTaskRequest`, `ExternalTaskResult`, `SessionWait`, `SessionMailboxItem`
- append-only persistence under `.supervisor/runtime/shared/`
- dedup by `request_id` / `result_id` where appropriate

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_event_plane_models.py tests/test_event_plane_store.py`
Expected: FAIL because the modules do not exist.

**Step 3: Write minimal implementation**

Implement:

- dataclasses / typed serializers
- append-only store for request/result/wait/mailbox records
- helper queries for "latest request state", "open waits", "mailbox items by session"
- `run_id` is `Optional[str]` on all four dataclasses
  (`ExternalTaskRequest`, `ExternalTaskResult`, `SessionWait`,
  `SessionMailboxItem`). Correlation logic must work when `run_id` is `None`.
- `SessionWait` includes `deadline_at`; correlation/query helpers must treat
  `deadline_at` as queryable so the expiry sweep (see Expiry Lifecycle) can
  find past-deadline waits without scanning the full log.
- Tests must cover the `run_id=None` case from the start.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_event_plane_models.py tests/test_event_plane_store.py`
Expected: PASS

---

## Task 3: Add daemon IPC for external task registration and result ingest

**Files:**
- Create: `supervisor/event_plane/ingest.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/daemon/client.py`
- Test: `tests/test_event_plane_ingest.py`
- Test: `tests/test_daemon.py`

**Step 1: Write the failing tests**

Cover:

- daemon can register an external task request
- daemon can ingest an external task result
- result cannot be ingested for an unknown request/session
- ingest is idempotent on duplicate result delivery

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_event_plane_ingest.py tests/test_daemon.py -k external_task`
Expected: FAIL because daemon IPC actions do not exist.

**Step 3: Write minimal implementation**

Add daemon actions such as:

- `external_task_create`
- `external_result_ingest`
- `mailbox_list`
- `mailbox_ack`

Persist:

- request record
- session wait
- result record
- mailbox item
- corresponding canonical session events

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_event_plane_ingest.py tests/test_daemon.py -k external_task`
Expected: PASS

---

## Task 4: Add daemon-owned wake policy

**Files:**
- Create: `supervisor/event_plane/wake_policy.py`
- Modify: `supervisor/loop.py`
- Modify: `supervisor/notifications.py`
- Test: `tests/test_wake_policy.py`
- Modify: `tests/test_sidecar_loop.py`

**Step 1: Write the failing tests**

Cover:

- mailbox item is persisted before wake policy executes
- advisory review results become `notify_operator`
- blocking review results on resumable runs can become `wake_worker`
- unsafe/currently-busy states become `defer`
- source drivers never call `terminal.inject()` directly

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_wake_policy.py tests/test_sidecar_loop.py -k wake`
Expected: FAIL because no event-plane wake policy exists.

**Step 3: Write minimal implementation**

Create a wake-policy layer that:

- reads the current run state plus mailbox item
- decides `notify_operator | wake_worker | defer | record_only`
- records a `wake_decision_applied` event
- routes worker wake back through the daemon-owned execution path

`supervisor/loop.py` is touched only to surface wait/mailbox state into
observer output so status/explain/observe show accurate "waiting on review"
context. All control decisions live in `wake_policy.py` and are routed via
daemon IPC. The sidecar loop must not scan the mailbox or act on wake
decisions itself (see Rule 4).

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_wake_policy.py tests/test_sidecar_loop.py -k wake`
Expected: PASS

---

## Task 5: Integrate the first real async review source

**Scope decision:** v1 ships exactly **one** end-to-end source integration:
**supervisor-issued external review**. Rationale: it is fully under our
control (no webhook/auth friction), exercises the full
request → wait → result → wake path end-to-end, and lets us validate the
substrate before taking on GitHub's delivery semantics. GitHub follows as
Task 5b in a later slice.

The source-driver contract (`supervisor/review_sources/base.py`) must still
anticipate both kinds — the object model and ingest path already do — but
only one adapter is implemented now.

**Files:**
- Create: `supervisor/review_sources/base.py`
- Create: `supervisor/review_sources/external_review.py`
- Modify: `supervisor/daemon/server.py`
- Test: `tests/test_review_sources.py`

**Deferred to Task 5b (follow-on slice):**
- Create: `supervisor/review_sources/github.py`

**Step 1: Write the failing tests**

Cover:

- external-review adapter returns results through the canonical ingest path
- the source-driver base contract is usable by a future GitHub adapter
  without modification (structural test — verify the base interface accepts
  the shape a GitHub adapter would need)
- duplicate provider delivery does not duplicate mailbox consumption

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_review_sources.py`
Expected: FAIL because review-source adapters do not exist.

**Step 3: Write minimal implementation**

Ship a minimal source-driver contract plus the external-review adapter:

- `review_sources/base.py` defines the adapter interface
- `review_sources/external_review.py` implements it

The adapter may poll or reconcile, but it must only emit normalized result
records into the daemon event plane — never mutate run state directly.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_review_sources.py`
Expected: PASS

---

## Task 5b (deferred): GitHub review/check source adapter

Not in v1 scope. Ships after Task 5's external-review adapter has validated
the substrate end-to-end. Will add `supervisor/review_sources/github.py`
against the same base contract established in Task 5.

---

## Task 6: Surface waits and mailbox items in operator UX

**Files:**
- Modify: `supervisor/operator/api.py`
- Modify: `supervisor/operator/actions.py`
- Modify: `supervisor/app.py`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Test: `tests/test_app_cli.py`
- Test: `tests/test_run_history.py`

**Step 1: Write the failing tests**

Cover:

- operator can inspect open review waits for a run/session
- operator can inspect mailbox items
- history/export includes external task and mailbox events

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_app_cli.py tests/test_run_history.py -k mailbox`
Expected: FAIL because UX surfaces do not expose the new plane.

**Step 3: Write minimal implementation**

Expose:

- current wait status
- mailbox items
- last wake decision

Update docs so the review-return path is operator-visible and auditable.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_app_cli.py tests/test_run_history.py -k mailbox`
Expected: PASS

---

## Task 7: Extend to plan-stage review on top of the same primitives

**Files:**
- Modify: `supervisor/event_plane/models.py`
- Modify: `supervisor/operator/api.py`
- Modify: `supervisor/app.py`
- Test: `tests/test_event_plane_ingest.py`

**Step 1: Write the failing test**

Cover:

- a review request with `phase=plan` and no `run_id` still correlates by `session_id`
- result can be recorded without an active run

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_event_plane_ingest.py -k phase_plan`
Expected: FAIL because the first release only handles active runs.

**Step 3: Write minimal implementation**

Extend correlation and mailbox logic so pre-run planning sessions can use the same event plane without introducing a second control model.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_event_plane_ingest.py -k phase_plan`
Expected: PASS

---

## Verification

### Targeted verification

Run:

```bash
pytest -q \
  tests/test_event_plane_models.py \
  tests/test_event_plane_store.py \
  tests/test_event_plane_ingest.py \
  tests/test_wake_policy.py \
  tests/test_review_sources.py \
  tests/test_daemon.py \
  tests/test_sidecar_loop.py \
  tests/test_app_cli.py \
  tests/test_run_history.py
```

Expected:

- all new event-plane tests pass
- no source adapter directly injects into the worker
- duplicate result delivery is idempotent
- mailbox write always precedes wake decision

### Full verification

Run:

```bash
pytest -q
```

Expected:

- existing runtime/control-plane behavior remains green
- no regression to current checkpoint/verification/recovery semantics

---

## Success Criteria

The first release is successful when all of the following are true:

1. a run can register an asynchronous review request without blocking the daemon
2. a later external result can be durably correlated back to the right session/run
3. the result is visible as a session-scoped mailbox/event record before any wake action is taken
4. the daemon, not the source adapter, decides whether to notify, defer, or wake the worker
5. operator UX can explain why the session is waiting or what review result arrived
6. GitHub review/check results and supervisor-issued external reviews both use the same return path

---

## Immediate Next Step

Implement **Task 1a**, then **Task 1b**, then **Task 2** — three small PRs
rather than one bundled slice.

Reason:

- Task 1a freezes the session identity semantics in isolation
- Task 1b wires run registration/resume to that substrate
- Task 2 stacks the event-plane objects on top of a stable session contract
- splitting lets each load-bearing change be reviewed and reverted independently

Do **not** start with GitHub polling/webhooks before the event-plane substrate
exists. That would recreate the adapter-driven drift this PRD is trying to
prevent. GitHub is explicitly Task 5b (follow-on) — Task 5 ships
supervisor-issued external review first.
