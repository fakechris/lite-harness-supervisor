# Global Daemon Architecture Options

> This document is a design discussion artifact, not a committed architecture decision.

## Goal

Evaluate whether `thin-supervisor` should evolve from the current **per-worktree daemon** model to a **single global daemon** that manages multiple worktrees.

This document exists so multiple agents can debate the tradeoffs against a shared, concrete target.

## Current Baseline

Today the system behaves as:

- one daemon per worktree
- one Unix socket per worktree
- project-local runtime state under `.supervisor/`
- global discovery only through the registry under the user state directory

This means:

- worktrees are isolated by default
- multiple daemons can accumulate over time
- users must reason about worktree-local daemons plus global discovery

## Proposed Alternative

Move to:

- one global daemon process per user/machine
- one global Unix socket
- multiple worktrees registered under that daemon
- run/controller isolation performed explicitly by workspace identity rather than separate processes

## Why Consider A Global Daemon

### 1. Simpler user mental model

Users could think:

- there is one supervisor runtime on my machine
- it knows about all projects/worktrees
- `/thin-supervisor` just connects to it

This is cleaner than:

- every worktree has its own daemon
- every worktree can auto-start another Python process

### 2. No idle-daemon accumulation

A global daemon avoids the current process multiplication problem:

- more worktrees do not imply more daemon processes
- idle lifecycle becomes a single process concern

### 3. More natural multi-session observability

A single controller can expose:

- all worktrees
- all runs
- all panes
- all orphaned state

without stitching together multiple daemon records.

### 4. Better fit for zero-setup UX

A zero-setup product story is easier to explain as:

- if the global daemon is missing, start it
- otherwise connect to it
- register the current worktree and pane

This is closer to the single-invoke runtime vision.

## Why Not Change Immediately

### 1. Isolation becomes explicit instead of structural

Today, process boundaries provide natural isolation.

With a global daemon we must explicitly model:

- `workspace_id`
- `workspace_root`
- workspace-local config
- workspace-local runtime state root
- workspace-specific recovery

What is currently "local because it lives in this directory" becomes "local because we modeled it correctly".

### 2. Failure domain becomes larger

Today:

- one daemon crash affects one worktree

With a global daemon:

- one daemon crash affects all worktrees

That raises the bar for:

- robustness
- persistence
- restart recovery
- diagnostics

### 3. Recovery becomes significantly more complex

A global daemon would have to recover correctly across:

- multiple worktrees
- multiple pane locks
- multiple paused runs
- multiple spec/config versions

This is much more complex than worktree-local orphan recovery.

### 4. Configuration precedence becomes more critical

A global daemon makes config layering a first-class design problem:

- global defaults
- global secrets
- worktree overrides
- run-level overrides

This is solvable, but must be designed intentionally.

## Architecture Options

## Option A: Keep per-worktree daemon

### Shape

- current architecture
- add idle shutdown
- improve observability
- keep global registry

### Pros

- smallest migration cost
- preserves failure isolation
- matches existing persisted state model
- simpler to ship incrementally

### Cons

- multiple daemon processes remain a core product fact
- users still need to understand that daemons are worktree-scoped
- multi-session experience remains CLI-composed rather than centrally managed

## Option B: Global daemon with local state roots

### Shape

- one global control plane process
- one global socket
- each run still stores state under the owning worktree's `.supervisor/runtime/`
- daemon keeps only controller/runtime metadata globally

### Pros

- better product mental model
- easier single-invoke runtime story
- eliminates idle-daemon accumulation
- preserves local on-disk state ownership

### Cons

- requires dual-scope logic:
  - global controller state
  - local persisted run state
- resume/orphan recovery becomes cross-root logic
- daemon must safely access many worktree roots

## Option C: Fully global daemon + fully global runtime state

### Shape

- one daemon
- one socket
- one global state root
- worktree identity stored in metadata only

### Pros

- most unified architecture
- easiest to build a future dashboard/TUI on top of
- one place to inspect all state

### Cons

- largest migration cost
- highest failure blast radius
- weakest locality for debugging and repo-scoped inspection
- changes current “state lives with the repo” assumption

## Recommended Near-Term Position

### Recommendation

Do **not** migrate immediately.

Instead:

1. complete the per-worktree daemon lifecycle contract
2. ship idle shutdown + clear status + multi-session CLI observability
3. use that to measure whether process multiplication remains a real product pain

Then evaluate **Option B** as the preferred long-term direction:

- one global daemon
- local state remains per worktree

This offers the cleanest product model without forcing a fully global storage model.

## Migration Preconditions

Before a global daemon migration should begin, the following must already be true:

1. controller modes are explicit and stable
2. pane ownership is globally reliable
3. status/ps/observe semantics are clean
4. config layering is explicit
5. orphan recovery contract is already well-defined

Without those, a topology migration will mix product ambiguity with architectural ambiguity.

## Global Daemon Required Semantics

If we choose the global daemon path, the system must support:

### Workspace registration

The daemon must explicitly know:

- workspace root
- workspace id
- workspace config source
- workspace runtime state root

### Per-workspace isolation

The daemon must ensure:

- pane conflicts are global
- run ids are globally unique
- workspace-local persisted state is never confused across worktrees

### Lifecycle

The daemon must distinguish:

- active workspace
- idle workspace
- orphaned workspace state
- disconnected but recoverable workspace

### Observability

The CLI must support:

- all worktrees on this machine
- all runs across worktrees
- filtering by worktree
- filtering by controller mode

## Open Design Questions

1. Should the global daemon be user-scoped or machine-scoped?
2. Should workspace state remain under `.supervisor/` or move into a global store?
3. How should permissions work if a worktree is deleted while runs remain in metadata?
4. How should the daemon validate that a workspace path still exists and still matches the original repo/worktree?
5. How should version skew be handled if the daemon binary stays alive while one repo is upgraded?

## Acceptance Criteria For Choosing Global Daemon

The migration should only be approved if it can demonstrate:

1. simpler user mental model than per-worktree daemon
2. no regression in orphan recovery
3. no regression in pane conflict safety
4. no regression in local state inspectability
5. clear operational benefit over per-worktree idle shutdown

## Suggested Evaluation Outcome

For now, the system should proceed with:

- **implementing the strengthened per-worktree daemon contract**
- **deferring the global daemon migration decision**

This keeps the current architecture safe while creating a concrete target for future simplification.
