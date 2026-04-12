# Default Clarify Before Run Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make supervised execution default to clarify-first planning, require explicit user confirmation before a generated spec can run, and preserve backward compatibility for existing approved/manual specs.

**Architecture:** Add a lightweight spec-approval model to the spec schema, enforce it at execution entry points (`run register`, `run foreground`, daemon register/resume), and add a small CLI command to approve a draft spec. Update the shipped skills so `/thin-supervisor` writes draft specs, asks for approval, then marks the spec approved before attaching.

**Tech Stack:** Python dataclasses, YAML loader/dumper, existing CLI/daemon flow, shell attach script, pytest.

### Task 1: Add spec approval metadata to the schema

**Files:**
- Modify: `supervisor/domain/models.py`
- Modify: `supervisor/plan/loader.py`
- Test: `tests/test_spec_loader.py`

**Step 1: Write the failing test**

Cover:
- specs can load an optional `approval` block
- a missing `approval` block remains runnable by default
- `approval.required: true` + `status: draft` is parsed correctly

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_spec_loader.py`
Expected: FAIL because approval metadata is not modeled yet.

**Step 3: Write minimal implementation**

Add a `SpecApproval` dataclass and thread it through `WorkflowSpec` + `load_spec`.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_spec_loader.py`
Expected: PASS

### Task 2: Enforce approval before execution and add approve CLI

**Files:**
- Create: `supervisor/spec_approval.py`
- Modify: `supervisor/app.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `scripts/thin-supervisor-attach.sh`
- Test: `tests/test_app_cli.py`
- Test: `tests/test_daemon.py`
- Test: `tests/test_attach_script.py`

**Step 1: Write the failing tests**

Cover:
- `run register` rejects a draft spec with a clear approval command
- `run foreground` rejects a draft spec
- daemon `_do_register` rejects a draft spec
- `thin-supervisor spec approve --spec ...` rewrites the spec to approved
- attach script surfaces the approval gate error cleanly

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_app_cli.py tests/test_daemon.py tests/test_attach_script.py -k approval`
Expected: FAIL because no approval gate / approve command exists.

**Step 3: Write minimal implementation**

Create helpers to:
- validate a spec is runnable
- rewrite `approval.status` to `approved`
- share the same error message across CLI/daemon

Add a new CLI command:
- `thin-supervisor spec approve --spec <path> [--by human]`

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_app_cli.py tests/test_daemon.py tests/test_attach_script.py -k approval`
Expected: PASS

### Task 3: Make the shipped skill default to clarify + explicit approval

**Files:**
- Modify: `skills/thin-supervisor/SKILL.md`
- Modify: `skills/thin-supervisor-codex/SKILL.md`
- Modify: `README.md`
- Modify: `docs/getting-started.md`

**Step 1: Update skill flow**

Change the default behavior to:
- always start with clarify
- write draft specs with `approval.required: true` and `approval.status: draft`
- require explicit user confirmation before attach
- call `thin-supervisor spec approve --spec ...` immediately before attach

**Step 2: Update docs**

Document:
- clarify-first default
- spec approval gate
- `thin-supervisor spec approve`
- backward compatibility note for manually authored specs without approval metadata

**Step 3: Verify docs references**

Run: `rg -n "spec approve|approval.status|clarify" README.md docs/getting-started.md skills/thin-supervisor/SKILL.md skills/thin-supervisor-codex/SKILL.md`
Expected: matching lines in all updated docs

### Task 4: Run verification

**Files:**
- Test only

**Step 1: Run targeted tests**

Run: `pytest -q tests/test_spec_loader.py tests/test_app_cli.py tests/test_daemon.py tests/test_attach_script.py`
Expected: PASS

**Step 2: Run full suite**

Run: `pytest -q`
Expected: PASS
