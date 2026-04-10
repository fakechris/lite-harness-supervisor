# Supervisor Stability And Observability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate ambiguous supervisor startup behavior, prevent cross-daemon pane collisions, and add enough observability to explain which daemon/run owns each pane.

**Architecture:** Introduce a small global control-plane registry under the user's state directory so daemons can discover each other and coordinate pane ownership. Tighten the CLI surface by removing the ambiguous legacy `run` path, adding global observability commands, and routing supervised execution through an explicit attach script so registration happens immediately after spec creation instead of relying on late-stage prompt memory.

**Tech Stack:** Python CLI, Unix sockets, tmux integration, pytest.

### Task 1: Add failing tests for global pane locking and daemon observability

**Files:**
- Create: `tests/test_global_registry.py`
- Modify: `tests/test_daemon.py`
- Modify: `tests/test_app_cli.py`

**Step 1: Write the failing tests**

- Add a test proving two daemon servers with different runtime directories but a shared global state root cannot register the same pane concurrently.
- Add a test proving a new global CLI command lists registered daemons with cwd, pid, socket, and active run count.
- Add a test proving `thin-supervisor pane-owner <pane>` returns owner metadata when a pane lock exists.
- Add a test proving the legacy `thin-supervisor run <spec> --pane <pane>` path exits with an explicit migration error.

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_app_cli.py tests/test_daemon.py tests/test_global_registry.py`
Expected: FAIL because no global registry or pane lock exists and legacy run still works.

### Task 2: Implement the global registry and pane lock

**Files:**
- Create: `supervisor/global_registry.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/daemon/client.py`

**Step 1: Add minimal global state helpers**

- Implement helpers for:
  - daemon registration/unregistration
  - listing all known daemons
  - exclusive pane lock acquisition/release
  - stale lock cleanup for dead PIDs

**Step 2: Wire daemon lifecycle**

- Register daemon metadata on `DaemonServer.start()`
- Unregister on cleanup
- Acquire pane lock during `_do_register`
- Release pane lock when runs are reaped or daemon shuts down

**Step 3: Run focused tests**

Run: `pytest -q tests/test_daemon.py tests/test_global_registry.py -k "lock or daemon"`
Expected: PASS

### Task 3: Add global observability commands

**Files:**
- Modify: `supervisor/app.py`
- Modify: `tests/test_app_cli.py`

**Step 1: Add CLI commands**

- Add `thin-supervisor ps`
- Add `thin-supervisor pane-owner <pane>`

**Step 2: Improve output**

- Show daemon PID, cwd, socket path, started time, and currently active runs
- Show pane owner metadata even if the owning daemon lives in another worktree

**Step 3: Run focused tests**

Run: `pytest -q tests/test_app_cli.py -k "ps or pane_owner"`
Expected: PASS

### Task 4: Remove the ambiguous legacy run path

**Files:**
- Modify: `supervisor/app.py`
- Modify: `docs/getting-started.md`
- Modify: `README.md`

**Step 1: Replace legacy behavior**

- Make `thin-supervisor run <spec> --pane <pane>` fail with a clear message:
  - use `run register` for daemon mode
  - use `run foreground` for foreground mode

**Step 2: Update docs**

- Remove legacy examples or mark them deprecated

**Step 3: Run focused tests**

Run: `pytest -q tests/test_app_cli.py -k legacy`
Expected: PASS

### Task 5: Add an explicit attach script and tighten skill timing

**Files:**
- Create: `scripts/lh-supervisor-attach.sh`
- Modify: `skills/lh-supervisor-codex/SKILL.md`
- Modify: `AGENTS.md`

**Step 1: Create the helper script**

- Validate `.supervisor/specs/<slug>.yaml` exists
- Run `thin-supervisor init --force`
- Run `thin-supervisor run register --spec ... --pane "$(thin-supervisor bridge id)"`

**Step 2: Update guidance**

- Change the skill flow so spec creation is followed immediately by the attach script before any implementation work
- Clarify that execution should not start before attach succeeds

**Step 3: Run shell-level validation**

Run: `bash -n scripts/lh-supervisor-attach.sh`
Expected: PASS

### Task 6: Add injection diagnostics for stacked input failures

**Files:**
- Modify: `supervisor/terminal/adapter.py`
- Modify: `supervisor/loop.py`
- Modify: `tests/test_terminal_adapter.py`
- Modify: `tests/test_sidecar_loop.py`

**Step 1: Add a post-injection verification hook**

- After injection, read the pane and detect obvious pending-input replay or unchanged tail content
- If confirmation fails, emit a session event and pause for human instead of blindly injecting again

**Step 2: Keep the first version conservative**

- Prefer false positives that pause the run over silent repeated injections

**Step 3: Run focused tests**

Run: `pytest -q tests/test_terminal_adapter.py tests/test_sidecar_loop.py`
Expected: PASS

### Task 7: Full verification

**Files:**
- Modify: none

**Step 1: Run the targeted supervisor suite**

Run: `pytest -q tests/test_app_cli.py tests/test_daemon.py tests/test_collaboration.py tests/test_global_registry.py tests/test_terminal_adapter.py tests/test_sidecar_loop.py`
Expected: PASS

**Step 2: Run the full suite**

Run: `pytest -q`
Expected: PASS
