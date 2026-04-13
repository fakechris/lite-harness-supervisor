# Runtime vs Devtime CLI Split Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split `thin-supervisor` into a runtime-only CLI and a separate `thin-supervisor-dev` CLI for eval, learning, and oracle/operator workflows.

**Architecture:** Keep runtime commands in `supervisor.app`, move devtime parser/entrypoint into a new `supervisor.dev_app` module, and share existing command handlers instead of duplicating business logic. Runtime help and docs must no longer expose devtime commands.

**Tech Stack:** Python 3.10, argparse, pytest, hatchling console scripts.

### Task 1: Add failing CLI boundary tests

**Files:**
- Modify: `tests/test_app_cli.py`
- Create: `tests/test_dev_app_cli.py`

**Step 1: Write failing tests**

Add tests that assert:
- `thin-supervisor` parser/help no longer includes `eval`, `learn`, or `oracle`
- `thin-supervisor-dev` parser/help does include them
- existing `cmd_eval` and `cmd_learn` handlers remain callable through the new dev CLI entrypoint

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_app_cli.py tests/test_dev_app_cli.py`
Expected: FAIL because the runtime parser still exposes devtime commands and `supervisor.dev_app` does not exist yet.

### Task 2: Add `thin-supervisor-dev` entrypoint and parser

**Files:**
- Modify: `pyproject.toml`
- Modify: `supervisor/app.py`
- Create: `supervisor/dev_app.py`

**Step 1: Write minimal implementation**

- Add a new console script: `thin-supervisor-dev = "supervisor.dev_app:main"`
- Extract or share parser helpers so runtime parser stays focused on:
  - init/deinit
  - daemon
  - run
  - list/ps/pane-owner/observe/note/spec/session/status/bridge/skill
- New dev parser should expose:
  - `oracle`
  - `learn`
  - `eval`

**Step 2: Run tests to verify parser behavior**

Run: `pytest -q tests/test_app_cli.py tests/test_dev_app_cli.py`
Expected: PASS

### Task 3: Update command strings and lifecycle hints

**Files:**
- Modify: `supervisor/eval/dossier.py`
- Modify: `supervisor/eval/promotion.py`
- Modify: `supervisor/eval/reporting.py`
- Modify: `tests/test_eval_*.py`

**Step 1: Update user-facing next-action strings**

Any devtime recommendation that currently says `thin-supervisor eval ...` should become `thin-supervisor-dev eval ...`.

**Step 2: Run focused tests**

Run: `pytest -q tests/test_eval_dossier.py tests/test_eval_promotion.py tests/test_eval_registry.py tests/test_app_cli.py tests/test_dev_app_cli.py`
Expected: PASS

### Task 4: Update docs and packaging guidance

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `CHANGELOG.md`

**Step 1: Split docs by audience**

- Runtime docs should only mention `thin-supervisor`
- Dev/operator docs should explicitly mention `thin-supervisor-dev`
- Explain that eval/local tuning is an internal operator workflow, not end-user/runtime usage

**Step 2: Run full verification**

Run: `pytest -q`
Expected: PASS
