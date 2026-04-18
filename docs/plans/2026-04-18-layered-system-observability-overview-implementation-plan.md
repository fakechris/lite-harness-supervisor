# Layered System Observability And Overview Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a top-to-bottom observability stack so an operator can see the whole `thin-supervisor` system at a glance, then drill from system overview to session detail to raw evidence without manually hunting through logs.

**Architecture:** Keep the runtime session-first and per-worktree. Do not introduce a second control plane. Instead, add three missing observability primitives: (1) explicit `state_transition` events, (2) a passive `SystemSnapshot` / `SystemTimeline` aggregator that folds sessions, daemons, and event-plane backlog into one view, and (3) operator surfaces (`overview`, `status`, `observe`, `tui`) that all read from the same normalized projections.

**Tech Stack:** Python 3.10, `supervisor/operator/*`, `supervisor/app.py`, `supervisor/storage/state_store.py`, `supervisor/daemon/server.py`, `supervisor/loop.py`, `supervisor/event_plane/*`, `pytest`.

---

## Why This Exists

`thin-supervisor` already has good per-run evidence:

- `state.json`
- `session_log.jsonl`
- `decision_log.jsonl`
- event-plane logs (`external_tasks.jsonl`, `session_waits.jsonl`, `session_mailbox.jsonl`)

But the operator experience is still fragmented:

- `status` gives a global-ish session list
- `ps` gives daemon/process view
- `observe` gives run detail
- `mailbox` / `waits` are separate event-plane drill-down commands
- TUI gives a run-centric screen, not a system-centric one

The missing product capability is:

> “From one place, show me what the whole system is doing right now, what changed recently, and what needs attention.”

This plan deliberately solves that product gap without changing runtime ownership or creating a generic event bus.

## Non-Goals

- Do **not** redesign runtime ownership semantics
- Do **not** replace per-run `session_log.jsonl` with a global source of truth
- Do **not** let TUI or `overview` mutate run state
- Do **not** build a new inbox product; reuse the existing event-plane substrate
- Do **not** add LLM summarization or explainer dependency to the read path

## Frozen Design Rules

### Rule 1: Observability stays session-first

The primary read object remains the session/run record produced by `session_index`. The new system view aggregates over sessions; it does not replace them with agent identities or source subscriptions.

### Rule 2: `overview`, `status`, `observe`, and `tui` must read the same normalized objects

Do not create parallel collectors. `overview` may need new aggregation logic, but it must be built out of the same canonical session records and passive event-plane summaries that existing read surfaces use.

### Rule 3: State-machine transitions become first-class timeline events

An operator should not need to infer a `top_state` jump indirectly from surrounding events. When `top_state` changes, the system must emit an explicit `state_transition` event with:

- `from_state`
- `to_state`
- `reason`
- `source`
- `trigger_seq` (when known)

### Rule 4: Wake decisions and event-plane backlog must surface at the same level as run state

The operator’s top-level view must include:

- open waits
- new mailbox items
- acknowledged mailbox items
- latest wake decisions

The event plane is already real. The missing work is observability, not new control semantics.

### Rule 5: The sidecar loop remains passive with respect to system overview

This plan must not make `supervisor/loop.py` scan mailbox items or evaluate wake policy. It may append new observability events and expose richer summaries, but control remains in daemon-owned decision points.

## Desired User-Facing Outcome

From any directory, an operator should be able to answer:

1. How many daemons / live sessions / orphaned sessions exist?
2. Which runs are blocked, paused, waiting on review, or in recovery?
3. Which sessions have open waits or newly landed mailbox items?
4. What changed in the last few minutes at the system level?
5. For a selected session, what is the snapshot, timeline, event-plane backlog, and next action?

## Canonical Read Layers

### Layer 1: System Overview

One aggregated `SystemSnapshot` with:

- daemon counts
- live vs orphaned vs completed session counts
- counts by `tag` (`daemon`, `foreground`, `attached`, `recovery`, `paused-*`, `orphaned`, `completed`)
- event-plane backlog counts (`waits_open`, `mailbox_new`, `mailbox_acknowledged`)
- actionable alert counts (`paused_for_human`, `overdue_waits`, `mailbox_needing_attention`)
- recent `SystemTimelineEvent`s

### Layer 2: Session List

Existing canonical session list from `session_index`, augmented with:

- event-plane summary per session
- better “why this matters now” tags
- clear indication that a session is awaiting external review / has mailbox backlog

### Layer 3: Session Detail

Existing `observe` / TUI detail, augmented with:

- event-plane summary block
- latest external task request/result summary
- latest wake decision
- latest state transition summary

### Layer 4: Raw Evidence

Existing logs and exports:

- `state.json`
- `session_log.jsonl`
- `external_tasks.jsonl`
- `session_waits.jsonl`
- `session_mailbox.jsonl`
- `run export / summarize / replay`

