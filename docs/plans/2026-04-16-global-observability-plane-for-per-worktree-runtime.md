# Global Observability Plane For Per-Worktree Runtime Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `thin-supervisor` globally observable from any directory without changing the current per-worktree runtime topology, so users can always see every live/orphaned/completed session before debugging individual runs.

**Architecture:** Keep execution per-worktree and keep daemon ownership local, but introduce one canonical global session collector/index that all operator surfaces read from. Unify `status`, `dashboard`, `tui`, and `observe` around the same session model instead of letting each command compose its own partial view from cwd-local state, live daemons, and pane locks.

**Tech Stack:** Python 3.10, `supervisor/app.py`, `supervisor/global_registry.py`, `supervisor/operator/tui.py`, `supervisor/daemon/client.py`, `supervisor/storage/state_store.py`, `pytest`.

## Problem Statement

The current runtime model is mechanically correct but operator visibility is not.

Execution today is scoped per worktree:

- each worktree has its own `.supervisor/`
- each worktree can auto-start its own daemon
- run state, spec, clarify, review, session log, and pause state live under that worktree

That part is fine.

The problem is that observability is not modeled globally:

- `thin-supervisor status` is mostly cwd-local
- `thin-supervisor ps` only shows live daemons / foreground controllers
- `pane-owner` only answers live pane lock questions
- `dashboard` is part-global, part-local
- `tui` has a broader scan than `status`, but still uses a separate collector path

This creates a product-level failure mode:

- a run can exist
- have real persisted state
- have a spec
- have a pause reason
- have a clear next action
- and still appear invisible from the “wrong” directory

That is exactly what happened in the observed `Phase 17` incident:

- root workspace showed:
  - no active run
  - no daemon
  - no pane owner
- but the real run existed under the child worktree:
  - `.worktrees/phase17-research-graph-visualization/.supervisor/...`
  - `run_89576d49897f`
  - `PAUSED_FOR_HUMAN`
  - pause reason: `no checkpoint received within delivery timeout after injection`

This is not merely a CLI polish issue. It means the product has no trustworthy global operator view.

## Desired Product Outcome

From **any** directory, an operator should be able to answer:

1. What sessions exist on this machine right now?
2. Which ones are live?
3. Which ones are paused/orphaned/completed but still actionable?
4. Which worktree owns each session?
5. What pane, spec, node, and next action belong to each session?

The user should not need to know:

- which worktree originally started the run
- whether the daemon is still alive
- whether the run is only on disk now
- whether `status` vs `dashboard` vs `tui` use different discovery rules

## Non-Goals

- Do **not** move to a global daemon in this phase
- Do **not** change runtime controller ownership semantics
- Do **not** auto-resume, auto-repair, or mutate paused runs as part of observability
- Do **not** redesign operator UX beyond what is necessary to make the view globally correct

This plan is about **visibility first**, not remediation first.

## Frozen Runtime / Observability Contract

### Rule 1: Runtime remains per-worktree

This plan preserves:

- one daemon per worktree
- worktree-local `.supervisor/`
- worktree-local persisted state

We are fixing the **view**, not the underlying runtime topology.

### Rule 2: There must be one canonical global session view

All operator-facing read surfaces must derive from the same session collector / index.

At minimum:

- `thin-supervisor status`
- `thin-supervisor dashboard`
- `thin-supervisor tui`
- `thin-supervisor observe`

must stop using divergent discovery logic.

### Rule 3: `status` becomes global-first

`thin-supervisor status` should answer:

> “What sessions exist anywhere that I can act on?”

Required default behavior:

- show sessions from all known worktrees
- include live daemon-owned runs
- include live foreground-debug runs
- include orphaned persisted runs
- include recently completed runs

Optional narrower behavior:

- `thin-supervisor status --local` may filter to current cwd/worktree

But the default must be global.

### Rule 4: Idle daemon shutdown must never erase session visibility

