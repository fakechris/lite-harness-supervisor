# PR33 Consolidation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the remaining candidate-lifecycle work for PR #33 so the supervision policy optimizer has one coherent, auditable promotion surface instead of a set of disconnected commands.

**Architecture:** Keep the existing `eval propose -> review-candidate -> gate-candidate -> promote-candidate` chain, but add a unified candidate dossier/status view and persist gate/promotion outputs as first-class reports. This closes the loop between candidate manifests, compare/canary evidence, and the promotion registry without changing runtime behavior.

**Tech Stack:** Existing `thin-supervisor` eval/reporting/registry modules, JSON/JSONL artifacts under `.supervisor/evals/`, argparse CLI, pytest.

## Remaining Scope

### Task 1: Write failing dossier tests

**Files:**
- Create: `tests/test_eval_dossier.py`
- Modify: `tests/test_app_cli.py`

**Step 1: Write the failing dossier builder tests**

Cover:
- candidate dossier loads manifest and review summary
- dossier finds related proposal reports
- dossier finds latest gate report by `candidate_id`
- dossier includes promotion registry status
- dossier computes next action from lifecycle state

**Step 2: Write the failing CLI tests**

Cover:
- `thin-supervisor eval candidate-status --candidate-id ... --json`
- `promotion-history` remains stable with richer promotion records
- `gate-candidate --save-report`
- `promote-candidate --save-report`

**Step 3: Run focused tests and confirm failure**

Run:
```bash
pytest -q tests/test_eval_dossier.py tests/test_app_cli.py -k "candidate_status or gate_candidate or promote_candidate"
```

Expected:
- failures for missing dossier/report persistence behavior

### Task 2: Implement dossier and artifact persistence

**Files:**
- Create: `supervisor/eval/dossier.py`
- Modify: `supervisor/eval/reporting.py`
- Modify: `supervisor/eval/registry.py`
- Modify: `supervisor/eval/__init__.py`
- Modify: `supervisor/app.py`

**Step 1: Add dossier helpers**

Implement helpers to:
- load candidate manifest
- derive review summary
- discover related proposal/compare/gate/promotion reports
- merge promotion registry state
- emit a stable dossier payload with `candidate`, `review`, `evidence`, `promotion_status`, `next_action`

**Step 2: Persist gate and promote artifacts**

Add report persistence support for:
- `review-candidate`
- `gate-candidate`
- `promote-candidate`

Persist payloads with explicit `candidate_id` metadata so dossier lookup is deterministic.

**Step 3: Enrich promotion records**

Store:
- `objective`
- `touched_fragments`
- `manifest_path`
- `report_path` when available

### Task 3: Document and verify

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/ARCHITECTURE.md`

**Step 1: Document the dossier/status flow**

Add the new command:
```bash
thin-supervisor eval candidate-status --candidate-id <candidate_id> [--json]
```

Explain how it ties together:
- manifest
- gate result
- promotion history
- related reports

**Step 2: Run focused tests**

Run:
```bash
pytest -q tests/test_eval_dossier.py tests/test_app_cli.py
```

Expected:
- PASS

**Step 3: Run full suite**

Run:
```bash
pytest -q
```

Expected:
- PASS