No new raw evidence store is needed beyond a lightweight shared `system_events.jsonl`.

## New Canonical Objects

Add these passive models:

```python
SystemSnapshot
SystemCounts
SystemAlert
SystemTimelineEvent
RunEventPlaneSummary
```

### `RunEventPlaneSummary`

```python
{
  "waits_open": int,
  "mailbox_new": int,
  "mailbox_acknowledged": int,
  "requests_total": int,
  "latest_mailbox_item_id": str,
  "latest_wake_decision": str,
}
```

### `SystemCounts`

```python
{
  "daemons": int,
  "foreground_runs": int,
  "live_sessions": int,
  "orphaned_sessions": int,
  "completed_sessions": int,
  "waits_open": int,
  "mailbox_new": int,
  "mailbox_acknowledged": int,
}
```

### `SystemAlert`

```python
{
  "kind": "paused_for_human" | "overdue_wait" | "mailbox_backlog" | "orphaned",
  "count": int,
  "summary": str,
}
```

### `SystemTimelineEvent`

```python
{
  "event_type": str,
  "occurred_at": str,
  "scope": "system" | "session",
  "session_id": str,
  "run_id": str,
  "summary": str,
  "payload": dict,
}
```

## Storage Strategy

Keep all existing append-only logs. Add only one new shared log:

- `.supervisor/runtime/shared/system_events.jsonl`

Writers:

- daemon lifecycle points
- wake-policy application
- wait expiry sweep
- explicit state-transition helper when the event is useful at system level

This file is for overview aggregation only. It is **not** a source of truth for session state.

## Task 1: Add canonical observability models

**Files:**
- Modify: `supervisor/operator/models.py`
- Create: `supervisor/operator/system_overview.py`
- Test: `tests/test_operator_api.py`
- Test: `tests/test_tui.py`
- Create: `tests/test_system_overview.py`

**Step 1: Write the failing model/aggregation tests**

Add tests that assert:

- a `RunEventPlaneSummary` can round-trip to dict
- a `SystemSnapshot` reports counts and alerts deterministically
- a system overview built from 2 sessions + 1 daemon + event-plane counts yields the expected counters

**Step 2: Run the targeted tests to verify failure**

Run:

```bash
pytest -q tests/test_system_overview.py tests/test_operator_api.py -k overview
```

Expected: FAIL because the new models / module do not exist.

**Step 3: Add the minimal models and overview module**

Implement:

- `RunEventPlaneSummary`
- `SystemCounts`
- `SystemAlert`
- `SystemTimelineEvent`
- `SystemSnapshot`
- empty/passive folding helpers in `supervisor/operator/system_overview.py`

Do **not** wire CLI yet.

**Step 4: Re-run the targeted tests**

Run:

```bash
pytest -q tests/test_system_overview.py tests/test_operator_api.py -k overview
```

Expected: PASS for the new model-only tests.

**Step 5: Commit**

```bash
git add supervisor/operator/models.py supervisor/operator/system_overview.py tests/test_system_overview.py tests/test_operator_api.py
git commit -m "feat: add system overview models"
```

## Task 2: Introduce explicit `state_transition` events

**Files:**
- Modify: `supervisor/storage/state_store.py`
- Modify: `supervisor/loop.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/operator/api.py`
- Test: `tests/test_state_machine_transitions.py`
- Test: `tests/test_operator_api.py`
- Test: `tests/test_daemon.py`

**Step 1: Write the failing tests**

Add tests that assert:

- when a run transitions `ATTACHED -> GATING`, `session_log.jsonl` gets a `state_transition` event
- repeated no-op transitions do **not** emit duplicate `state_transition` records
- transition payload contains `from_state`, `to_state`, `reason`, `source`

**Step 2: Run the targeted tests to verify failure**

Run:

```bash
pytest -q tests/test_state_machine_transitions.py tests/test_operator_api.py -k state_transition
```

Expected: FAIL because no such event is emitted today.

**Step 3: Add a transition-and-record helper**

Do **not** put log writing inside `supervisor/domain/state_machine.py`.

Instead:

- keep `transition_top_state()` pure
- add a helper on `StateStore` or a small wrapper helper near runtime code that:
  - reads current state
  - calls `transition_top_state(...)`
  - if the state changed, appends `state_transition`

Adopt this helper in:

- `supervisor/loop.py`
- `supervisor/daemon/server.py`

Do **not** change replay code in `supervisor/history.py` to write live events.

**Step 4: Teach operator summaries about `state_transition`**

Update event summarization in `supervisor/operator/api.py` so timeline lines render as:

- `ATTACHED → GATING — agent checkpoint arrived`
- `VERIFYING → COMPLETED — final verification passed`

**Step 5: Re-run the targeted tests**

Run:

```bash
pytest -q tests/test_state_machine_transitions.py tests/test_operator_api.py tests/test_daemon.py -k state_transition
```

Expected: PASS.

**Step 6: Commit**

```bash
git add supervisor/storage/state_store.py supervisor/loop.py supervisor/daemon/server.py supervisor/operator/api.py tests/test_state_machine_transitions.py tests/test_operator_api.py tests/test_daemon.py
git commit -m "feat: emit explicit state transition events"
```

## Task 3: Add shared system events and `SystemSnapshot` aggregation

**Files:**
- Modify: `supervisor/storage/state_store.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/operator/session_index.py`
- Modify: `supervisor/operator/system_overview.py`
- Modify: `supervisor/event_plane/surface.py`
- Test: `tests/test_system_overview.py`
- Test: `tests/test_daemon.py`
- Test: `tests/test_session_index.py`

**Step 1: Write the failing tests**

Add tests that assert:

- shared `system_events.jsonl` can be appended and folded
- overview aggregation counts daemon/session/event-plane backlog correctly
- recent system timeline merges:
  - daemon lifecycle event
  - wake decision event
  - wait expiry event

**Step 2: Run the targeted tests to verify failure**

Run:

```bash
pytest -q tests/test_system_overview.py tests/test_daemon.py tests/test_session_index.py -k system_overview
```

Expected: FAIL because shared system events and aggregation do not exist.

**Step 3: Add shared system-event append/read helpers**

Implement append-only helpers for:

- `daemon_started`
- `daemon_stopped`
- `wake_decision_applied`
- `session_wait_expired`

Store them in:

- `.supervisor/runtime/shared/system_events.jsonl`

Do not make them authoritative. They are observability-only.

**Step 4: Extend passive event-plane summary**

Make `supervisor/event_plane/surface.py` capable of returning:

- latest mailbox item id
- latest wake decision

without changing any control behavior.

**Step 5: Implement `SystemSnapshot` folding**

Aggregate:

- `collect_sessions()`
- live daemon registry
- event-plane summaries per session
- shared system events across discoverable worktrees

Return:

- counts
- alerts
- recent timeline events
- the normalized session list

**Step 6: Re-run the targeted tests**

Run:

```bash
pytest -q tests/test_system_overview.py tests/test_daemon.py tests/test_session_index.py -k system_overview
```

Expected: PASS.

**Step 7: Commit**

```bash
git add supervisor/storage/state_store.py supervisor/daemon/server.py supervisor/operator/session_index.py supervisor/operator/system_overview.py supervisor/event_plane/surface.py tests/test_system_overview.py tests/test_daemon.py tests/test_session_index.py
git commit -m "feat: add shared system overview aggregation"
```

## Task 4: Add `thin-supervisor overview`

**Files:**
- Modify: `supervisor/app.py`
- Modify: `supervisor/daemon/client.py` (only if a daemon read helper is needed; prefer local read path first)
- Modify: `supervisor/operator/system_overview.py`
- Test: `tests/test_app_cli.py`

**Step 1: Write the failing CLI tests**

Add tests that assert:

- `thin-supervisor overview` prints top-level counters
- `thin-supervisor overview` prints alerts when waits/mailbox backlog exist
- `thin-supervisor overview --json` returns machine-readable output
- `thin-supervisor overview --watch` re-renders without crashing (unit-test the loop boundary, not full curses behavior)

**Step 2: Run the targeted tests to verify failure**

Run:

```bash
pytest -q tests/test_app_cli.py -k overview
```

Expected: FAIL because the command does not exist.

**Step 3: Implement the command**

Add:

- `thin-supervisor overview`
- `thin-supervisor overview --json`
- `thin-supervisor overview --watch`

Render sections in this order:

1. headline counts
2. alerts
3. recent system events
4. hottest sessions (top N actionable sessions)

Keep output plain and compact. Do not embed raw logs.

**Step 4: Re-run the targeted CLI tests**

Run:

```bash
pytest -q tests/test_app_cli.py -k overview
```

Expected: PASS.

**Step 5: Commit**

```bash
git add supervisor/app.py supervisor/operator/system_overview.py tests/test_app_cli.py
git commit -m "feat: add system overview command"
```

## Task 5: Surface event-plane summary in `status` and `observe`

**Files:**
- Modify: `supervisor/operator/models.py`
- Modify: `supervisor/operator/api.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/app.py`
- Test: `tests/test_operator_api.py`
- Test: `tests/test_app_cli.py`
- Test: `tests/test_run_history.py`

**Step 1: Write the failing read-surface tests**

Add tests that assert:

- `RunSnapshot` includes event-plane summary fields
- `observe` prints:
  - waits open
  - mailbox new/acknowledged
  - latest wake decision
- `status` marks sessions awaiting external review or mailbox attention

**Step 2: Run the targeted tests to verify failure**

Run:

```bash
pytest -q tests/test_operator_api.py tests/test_app_cli.py tests/test_run_history.py -k "event_plane or observe"
```

Expected: FAIL because the read surfaces do not yet include that summary.

**Step 3: Extend `RunSnapshot` and snapshot building**

Add a nested or flattened `event_plane` block to the run snapshot projection.

Populate it via the existing passive `event_plane.surface.summarize_for_session()` path.

Do not query daemon control state from the loop.

**Step 4: Update `cmd_status` and `cmd_observe`**

Make them print:

- `awaiting external review`
- `waits_open=...`
- `mailbox_new=...`
- `latest_wake_decision=...`

when present.

**Step 5: Re-run the targeted tests**

Run:

```bash
pytest -q tests/test_operator_api.py tests/test_app_cli.py tests/test_run_history.py -k "event_plane or observe"
```

Expected: PASS.

**Step 6: Commit**

```bash
git add supervisor/operator/models.py supervisor/operator/api.py supervisor/daemon/server.py supervisor/app.py tests/test_operator_api.py tests/test_app_cli.py tests/test_run_history.py
git commit -m "feat: surface event plane summaries in operator views"
```

## Task 6: Add TUI global mode

**Files:**
- Modify: `supervisor/operator/tui.py`
- Modify: `supervisor/operator/system_overview.py`
- Test: `tests/test_tui.py`

**Step 1: Write the failing formatting tests**

Add tests that assert:

- TUI can render a top banner with system counts
- TUI can render alerts / recent system events
- run rows can show event-plane attention markers

Keep these as non-interactive formatting tests, matching current TUI test style.

**Step 2: Run the targeted tests to verify failure**

Run:

```bash
pytest -q tests/test_tui.py -k "overview or system"
```

Expected: FAIL because no global mode formatting exists.

**Step 3: Implement a dual-mode TUI**

Keep one TUI entrypoint. Add:

- `global mode`
  - top banner: system counts
  - left pane: actionable sessions
  - center pane: selected session snapshot + timeline
  - right pane: alerts + event-plane summary + recent system events
- `run mode`
  - existing behavior, but enriched with event-plane detail

Do not add a second TUI command.

**Step 4: Re-run the targeted tests**

Run:

```bash
pytest -q tests/test_tui.py -k "overview or system"
```

Expected: PASS.

**Step 5: Commit**

```bash
git add supervisor/operator/tui.py supervisor/operator/system_overview.py tests/test_tui.py
git commit -m "feat: add global observability mode to tui"
```

## Task 7: Update docs and release notes

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/getting-started.md`
- Modify: `CHANGELOG.md`

**Step 1: Write the doc updates**

Document:

- the new `overview` command
- the four observability layers
- `state_transition` as a first-class event
- TUI global mode
- how `mailbox` / `waits` relate to `overview` and `observe`

**Step 2: Verify docs mention the shipped commands**

Run:

```bash
rg -n "overview|state_transition|mailbox|waits|tui" README.md docs/ARCHITECTURE.md docs/getting-started.md CHANGELOG.md
```

Expected: every new surface appears in at least one user-facing doc and one architecture doc.

**Step 3: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/getting-started.md CHANGELOG.md
git commit -m "docs: describe layered system observability"
```

## End-to-End Verification

Run the focused suites as each task lands, then run a final sweep:

```bash
pytest -q tests/test_system_overview.py \
  tests/test_state_machine_transitions.py \
  tests/test_operator_api.py \
  tests/test_app_cli.py \
  tests/test_tui.py \
  tests/test_daemon.py \
  tests/test_session_index.py \
  tests/test_run_history.py
```

Then run a wider smoke pass:

```bash
pytest -q tests/test_dashboard.py tests/test_session_index.py tests/test_notifications.py tests/test_event_plane_store.py
```

## Acceptance Criteria

This plan is complete when all of the following are true:

1. `thin-supervisor overview` shows a coherent global system snapshot from any directory.
2. Operators can see recent system-level changes without tailing raw logs.
3. `state_transition` is a first-class timeline event, not an inferred side-effect.
4. `status`, `observe`, and `tui` all expose event-plane backlog in a consistent shape.
5. TUI offers a top-level global mode and a detailed run mode without introducing a second collector path.
6. No runtime control semantics changed: the sidecar loop still does not scan mailbox items or evaluate wake decisions.
7. All targeted tests pass, and no existing observability surface regresses.

## Recommended Execution Order

Follow the tasks in order:

1. models
2. state-transition event emission
3. system overview aggregation
4. CLI `overview`
5. `status` / `observe` enrichment
6. TUI global mode
7. docs

Do **not** start with TUI. Without the passive models and aggregation layer first, the UI work will duplicate logic and drift immediately.

Plan complete and saved to `docs/plans/2026-04-18-layered-system-observability-overview-implementation-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