A daemon is allowed to exit when idle.

That must **not** make its runs invisible if they still have actionable local state.

After daemon shutdown, a run must remain globally visible if it is:

- orphaned
- paused for human
- completed and worth summarizing

### Rule 5: Worktree ownership must be explicit in every session view

Every session shown in the global view must carry:

- `worktree_root`
- `controller_mode`
- `run_id`
- `top_state`
- `current_node`
- `pane_target`
- `spec_path`
- `next_action`
- `last_update_at`
- `is_live`
- `is_orphaned`

The user should never need to guess which worktree a session belongs to.

## Canonical Session Model

Add one canonical session record shape used by all read surfaces.

Required fields:

```python
{
  "run_id": str,
  "worktree_root": str,
  "spec_path": str,
  "controller_mode": "daemon" | "foreground" | "local",
  "top_state": str,
  "current_node": str,
  "pane_target": str,
  "daemon_socket": str,
  "is_live": bool,
  "is_orphaned": bool,
  "is_completed": bool,
  "pause_reason": str,
  "next_action": str,
  "last_checkpoint_summary": str,
  "last_update_at": str,
}
```

This does **not** need to become a new source of truth file on disk.

It may be computed as a derived global view from:

- local `state.json`
- local `session_log.jsonl`
- live daemon `status()`
- global pane registry
- global daemon registry
- known worktrees registry

But all read commands must consume the same normalized shape.

## Worktree Discovery Rules

The system must discover sessions from more than just the current cwd.

The collector must scan worktrees from:

1. current cwd
2. `known_worktrees.json`
3. live daemon registry
4. live pane-owner registry
5. optional `git worktree list` for the current repo, when available

Reason for item 5:

- current incident already showed that root and child worktree can drift
- `known_worktrees.json` is helpful but not sufficient as the only discovery source
- if the repo itself knows about a linked worktree, the collector should be able to use that as read-only discovery input

This is still safe:

- discovery is read-only
- no mutation is needed
- this only broadens visibility

## Why The Current Mechanism Failed

The current failure is the combination of three behaviors:

### 1. Run state was created in the child worktree

The `Phase 17` run lives under:

- `/Users/chris/Documents/openclaw-template/.worktrees/phase17-research-graph-visualization/.supervisor/...`

That is correct.

### 2. The daemon shut down after idling

Also correct.

### 3. The root workspace CLI was then asked to answer a global question with a local-only command

Current `status` only inspects:

- current cwd daemon
- current cwd local state

So from root it answered “nothing here”.

That is the product bug.

The user’s question was global:

> What is running? Where did it go?

But the command semantics were local.

## Impact On The Current `Phase 17` Incident

### Important decision

This observability plan must **not** mutate current session state as part of phase 1.

Required phase-1 behavior:

- read existing run state
- surface it globally
- do not rewrite run ids
- do not rewrite spec paths
- do not resume or pause anything
- do not “heal” the session automatically

### Expected effect on the current incident

After this work lands, from the root workspace the user should see:

- an orphaned session owned by the `phase17-research-graph-visualization` worktree
- run id `run_89576d49897f`
- node `step_1_graph_api`
- state `PAUSED_FOR_HUMAN`
- next action pointing to resume using that worktree’s spec

This means:

- **yes**, the change affects what the user can see about the current incident
- **no**, it should not alter the runtime state of the incident

That is exactly the right behavior for phase 1.

Only after this visibility fix is in place should we continue debugging why the session stopped making progress after the second injection.

## Command Semantics After This Change

### `thin-supervisor status`

Default: global-first session list with clear buckets:

- active daemon-owned runs
- active foreground-debug runs
- orphaned persisted state
- recently completed runs

### `thin-supervisor status --local`

Restrict to current cwd/worktree only.

### `thin-supervisor ps`

Continue to answer a narrower question:

> Which daemon processes / foreground controllers are alive?

`ps` should remain process-oriented, not session-oriented.

