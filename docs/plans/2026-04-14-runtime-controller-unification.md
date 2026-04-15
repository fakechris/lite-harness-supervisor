# Runtime Controller Unification Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify `thin-supervisor` runtime control so users can invoke `/thin-supervisor` without needing to understand daemon vs foreground internals, while preserving a separate debug-only foreground controller mode.

**Architecture:** Treat runtime control as a single product surface with one default controller model: daemon-owned runs. Reframe foreground behavior into two explicit concepts: an ordinary user-facing attached view over daemon-owned runs, and a separate debug-only foreground controller path for developers. Add explicit runtime detection so daemon-owned, auto-started, foreground-debug, and persisted local state can all be recognized, surfaced, and handled consistently.

**Tech Stack:** Python 3.10, `supervisor/app.py`, `supervisor/bootstrap.py`, `supervisor/daemon/server.py`, `supervisor/global_registry.py`, `supervisor/storage/state_store.py`, `supervisor/notifications.py`, tmux surfaces, pytest.

## Product Requirement

### Problem Statement

The runtime currently exposes multiple partially overlapping control models:

- background daemon
- zero-setup bootstrap that assumes daemon ownership
- `run foreground` as a separate controller
- pane ownership and local persisted state hints

This creates ambiguity in real use:

- users cannot tell whether a run is daemon-owned or foreground-owned
- bootstrap only reasons about daemon reachability, not full controller state
- foreground runs are not first-class in global discovery
- multi-session usage becomes hard to understand as more runs accumulate

The result is a runtime that works mechanically but does not present a coherent mental model.

### Target Runtime Model

There should be **one default runtime controller model**:

- **daemon-owned runtime** for all normal user-facing execution

There may be **one developer-only debug controller model**:

- **foreground controller** for local debugging only

There should be **one user-facing presentation model**:

- users attach to, observe, resume, stop, and inspect runs
- users do not need to reason about controller implementation details

### Non-Goals

- Do not build a full TUI in this phase
- Do not merge runtime and devtime CLIs again
- Do not expose optimizer/eval concepts to runtime users
- Do not preserve the current ambiguous “foreground is just another normal runtime path” behavior

## Required Runtime Semantics

### 1. Daemon-owned runtime is the default

For ordinary runtime use, `/thin-supervisor` and zero-setup bootstrap should always target daemon-owned runs.

Required behavior:

- if a daemon is already running for the project, reuse it
- if no daemon is running, auto-start one
- if a matching daemon-owned run already exists for the pane/spec/project context, show or reuse it instead of creating a duplicate

### 2. Foreground controller becomes debug-only

`thin-supervisor run foreground` should remain available, but it must be treated as an explicit developer/debug mode, not the normal runtime path.

Required behavior:

- foreground controller mode must be clearly labeled as debug-only
- zero-setup runtime bootstrap must not silently choose foreground controller mode
- runtime docs and skill docs must stop presenting foreground controller mode as equivalent to daemon-owned runtime

### 3. Attached view is not a separate controller

For user-facing runtime flows, “foreground” should mean “attached view” or “visible supervision surface”, not “a second controller implementation”.

Required behavior:

- user-facing runtime flows should attach to daemon-owned runs
- the system should distinguish:
  - controller mode
  - visibility/attachment mode
- status output should expose both clearly when relevant

### 4. Cross-mode detection must be explicit

The runtime must detect all controller states before deciding what to do.

Required behavior:

- detect active project daemon
- detect active daemon-owned runs for the current pane
- detect active foreground-owned runs for the current pane/project
- detect persisted orphaned local state
- detect pane ownership conflicts across worktrees/projects

The runtime should never create a new controller path until it has checked all of the above.

## Mode Interaction Rules

### Case A: daemon already running

If a project daemon already exists:

- `thin-supervisor daemon start` must not start a second daemon
- bootstrap must reuse the existing daemon
- runtime invocation must not start a foreground controller by default

### Case B: daemon not running

If no daemon exists:

- bootstrap should auto-start the daemon
- runtime invocation should continue against that daemon-owned controller

### Case C: foreground debug controller already running

If a foreground debug run is already active:

- bootstrap and runtime invocation must detect it
- the system must surface that a foreground-owned run already exists
- the user must be offered a coherent next action:
  - observe
  - continue using that run
  - stop it and start daemon-owned runtime

The system must not blindly launch a daemon-owned duplicate on the same pane.

### Case D: daemon exists and user launches debug foreground

This path must be explicit and constrained.

Required behavior:

- either reject it with a clear explanation, or
- allow it only when it targets a different pane/spec and is explicitly marked debug-only

The project should choose one policy and encode it consistently in code, docs, and tests.

**Recommendation:** reject by default for the same pane/project, allow only as an explicit debug path on a separate pane.

## Multi-Session Requirements

### Current problem

Single-session assumptions leak into the current runtime. With multiple sessions:

- pane ownership is hard to inspect mentally
- daemon-owned and foreground-owned runs are not presented uniformly
- persisted local state and active runs are split across different views

### Required minimum improvement (without TUI)

Before any TUI work, ship a minimum coherent multi-session operator surface.

Required behavior:

- `thin-supervisor status` must clearly separate:
  - active daemon-owned runs
  - active foreground-owned runs
  - orphaned persisted local states
- `thin-supervisor ps` must show daemon processes plus active run counts
- `thin-supervisor pane-owner` must identify whether the owner is daemon-owned or foreground-owned
- a user should be able to answer “what is running where?” from CLI output alone

### Deferred work

Full TUI is deferred.

Allowed follow-up after this plan:

- lightweight dashboard / top-style view
- richer session switching UI

Not required in this phase:

- curses-based TUI
- split-pane interactive process manager

## Acceptance Criteria

The work is not complete until all of the following are true:

1. `/thin-supervisor` always prefers daemon-owned runtime for ordinary user flows.
2. If a daemon already exists, bootstrap and runtime invocation reuse it instead of launching another controller.
3. If no daemon exists, bootstrap auto-starts one and continues.
4. If a foreground debug controller already exists, the runtime detects it and gives a clear next action instead of silently creating a conflicting run.
5. Same-pane controller conflicts are prevented across daemon-owned and foreground-owned modes.
6. `status`, `ps`, and `pane-owner` can distinguish daemon-owned runs, foreground-owned runs, and orphaned local state.
7. Docs and skills no longer present debug foreground mode as part of the normal happy path.
8. Multi-session CLI output is understandable without reading code or local state files directly.

## Implementation Plan

### Task 1: Define controller ownership model

**Files:**
- Modify: `supervisor/domain/models.py`
- Modify: `supervisor/global_registry.py`
- Modify: `supervisor/app.py`
- Test: `tests/test_global_registry.py`
- Test: `tests/test_app_cli.py`

**Step 1: Write failing ownership tests**

Add tests that require ownership metadata to include controller mode:

- daemon-owned
- foreground-owned
- orphaned persisted local state

**Step 2: Run targeted tests to confirm failure**

Run: `pytest -q tests/test_global_registry.py tests/test_app_cli.py`

Expected: failures showing controller ownership is not fully modeled.

**Step 3: Add explicit controller ownership fields**

Introduce a consistent ownership vocabulary:

- `controller_mode: daemon | foreground`
- `view_mode: attached | detached | debug`

Use it in pane ownership and run summaries.

**Step 4: Re-run targeted tests**

Run: `pytest -q tests/test_global_registry.py tests/test_app_cli.py`

Expected: PASS

### Task 2: Make bootstrap detect all controller states before acting

**Files:**
- Modify: `supervisor/bootstrap.py`
- Modify: `supervisor/daemon/client.py`
- Modify: `supervisor/app.py`
- Test: `tests/test_bootstrap.py`

**Step 1: Write failing bootstrap tests**

Cover:

- daemon already running
- no daemon running
- foreground-owned run already present
- pane locked by daemon-owned run
- pane locked by foreground-owned run

**Step 2: Run targeted tests to confirm failure**

Run: `pytest -q tests/test_bootstrap.py`

Expected: failures on foreground-detection and controller conflict handling.

**Step 3: Extend bootstrap detection**

Before reporting `Ready`, bootstrap must:

- detect daemon reachability
- detect pane ownership
- detect controller mode of the owner
- return a structured next action instead of only pass/fail where appropriate

