# Skill Evolution Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the first durable learning layer for thin-supervisor so repeated friction, user preference signals, and hindsight summaries can accumulate into a stable substrate for future skill evolution.

**Architecture:** Introduce a small learning store under `.supervisor/runtime/shared/` that persists two things: append-only `friction_event`s and mutable `user_preference_memory`. Wire those artifacts into existing run-history exports/postmortems so evolution happens from structured logs instead of ad-hoc prompt edits. Keep this first increment advisory and offline-oriented; do not auto-rewrite skills yet.

**Tech Stack:** Python dataclasses/helpers, existing CLI surface in `supervisor/app.py`, JSON/JSONL persistence, pytest.

## Task 1: Add durable learning storage helpers

**Files:**
- Create: `supervisor/learning.py`
- Test: `tests/test_learning.py`

**Step 1: Write the failing tests**

Cover:
- appending a `friction_event` writes an append-only JSONL record with ids/timestamps
- listing friction events can filter by `run_id` and `kind`
- saving/loading `user_preference_memory` is stable and merges updates

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_learning.py`
Expected: FAIL because learning storage helpers do not exist yet.

**Step 3: Write minimal implementation**

Add helpers for:
- `append_friction_event(...)`
- `list_friction_events(...)`
- `load_user_preferences(...)`
- `save_user_preferences(...)`

Use `.supervisor/runtime/shared/friction_events.jsonl` and `.supervisor/runtime/shared/user_preferences.json`.

**Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_learning.py`
Expected: PASS

## Task 2: Expose learning operations through CLI

**Files:**
- Modify: `supervisor/app.py`
- Test: `tests/test_app_cli.py`

**Step 1: Write the failing tests**

Cover:
- `thin-supervisor learn friction add ...` records an event
- `thin-supervisor learn friction list --json` returns stored events
- `thin-supervisor learn prefs set --key ... --value ...` persists a preference
- `thin-supervisor learn prefs show --json` returns the stored preference map

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_app_cli.py -k 'learn_friction or learn_prefs'`
Expected: FAIL because no `learn` command exists.

**Step 3: Write minimal implementation**

Add a top-level `learn` command with two groups:
- `learn friction add|list`
- `learn prefs set|show`

Keep the surface simple and scriptable.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_app_cli.py -k 'learn_friction or learn_prefs'`
Expected: PASS

## Task 3: Connect learning artifacts to run history

**Files:**
- Modify: `supervisor/history.py`
- Test: `tests/test_run_history.py`

**Step 1: Write the failing tests**

Cover:
- `export_run()` includes friction events related to the run
- `summarize_run()` reports friction counts and kinds
- `render_postmortem()` includes a friction section

**Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_run_history.py -k friction`
Expected: FAIL because history export ignores learning artifacts.

**Step 3: Write minimal implementation**

Thread related friction events into:
- `export_run()`
- `summarize_run()`
- `render_postmortem()`

Keep `user_preference_memory` out of per-run export except as a lightweight snapshot for context.

**Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_run_history.py -k friction`
Expected: PASS

## Task 4: Document the evolution strategy

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Create: `docs/reviews/2026-04-12-skill-evolution-research-and-design.md`

**Step 1: Write the design summary**

Document:
- why we do not auto-edit skills on every complaint
- the 4-layer strategy: online override, preference memory, friction log, offline replay/eval
- how the new `learn` commands fit into that strategy
- the research basis (Reflexion, Self-Refine, DSPy, LaMP, implicit-feedback work)

**Step 2: Verify docs references**

Run: `rg -n "learn friction|learn prefs|friction_event|user_preference" README.md docs/ARCHITECTURE.md docs/reviews/2026-04-12-skill-evolution-research-and-design.md`
Expected: matches in all updated docs

## Task 5: Run verification

**Files:**
- Test only

**Step 1: Run targeted tests**

Run: `pytest -q tests/test_learning.py tests/test_app_cli.py tests/test_run_history.py`
Expected: PASS

**Step 2: Run full suite**

Run: `pytest -q`
Expected: PASS
