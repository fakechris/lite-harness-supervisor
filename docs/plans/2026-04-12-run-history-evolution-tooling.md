# Run History Evolution Tooling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add stable historical run tooling so thin-supervisor can export, summarize, replay, and postmortem past runs, while preserving oracle-to-routing causality as auditable metadata.

**Architecture:** Introduce a small `supervisor.history` module that reads per-run artifacts (`state.json`, `decision_log.jsonl`, `session_log.jsonl`) plus shared notes and produces a stable export document. Build `run summarize`, `run replay`, and `run postmortem` on top of that export layer. Extend oracle note persistence and `RoutingDecision` so routing events can optionally reference a prior `OracleOpinion` consultation ID without letting the oracle drive control flow.

**Tech Stack:** Python 3.10 stdlib (`json`, `pathlib`, `collections`, `tempfile`), existing CLI in `supervisor/app.py`, existing models in `supervisor/domain/models.py`, existing daemon note plane, pytest.

## Task 1: Add failing tests for historical export and summary

**Files:**
- Create: `tests/test_run_history.py`
- Modify: `tests/test_app_cli.py`

**Step 1: Write the failing test**

Add tests that construct a fake run directory and assert:
- `export_run(...)` returns a stable JSON object containing `schema_version`, `state`, `decision_log`, `session_log`, and related notes
- `summarize_run(...)` returns counts for checkpoints, verifications, routing events, and oracle notes

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_run_history.py tests/test_app_cli.py -k "export or summarize"`
Expected: FAIL because the history helpers and CLI subcommands do not exist yet

## Task 2: Add failing tests for replay and postmortem

**Files:**
- Modify: `tests/test_run_history.py`
- Modify: `tests/test_app_cli.py`

**Step 1: Write the failing test**

Add tests that assert:
- `replay_run(...)` re-evaluates historical checkpoints and reports predicted vs actual gate decisions without injecting anything
- `render_postmortem(...)` generates markdown with outcome, decision counts, verification counts, and oracle/routing references
- `thin-supervisor run replay` and `thin-supervisor run postmortem` print or write the expected output

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_run_history.py tests/test_app_cli.py -k "replay or postmortem"`
Expected: FAIL because replay/postmortem interfaces do not exist yet

## Task 3: Add failing tests for oracle-aware routing metadata

**Files:**
- Modify: `tests/test_collaboration.py`
- Modify: `tests/test_supervision_policy.py`
- Modify: `tests/test_app_cli.py`

**Step 1: Write the failing test**

Add tests that assert:
- daemon notes can persist structured metadata for oracle notes
- `cmd_oracle(... --run ...)` stores the full consultation payload as note metadata
- `RoutingDecision` serializes an optional `consultation_id`
- escalation routing picks up the latest oracle consultation ID for the same run when present

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_collaboration.py tests/test_supervision_policy.py tests/test_app_cli.py -k "oracle or routing"`
Expected: FAIL because note metadata and consultation-linked routing are not implemented yet

## Task 4: Implement the history layer

**Files:**
- Create: `supervisor/history.py`
- Modify: `supervisor/storage/state_store.py`

**Step 1: Write minimal implementation**

Implement helpers to:
- resolve a run directory from `run_id`
- load JSONL safely
- export a run as a stable dict
- summarize exported history
- replay decisions using a fresh in-memory state machine plus recorded verifications
- render markdown postmortems

**Step 2: Run targeted tests**

Run: `pytest -q tests/test_run_history.py tests/test_app_cli.py -k "export or summarize or replay or postmortem"`
Expected: PASS

## Task 5: Wire CLI subcommands

**Files:**
- Modify: `supervisor/app.py`

**Step 1: Write minimal implementation**

Add:
- `thin-supervisor run export <run_id> [--output path] [--json]`
- `thin-supervisor run summarize <run_id> [--json]`
- `thin-supervisor run replay <run_id> [--json]`
- `thin-supervisor run postmortem <run_id> [--output path]`

Use the shared history module instead of embedding logic in CLI handlers.

**Step 2: Run targeted tests**

Run: `pytest -q tests/test_app_cli.py tests/test_run_history.py`
Expected: PASS

## Task 6: Wire oracle-aware routing

**Files:**
- Modify: `supervisor/domain/models.py`
- Modify: `supervisor/daemon/client.py`
- Modify: `supervisor/daemon/server.py`
- Modify: `supervisor/loop.py`

**Step 1: Write minimal implementation**

Add note metadata support, persist oracle payload metadata from `cmd_oracle`, extend `RoutingDecision` with optional `consultation_id`, and thread the latest run-local oracle consultation ID into routing audit events on escalation.

**Step 2: Run targeted tests**

Run: `pytest -q tests/test_collaboration.py tests/test_supervision_policy.py tests/test_app_cli.py`
Expected: PASS

## Task 7: Update docs and verify end-to-end

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/ARCHITECTURE.md`

**Step 1: Document the new historical tooling**

Document:
- stable run export
- replay semantics
- postmortem reports
- oracle-aware routing causality

**Step 2: Run full verification**

Run: `pytest -q`
Expected: PASS