**Step 4: Re-run targeted tests**

Run: `pytest -q tests/test_bootstrap.py`

Expected: PASS

### Task 3: Restrict normal runtime flows to daemon-owned control

**Files:**
- Modify: `skills/thin-supervisor/SKILL.md`
- Modify: `skills/thin-supervisor-codex/SKILL.md`
- Modify: `docs/getting-started.md`
- Test: `tests/test_primitives.py`

**Step 1: Write failing doc/skill assertions**

Add tests or assertions that the normal runtime happy path:

- uses bootstrap + daemon-owned registration
- does not recommend debug foreground mode

**Step 2: Run targeted tests**

Run: `pytest -q tests/test_primitives.py`

Expected: FAIL if old foreground/attach semantics still leak into the happy path.

**Step 3: Update skill and docs**

Make the normal runtime path explicitly:

- bootstrap
- approve
- register against daemon-owned runtime
- attach/observe as presentation, not controller choice

**Step 4: Re-run targeted tests**

Run: `pytest -q tests/test_primitives.py`

Expected: PASS

### Task 4: Constrain debug foreground mode

**Files:**
- Modify: `supervisor/app.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/global_registry.py`
- Test: `tests/test_app_cli.py`
- Test: `tests/test_daemon.py`

**Step 1: Write failing conflict tests**

Add tests for:

- foreground start while daemon-owned run owns the same pane
- daemon register while foreground-owned run owns the same pane
- allowed explicit debug foreground on a separate pane

**Step 2: Run targeted tests**

Run: `pytest -q tests/test_app_cli.py tests/test_daemon.py`

Expected: FAIL on cross-mode conflict policy.

**Step 3: Encode the policy**

Recommended policy:

- reject same-pane same-project foreground/daemon controller conflicts
- allow separate-pane explicit debug foreground
- label debug foreground clearly in output

**Step 4: Re-run targeted tests**

Run: `pytest -q tests/test_app_cli.py tests/test_daemon.py`

Expected: PASS

### Task 5: Improve multi-session CLI observability

**Files:**
- Modify: `supervisor/app.py`
- Modify: `supervisor/pause_summary.py`
- Modify: `supervisor/progress.py`
- Test: `tests/test_app_cli.py`

**Step 1: Write failing CLI presentation tests**

Require `status`, `ps`, and `pane-owner` to show:

- controller mode
- ownership mode
- orphaned vs active distinction

**Step 2: Run targeted tests**

Run: `pytest -q tests/test_app_cli.py`

Expected: FAIL on missing controller/view distinctions.

**Step 3: Improve CLI summaries**

Add concise, scannable output that answers:

- what is running
- where it is running
- who owns the pane
- whether it is daemon-owned, foreground-owned, or orphaned

**Step 4: Re-run targeted tests**

Run: `pytest -q tests/test_app_cli.py`

Expected: PASS

## Testing Matrix

Run all of the following before calling this complete:

```bash
pytest -q tests/test_bootstrap.py tests/test_global_registry.py tests/test_app_cli.py tests/test_daemon.py tests/test_primitives.py
pytest -q
```

Manual verification:

1. Start daemon in project A, then invoke runtime skill in project A again
2. Invoke runtime skill in a fresh project B with no daemon
3. Start a debug foreground run on pane X, then attempt runtime bootstrap on pane X
4. Start a debug foreground run on pane X, then attempt runtime bootstrap on pane Y
5. Inspect `thin-supervisor status`, `thin-supervisor ps`, and `thin-supervisor pane-owner <pane>`

Expected:

- no duplicate controller launch
- clear conflict handling
- clear ownership reporting
- no ambiguity about whether a run is daemon-owned or foreground-owned

## Developer Notes

- This plan is about runtime model coherence, not new features for end users
- The most important simplification is: ordinary users should not need to care about controller type
- Do not add TUI in this phase
- Do not preserve ambiguous semantics just for backward compatibility if they keep the runtime confusing

## Recommended Execution Order

1. Define controller ownership model
2. Extend bootstrap detection
3. Update skill/docs happy path
4. Constrain debug foreground mode
5. Improve multi-session CLI observability