### `thin-supervisor dashboard`

Must consume the same global session collector as `status`.

The difference should be presentation / drill-in only.

### `thin-supervisor tui`

Must also consume the same collector.

It may show richer views, but it must not see a different universe of runs than `status`.

### `thin-supervisor observe <run_id>`

Must resolve against the global session view, not only a live daemon path.

If the run is local/orphaned and no daemon is running, `observe` should still show:

- snapshot
- last checkpoint
- recent timeline
- worktree ownership
- pause reason

## Specific Change Plan

### Task 1: Freeze the canonical collector contract

**Files:**
- Create: `supervisor/operator/session_index.py`
- Modify: `supervisor/app.py`
- Modify: `supervisor/operator/tui.py`
- Test: `tests/test_session_index.py`

**Step 1: Write failing session-collector tests**

Add tests for:

- cwd root + child worktree orphaned run
- live daemon run in another worktree
- live foreground run in another worktree
- completed run in another worktree

**Step 2: Run tests to confirm failure**

Run:

```bash
pytest -q tests/test_session_index.py
```

Expected:

- failures because no unified collector exists yet

**Step 3: Introduce canonical session collector**

Create one collector API, e.g.:

```python
collect_sessions(*, local_only: bool = False) -> list[SessionRecord]
find_session(run_id: str) -> SessionRecord | None
```

This module owns:

- worktree discovery
- deduping
- normalization
- liveness/orphan/completed classification

**Step 4: Commit**

```bash
git add supervisor/operator/session_index.py tests/test_session_index.py supervisor/app.py supervisor/operator/tui.py
git commit -m "feat: add canonical global session collector"
```

### Task 2: Unify worktree discovery

**Files:**
- Modify: `supervisor/global_registry.py`
- Modify: `supervisor/operator/session_index.py`
- Test: `tests/test_global_registry.py`
- Test: `tests/test_session_index.py`

**Step 1: Write failing discovery tests**

Cover:

- known worktree visible even after daemon shutdown
- worktree discovered from git worktree list when registry is incomplete
- duplicate paths deduped by resolved path

**Step 2: Run targeted tests**

Run:

```bash
pytest -q tests/test_global_registry.py tests/test_session_index.py -k "worktree or discovery"
```

Expected:

- failures around incomplete discovery sources

**Step 3: Implement merged worktree discovery**

Collector must union:

- current cwd
- `list_known_worktrees()`
- daemon `cwd`
- pane owner `cwd`
- git worktree roots when available

All paths normalized with `.resolve()`.

**Step 4: Commit**

```bash
git add supervisor/global_registry.py supervisor/operator/session_index.py tests/test_global_registry.py tests/test_session_index.py
git commit -m "feat: unify worktree discovery for session observability"
```

### Task 3: Make `status` global-first

**Files:**
- Modify: `supervisor/app.py`
- Test: `tests/test_app_cli.py`

**Step 1: Write failing CLI tests**

Add tests requiring:

- `status` from root shows orphaned run in child worktree
- `status --local` only shows current worktree
- `status` buckets remain stable

**Step 2: Run targeted tests**

Run:

```bash
pytest -q tests/test_app_cli.py -k "status and worktree"
```

Expected:

- failures because current `status` is cwd-scoped

**Step 3: Reimplement `cmd_status()` on top of the collector**

Do not hand-scan cwd in `cmd_status()`.

Instead:

- call `collect_sessions(local_only=args.local)`
- bucket normalized session records
- print worktree root explicitly when the run is not in current cwd

**Step 4: Commit**

```bash
git add supervisor/app.py tests/test_app_cli.py
git commit -m "feat: make status global-first across worktrees"
```

### Task 4: Make `dashboard` and `tui` consume the same session universe

