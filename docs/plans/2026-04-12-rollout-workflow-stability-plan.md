# Rollout Workflow Stability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the real canary and limited-rollout workflow auditable by recording candidate-bound rollout attempts and surfacing them through CLI and candidate dossiers.

**Architecture:** Extend the existing `eval canary` command so it can optionally bind a canary result to a candidate and rollout phase, then persist that record in a rollout registry. Add `rollout-history` and wire rollout records into `candidate-status` so shadow/limited rollout is no longer a docs-only checklist.

**Tech Stack:** Existing `supervisor/eval/canary.py`, candidate dossier/reporting modules, JSONL registries under `.supervisor/evals/`, argparse CLI, pytest.

### Task 1: Red tests for rollout bookkeeping

**Files:**
- Create: `tests/test_eval_rollouts.py`
- Modify: `tests/test_app_cli.py`

**Step 1: Write failing registry tests**

Cover:
- writing a rollout record from a candidate-bound canary result
- listing rollout history with and without candidate filter
- computing latest rollout per candidate

**Step 2: Write failing CLI tests**

Cover:
- `thin-supervisor eval canary --candidate-id ... --phase shadow --save-report --json`
- `thin-supervisor eval rollout-history --candidate-id ... --json`
- `thin-supervisor eval candidate-status` includes rollout history

**Step 3: Verify red**

Run:
```bash
pytest -q tests/test_eval_rollouts.py tests/test_app_cli.py -k "rollout or canary"
```

### Task 2: Implement rollout registry and CLI

**Files:**
- Create: `supervisor/eval/rollouts.py`
- Modify: `supervisor/eval/canary.py`
- Modify: `supervisor/eval/dossier.py`
- Modify: `supervisor/eval/__init__.py`
- Modify: `supervisor/app.py`

**Step 1: Add rollout registry helpers**

Implement helpers to:
- persist rollout records
- list rollout history
- compute latest rollout per candidate

**Step 2: Extend canary CLI**

Support optional:
- `--candidate-id`
- `--phase shadow|limited`

When present, persist rollout history and include the saved record in JSON output.

**Step 3: Extend candidate dossier**

Expose:
- rollout history for the candidate
- latest rollout record
- next-action bias based on rollout phase and decision

### Task 3: Document and verify

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/ARCHITECTURE.md`

**Step 1: Document rollout bookkeeping**

Add:
```bash
thin-supervisor eval rollout-history [--candidate-id <candidate_id>] [--json]
```

**Step 2: Focused tests**

Run:
```bash
pytest -q tests/test_eval_rollouts.py tests/test_app_cli.py
```

**Step 3: Full suite**

Run:
```bash
pytest -q
```

