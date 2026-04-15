# Zero-Setup Runtime UX Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `thin-supervisor` feel like a single-invoke product for runtime users: the user should be able to enter a project, invoke the skill inside Codex, and have setup, config resolution, daemon startup, pane attach, and supervised execution happen automatically.

**Architecture:** Keep the runtime/devtime split, but move runtime bootstrapping behind a single high-level entry path. Introduce a global runtime config layer for reusable secrets and defaults, allow per-project overrides, and make the skill/runtime bridge self-heal missing initialization instead of requiring users to remember `init`, `daemon start`, or attach commands.

**Tech Stack:** Python 3.10, existing runtime CLI in `supervisor/app.py`, skill entrypoints under `skills/thin-supervisor*`, `.supervisor/` project state, global user config under the home directory, tmux execution surface, pytest.

## Product Requirement

### Problem Statement

Current runtime behavior is usable for engineers but too heavy for normal users:

- users must remember `thin-supervisor init`
- users must remember `thin-supervisor daemon start`
- users may need to know about attach/register behavior
- users can forget setup and hit confusing runtime failures
- credentials and defaults are currently too project-local for a multi-project workflow

This creates unnecessary setup friction and breaks the intended mental model of “just invoke the skill and let the system take over”.

### Desired User Experience

For runtime users, the intended flow is:

1. Open tmux
2. `cd` into a project
3. Launch Codex
4. Invoke `/thin-supervisor`
5. Everything else happens automatically

The user should not need to run any setup command manually in the normal case.

### Non-Goals

- Do not merge runtime and devtime CLI again
- Do not expose eval/canary/promotion controls to runtime users
- Do not require users to understand panes, daemon sockets, or attach semantics
- Do not require users to duplicate shared credentials per project unless they explicitly want project-local overrides

## Requirements

### 1. Single-invoke runtime bootstrap

When `/thin-supervisor` is invoked inside Codex:

- detect whether the current environment is inside tmux
- detect whether the current project has `.supervisor/`
- if missing, auto-run the equivalent of `thin-supervisor init --repair`
- detect whether a project daemon is running
- if missing, auto-start it
- detect the current execution surface and pane id
- ensure the current pane is attached/registerable
- continue into clarify/plan/approve/execute without requiring user shell commands

If bootstrap fails, the user-facing message must explain exactly what is missing and what the system already attempted automatically.

### 2. Global runtime config with project override

Introduce a global config layer for reusable settings and secrets.

Required behavior:

- global config lives in a stable user-level path
- runtime reads config in this precedence order:
  1. per-invocation explicit inputs
  2. project-local config
  3. global config
  4. built-in defaults
- users should only provide shared credentials once for multi-project use
- project-local override must remain possible when the user explicitly requests single-project settings

Example use case:

- user provides DingTalk token once
- token is written into global config
- future projects can reuse it automatically
- if a specific project needs a different token, that project can override locally

### 3. Interactive runtime credential capture

When required runtime credentials are missing:

- the skill/runtime path should ask for the missing value in the agent conversation
- once the user provides it, the system should persist it into the correct config scope
- the user should not need to manually edit YAML

This applies to secrets like tokens, client IDs, secrets, tenant ids, or other runtime bootstrap configuration.

### 4. Multi-project support without extra setup burden

The system must work cleanly when two or more projects run in different tmux sessions.

Required behavior:

- each project keeps its own `.supervisor/` runtime state and daemon
- shared credentials come from global config unless locally overridden
- `thin-supervisor ps` still provides a global machine-level view
- invoking `/thin-supervisor` in project A must not require reconfiguration already completed globally for project B

### 5. Explicit runtime messaging

The user should be able to understand which phase the system is in:

- bootstrapping project state
- starting daemon
- resolving config
- attaching pane
- generating/approving spec
- running supervised execution

Messages should be short and human-readable, not implementation dumps.

## Acceptance Criteria

The work is not complete until all of the following are true:

1. In a fresh project with no `.supervisor/`, a user can open tmux, launch Codex, invoke `/thin-supervisor`, and successfully start the supervised flow without manually running `thin-supervisor init`.
2. If the daemon is not running, the same flow auto-starts it without requiring `thin-supervisor daemon start`.
3. If required runtime credentials are missing, the system asks for them conversationally and persists them without requiring hand-editing config files.
4. A user can run two separate projects in two tmux sessions and reuse global config without repeating setup.
5. Project-local override remains possible and has higher precedence than global config.
6. Runtime users do not need to know or invoke `thin-supervisor-dev`.
7. The documentation for runtime setup is updated so the top-level happy path no longer begins with manual init/start commands.

## Implementation Slices

### Slice 1: Config layering contract

Define and document:

- global config path
- project config path
- precedence rules
- secret write semantics
- which fields are safe to inherit globally and which must remain local

### Slice 2: Bootstrap API

Add a runtime bootstrap path that can be called by:

- skill-driven runtime invocation
- future high-level runtime commands

This should encapsulate:

- init/repair
- daemon startup check
- execution surface detection
- attach readiness

### Slice 3: Skill integration

Update the runtime skill so `/thin-supervisor` calls the bootstrap flow before entering clarify/plan/approve/execute.

Important:

- keep the runtime/devtime split
- do not leak devtime controls into the runtime UX

### Slice 4: Credential prompting + persistence

Add a credential resolution path that:

- determines missing required values
- asks the user inside the agent interaction
- writes those values into global or local config depending on scope
- masks sensitive values in logs and status output

### Slice 5: Multi-project verification

Test and document:

- two projects
- two tmux sessions
- shared global credential reuse
- one project-local override

## Testing Plan

### Unit tests

- config precedence resolution
- global vs project override behavior
- missing-credential detection
- bootstrap auto-init behavior
- bootstrap auto-daemon-start behavior
- pane/session detection behavior

### Integration tests

- fresh project with no `.supervisor/`
- existing project with daemon stopped
- two projects with shared global config
- one project with local override

### Manual N2N test

Use the actual user workflow:

1. create or enter a fresh project directory
2. open tmux
3. launch Codex
4. invoke `/thin-supervisor`
5. confirm the system bootstraps itself and proceeds

## Developer Notes

- This is a product UX simplification task, not just a runtime refactor
- The goal is to remove user shell-command memory from the happy path
- Keep fail-fast behavior when bootstrap cannot complete, but only after the system has tried the automatic path
- Prefer writing global config in a path that works across projects and survives repo changes

## Recommended Execution Order

1. Implement config precedence and tests
2. Implement bootstrap API and tests
3. Integrate bootstrap into runtime skill entry
4. Add credential prompting/persistence
5. Update runtime docs
6. Run multi-project tmux N2N verification
