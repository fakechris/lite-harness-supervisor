# Per-Worktree Daemon Lifecycle And Observability

> **For Claude/Codex:** treat this as a product + runtime contract document, not just a code refactor note.

## Goal

Keep the current **per-worktree daemon** architecture, but make it operationally safe and understandable:

- no unbounded accumulation of idle daemons
- clear lifecycle and shutdown semantics
- clear CLI visibility for multi-session and multi-worktree use
- explicit distinction between active, idle, foreground-debug, and orphaned state

This document intentionally assumes the current architecture remains in place for now:

- one daemon per workspace/worktree
- project-local runtime state under `.supervisor/`
- cross-worktree discovery through the global registry

## Terminology

### Workspace / Worktree

In this system, the daemon is not scoped to a Git repository identity. It is scoped to the **current working directory instance**.

That means:

- one main checkout = one daemon
- each extra `git worktree` = another daemon

This document uses **per-worktree daemon** as the correct term.

### Daemon-owned runtime

Normal runtime execution path. A worktree-local daemon controls one or more runs for that worktree.

### Foreground debug controller

Explicit debug-only controller created by `thin-supervisor run foreground`. It is not the default runtime model.

### Orphaned local state

Persisted run state left behind without an active controlling worker.

## Problem Statement

The current per-worktree daemon model is mechanically correct but has three product problems:

1. **Idle daemon accumulation**
   - worktree daemons are auto-started and reused
   - they do not currently exit after becoming idle
   - users who touch many worktrees over time will accumulate many idle Python processes

2. **Unclear status model**
   - users can see active runs, paused runs, completed runs, daemon-owned vs foreground-owned runs, and orphaned local state
   - but the lifecycle is still not framed as a single coherent runtime contract

3. **Multi-session observability remains CLI-fragmented**
   - support exists, but the product model is not fully explicit
   - users need to understand how `ps`, `status`, `pane-owner`, and `observe` fit together

This plan closes those gaps while keeping the current architecture.

## Architecture Decision

### Keep per-worktree daemon for now

We are **not** changing to a global daemon in this phase.

Reasons:

- worktree-local isolation is already implemented
- failure domains remain small
- persisted runtime state already lives under the worktree
- existing resume/orphan/pane-lock logic already assumes local daemon ownership

### Add lifecycle controls instead of changing topology

The problem to solve is not "multiple daemons exist".  
The problem to solve is:

- idle daemons do not retire themselves
- their lifecycle is not obvious to users

## Required Runtime Semantics

### 1. Daemon scope

Each worktree has at most one daemon-owned controller.

Required behavior:

- starting a daemon in a worktree that already has a running daemon must reuse, not duplicate
- bootstrap and register flows must reuse an existing daemon for that worktree
- daemon discovery must remain worktree-local first, then globally observable

### 2. Idle lifecycle

Per-worktree daemons must no longer live forever after the last run finishes.

Required behavior:

- if `active_runs == 0`
- and the daemon has been idle for at least `idle_shutdown_sec`
- and there is no active foreground ownership conflict
- the daemon should shut itself down cleanly

Recommended default:

- `idle_shutdown_sec = 600` seconds (10 minutes)

This value should be configurable.

### 3. Safe automatic restart

Idle shutdown must be invisible to normal users.

Required behavior:

- if a worktree daemon auto-exited while idle
- the next bootstrap/register/runtime invocation must auto-start it again
- the user should not need to distinguish:
  - "never started"
  - "stopped manually"
  - "auto-shut down while idle"

### 4. Activity accounting

Idle shutdown cannot be based only on run count. The daemon must track recent activity.

Required daemon activity timestamps:

- `started_at`
- `last_run_started_at`
- `last_run_finished_at`
- `last_client_contact_at`
- `last_state_change_at`

At minimum, the idle decision must consider:

- zero active runs
- no recent client contact
- no recent state transitions

### 5. Foreground coexistence

Foreground debug mode remains debug-only and must not block daemon lifecycle forever.

Required behavior:

- foreground runs do not count as daemon-owned active runs
- daemon idle shutdown must ignore unrelated foreground ownership in other panes/worktrees
- if a foreground run exists in the same worktree, status output must show it clearly
- idle shutdown must never silently kill a foreground controller, because it does not own it

## Idle Shutdown Requirements

### Functional requirements

1. The daemon must evaluate idle shutdown periodically while its accept loop is alive.
2. Idle shutdown must only occur when `active_runs == 0`.
3. Idle shutdown must not occur during:
   - delivery ack pending
   - verification in progress
   - stop/reap cleanup in progress
4. Idle shutdown must emit a session-independent daemon event into logs/registry.
5. Idle shutdown reason must be inspectable after the fact.

### Registry requirements

The global daemon registry should expose:

- `state: active | idle | shutting_down`
- `active_runs`
- `idle_for_sec`
- `last_client_contact_at`
- `last_run_finished_at`
- `worktree_root`

This allows CLI tools to show whether a daemon is:

- healthy and active
- safe but idle
- stale and likely dead

### CLI requirements

`thin-supervisor ps` must surface daemon lifecycle state clearly.

Example shape:

```text
PID      MODE     RUNS  IDLE   WORKTREE
81231    active   2     0s     ~/workspace/project-a
81302    idle     0     8m     ~/workspace/project-b
```

If a daemon is in idle grace period, the user should be able to tell that it will auto-exit soon.