**Files:**
- Modify: `supervisor/app.py`
- Modify: `supervisor/operator/tui.py`
- Modify: `supervisor/operator/command_dispatch.py`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_tui.py`

**Step 1: Write failing parity tests**

Require that:

- `status`, `dashboard`, and `tui` see the same run ids
- root cwd can drill into child worktree orphaned run
- daemon shutdown does not hide persisted runs from dashboard/tui

**Step 2: Run targeted tests**

Run:

```bash
pytest -q tests/test_dashboard.py tests/test_tui.py -k "global or worktree or parity"
```

Expected:

- failures because current commands use different collectors

**Step 3: Refactor both command surfaces**

- `dashboard` must read from `collect_sessions()`
- TUI `collect_runs()` must either call the new collector directly or be deleted and replaced by it
- command dispatch should resolve runs against the same canonical session view

**Step 4: Commit**

```bash
git add supervisor/app.py supervisor/operator/tui.py supervisor/operator/command_dispatch.py tests/test_dashboard.py tests/test_tui.py
git commit -m "feat: unify dashboard and tui session visibility"
```

### Task 5: Make `observe` work for orphaned local runs without a live daemon

**Files:**
- Modify: `supervisor/app.py`
- Modify: `supervisor/operator/actions.py`
- Modify: `supervisor/operator/run_context.py`
- Test: `tests/test_app_cli.py`
- Test: `tests/test_operator_actions.py`

**Step 1: Write failing observe tests**

Cover:

- orphaned run in another worktree
- no live daemon
- observe still returns snapshot/timeline/pause reason

**Step 2: Run targeted tests**

Run:

```bash
pytest -q tests/test_app_cli.py tests/test_operator_actions.py -k "observe and orphaned"
```

Expected:

- failures because current observe assumes daemon path

**Step 3: Add local/orphaned observe fallback**

`observe` should:

- resolve the session globally
- use daemon RPC if live
- otherwise build response from local state + session log

**Step 4: Commit**

```bash
git add supervisor/app.py supervisor/operator/actions.py supervisor/operator/run_context.py tests/test_app_cli.py tests/test_operator_actions.py
git commit -m "feat: observe orphaned runs without live daemon"
```

### Task 6: Document the new operator mental model

**Files:**
- Modify: `docs/getting-started.md`
- Modify: `README.md`
- Modify: `docs/plans/2026-04-15-per-worktree-daemon-lifecycle-and-observability.md`

**Step 1: Update docs**

Required content:

- `status` is global-first
- `status --local` is cwd-only
- `ps` is process-oriented
- `dashboard`/`tui` share the same run universe
- daemon shutdown does not erase session visibility

**Step 2: Commit**

```bash
git add README.md docs/getting-started.md docs/plans/2026-04-15-per-worktree-daemon-lifecycle-and-observability.md
git commit -m "docs: define global observability plane semantics"
```

## Test Matrix

At minimum, final verification must cover:

1. root cwd sees child worktree orphaned run
2. root cwd sees child worktree completed run
3. root cwd sees child worktree live daemon run
4. root cwd sees child worktree foreground run
5. `status`, `dashboard`, and `tui` return the same run universe
6. `observe` works with and without a live daemon
7. daemon idle shutdown does not remove persisted session visibility
8. current incident shape reproduces:
   - root workspace
   - child worktree paused run
   - no live daemon
   - session still visible globally

## Rollout Safety

This work should be shipped in two layers:

### Phase 1

Read-path only:

- collector
- global status
- dashboard/tui parity
- observe fallback

No mutation to run state.

### Phase 2

Only after visibility is correct:

- re-debug the `Phase 17` timeout incident
- determine whether the Codex UI / delivery-ack path has another issue
- consider repair helpers or resume ergonomics

Do **not** mix phase 2 into the initial observability PR.

## Final Decision

The next engineering move should **not** be “fix the specific session first”.

The next move should be:

1. make the session globally visible from anywhere
2. make every operator surface agree on that session universe
3. only then continue root-cause debugging of the session itself

That is the fastest path to eliminating this entire class of confusion.
