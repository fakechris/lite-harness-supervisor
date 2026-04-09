# Sidecar Initial Instruction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure the sidecar injects the first node instruction immediately after startup so an idle agent pane can begin work without a pre-existing checkpoint.

**Architecture:** Keep the fix localized to `SupervisorLoop.run_sidecar()` so startup behavior matches the existing post-verification injection path. Add a regression test that proves the first injected instruction targets the first node before any checkpoint arrives, then verify the full suite remains green.

**Tech Stack:** Python 3.10+, pytest, tmux sidecar supervisor

### Task 1: Reproduce the Startup Gap

**Files:**
- Modify: `tests/test_sidecar_loop.py`
- Test: `tests/test_sidecar_loop.py`

**Step 1: Write the failing test**

Add a test that feeds the sidecar one empty pane read before the first checkpoint and asserts the first injected instruction contains the `write_test` objective.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_sidecar_loop.py -k initial_instruction`
Expected: FAIL because no initial instruction is injected before the first checkpoint.

### Task 2: Inject the Initial Node Instruction

**Files:**
- Modify: `supervisor/loop.py`
- Test: `tests/test_sidecar_loop.py`

**Step 1: Write minimal implementation**

Inject the current node instruction once when the loop transitions from `READY` to `RUNNING`, before entering the polling loop. Preserve the existing instruction builder and read-before-inject guard.

**Step 2: Run targeted tests**

Run: `pytest -q tests/test_sidecar_loop.py -k initial_instruction`
Expected: PASS

### Task 3: Regression Verification

**Files:**
- Test: `tests/test_sidecar_loop.py`
- Test: `tests/`

**Step 1: Re-run focused sidecar tests**

Run: `pytest -q tests/test_sidecar_loop.py`
Expected: PASS

**Step 2: Run full suite**

Run: `pytest -q`
Expected: PASS