## Clear Status Requirements

### Problem

`status` currently provides useful data, but the runtime mental model is still too implicit.

Users need to answer:

- what is actively running?
- who owns it?
- is this controlled by a daemon or by foreground debug mode?
- is this just persisted local state?
- what do I do next?

### Required status buckets

`thin-supervisor status` must explicitly separate:

1. **Active daemon-owned runs**
2. **Active foreground-debug runs**
3. **Paused/orphaned persisted local state**
4. **Completed local state worth summarizing**

### Output requirements

For each run-like item, show:

- run id
- controller mode
- current node
- pane target if known
- top state
- short reason
- next action

Example shape:

```text
Active runs:
  [daemon] run_aaa RUNNING node=step_2 pane=%3
    next: thin-supervisor observe run_aaa

Debug foreground runs:
  [foreground-debug] run_bbb RUNNING node=step_1 pane=%7
    next: inspect the foreground pane directly

Local persisted state:
  [orphaned] run_ccc PAUSED_FOR_HUMAN node=step_4 pane=%1
    reason: daemon exited while the run was in progress
    next: thin-supervisor run resume --spec ...
```

### Status behavior requirements

- `status` must not imply an item is active when it is only persisted
- `status` must not collapse daemon-owned, foreground-owned, and orphaned state into one list without labels
- `status` must prefer explicit labels over prose

## Multi-Session Observability Requirements

### Current capability baseline

The runtime already supports:

- multiple worktrees
- multiple daemons
- multiple tmux sessions
- pane ownership locking
- per-project local state

What it lacks is a sufficiently crisp user-facing observability contract.

### Required CLI observability model

Without introducing a TUI, the following commands together must fully explain the system:

#### `thin-supervisor ps`

Machine-level daemon summary:

- worktree root
- daemon pid
- daemon lifecycle state
- active run count
- idle duration

#### `thin-supervisor status`

Current worktree summary:

- active daemon-owned runs
- active foreground-debug runs
- orphaned local state
- completed local state

#### `thin-supervisor pane-owner <pane>`

Pane-level ownership:

- owning run id
- controller mode
- owning pid
- worktree root
- spec path if available

#### `thin-supervisor observe <run_id>`

Run-level event view:

- last checkpoints
- last verification
- current reason / pause summary

### Product requirement

A user managing multiple sessions must be able to answer:

1. what daemons exist on this machine?
2. which worktree owns each daemon?
3. which runs are active now?
4. which pane belongs to which run?
5. which items are truly active vs merely persisted?

without reading runtime files manually.

## Required Developer Work

### Slice 1: Idle daemon lifecycle contract

Implement:

- daemon idle timer
- last-activity tracking
- auto-shutdown when safe
- explicit daemon shutdown reason reporting

Files likely involved:

- `supervisor/daemon/server.py`
- `supervisor/global_registry.py`
- `supervisor/domain/models.py`
- `supervisor/app.py`
- `tests/test_daemon.py`
- `tests/test_app_cli.py`

### Slice 2: Registry and CLI state model

Implement:

- daemon lifecycle state in registry
- `idle_for_sec` / last activity fields
- `ps` output updates
- `status` bucket separation hardening

Files likely involved:

- `supervisor/global_registry.py`
- `supervisor/app.py`
- `tests/test_app_cli.py`
- `tests/test_global_registry.py`

### Slice 3: Foreground/debug clarity

Implement:

- explicit labeling in `status`
- explicit ownership display in `pane-owner`
- no ambiguity between debug foreground and daemon-owned runtime

Files likely involved:

- `supervisor/app.py`
- `supervisor/global_registry.py`
- `tests/test_app_cli.py`

## Acceptance Criteria

The work is not complete until all of the following are true:

1. A worktree daemon auto-exits after a configurable idle period with zero active runs.
2. The next normal runtime invocation auto-starts the daemon again without user intervention.
3. `thin-supervisor ps` shows which daemons are active vs idle.
4. `thin-supervisor status` clearly separates daemon-owned runs, foreground-debug runs, and orphaned persisted local state.
5. `thin-supervisor pane-owner` clearly shows controller mode and owning worktree.
6. A user with multiple worktrees and tmux sessions can answer "what is running where?" from CLI output alone.
7. No completed run thread remains indefinitely in daemon memory after reaping.
8. Idle shutdown does not kill active runs or corrupt persisted local state.

## Testing Plan

### Unit / targeted tests

- daemon idle timer starts only when active run count reaches zero
- daemon does not idle-exit while runs are active
- daemon registry shows active vs idle state
- foreground ownership remains visible in status/pane-owner
- stale/dead daemon registry entries are cleaned up

### Integration tests

- two worktrees, each with its own daemon
- one active, one idle
- one daemon auto-exits and later auto-restarts on bootstrap
- one foreground-debug run plus one daemon-owned run in another pane

### Manual verification

1. Start runtime in two separate worktrees
2. Let one daemon go idle
3. Verify `ps` shows active vs idle correctly
4. Wait for idle shutdown
5. Invoke runtime again in that worktree
6. Verify auto-restart works without extra user commands

## Non-Goals

- No full TUI in this phase
- No change to single global daemon yet
- No runtime/devtime CLI merge
- No optimizer/eval feature work

## Follow-up

If this plan still leaves too much lifecycle complexity or too many idle processes, the next architectural step is to evaluate a **global daemon** with per-worktree isolation, documented separately.
