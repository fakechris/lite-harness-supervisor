# Supervision Policy Optimizer Roadmap

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade `thin-supervisor` from a narrow approval-prompt tuning project into a supervision policy optimizer with correct control-plane behavior, richer offline evaluation, replay-driven learning, and canary-based promotion.

**Architecture:** Keep the current supervisor runtime and history substrate, but separate immutable execution contracts from optimizable strategy fragments. Expand evaluation from `approval-core` to full supervision-policy coverage, then add replay-aware scoring, constrained candidate generation, and shadow-canary promotion gates. External systems like DSPy, Trace, and Promptim are treated as reference architectures, not runtime dependencies.

**Tech Stack:** Existing `thin-supervisor` daemon/runtime/history/eval stack, JSONL datasets, pytest, GitHub PR workflow, replay exports, friction memory, optional oracle advisory.

---

## Status Snapshot (updated 2026-04-17 / release 0.3.2)

This roadmap is no longer a purely forward-looking plan. Most of the original slices have now landed on `main`.

| Track | Status | What is live now |
|------|--------|------------------|
| PR1: Control plane correctness | **Shipped** | attach/resume hardening, `ATTACHED` / `RECOVERY_NEEDED`, global observability plane, stronger tmux injection gating |
| PR2: Eval surface expansion | **Shipped** | approval, clarify, routing, escalation, finish-gate, and pause-UX suites all exist under `thin-supervisor-dev eval` |
| PR3: Weighted replay + friction compare | **Partial** | replay, compare, friction memory, canary aggregation, and rollout ledgers are live; richer typed weighting and less heuristic semantics are still open |
| PR4: Contract / strategy split | **Shipped** | frozen contract docs and strategy fragments are separated in the skill layout |
| PR5: Candidate generation with lineage | **Shipped** | propose, review-candidate, candidate-status, gate-candidate, promote-candidate, and `eval improve` are all live |
| PR6: Shadow canary runner | **Shipped** | rollout recording, gate/promotion workflow, and saved eval reports are implemented |

The next milestone is no longer “build the optimizer substrate.” It is **reduce semantic fragility**: move meaning out of harness regexes and into structured skill/protocol outputs without turning every gate into a slow inference call.

---

## Why This Plan Exists

The current repository already has the right substrate:
- deterministic run state and session logs
- `run export / summarize / replay / postmortem`
- `friction_event` and `user_preference_memory`
- first-generation offline eval commands under `thin-supervisor eval`

What it does not yet have is a mature optimizer loop. Today the eval layer is still narrow:
- golden coverage is mostly approval-centric
- replay scoring is still too shallow in places
- proposal generation is constrained to a tiny built-in candidate pool
- semantic classification still relies on too many harness-side heuristics

The next phase should optimize **supervision policy semantics**, not just approval wording or more regex branches.

## External Reference Systems

Use these projects as architectural references:

- **DSPy**
  Learn from component-local optimization, trace-informed refinement, and clear separation between programs, optimizers, and datasets.
- **Trace**
  Learn from treating execution traces as first-class optimization signals.
- **Promptim**
  Learn from explicit dataset/evaluator/optimizer/train-loop decomposition.
- **PromptWizard / AdalFlow / TextGrad / REVOLVE**
  Learn from candidate generation and text-parameter optimization, but do not import them directly until local runtime correctness and eval depth are stronger.

Do **not** treat any external framework as a drop-in dependency for the first implementation phase.

## Guiding Principles

1. Control-plane correctness comes before policy optimization.
2. Contract rules stay frozen; only strategy fragments are optimizable.
3. Replay and friction outrank synthetic wins.
4. Promotion decisions require offline gate plus real canary evidence.
5. Oracle/advisory models may propose or reflect, but not directly control runtime defaults.

## Target End State

The repository now supports:
- correct pause/resume/attach behavior under realistic daemon and tmux flows
- eval suites that cover approval, routing, escalation, finish gates, and UX observability
- strategy-fragment candidate generation with lineage and auditability
- shadow canary and limited rollout promotion workflow implemented as commands, not prose

The remaining target end state is:
- less regex-heavy semantic classification in harness gates
- more structured checkpoint / escalation payloads
- weighted replay scoring that distinguishes harmless divergence from risky regressions without overfitting to a tiny golden set
- cleaner separation between mechanism rules (harness) and semantic intent (skill / protocol)

## Layer Model

### Contract Layer

Immutable rules that must not be optimized away:
- explicit approval must not trigger repeated confirmation
- attach must occur before implementation starts
- supervised agents must not ask “should I continue?”
- pause/completed states must be externally visible
- finish gates and review gates must remain satisfiable and auditable

### Strategy Layer

Optimizable policy surfaces:
- approval boundary behavior
- escalation vs auto-continue thresholds
- pause wording and next-action wording
- shadow-canary promotion thresholds
- replay mismatch tolerances

### Signal Layer

Optimization inputs:
- checkpoints
- decisions
- verification results
- replay outputs
- friction events
- user preference memory
- postmortem summaries

## PR1: Control Plane Correctness

**Status:** Shipped on `main`

**Objective:** Ensure runtime behavior is trustworthy enough to produce valid learning signals.

### Scope

- Re-audit `pause -> resume -> running` transitions in daemon and loop paths
- Re-audit attach/resume behavior for tmux and daemon-managed runs
- Promote known deep-review/runtime gaps into automated integration tests
- Reduce or eliminate test ignores around daemon/attach/collaboration where feasible

### Files to touch

- `supervisor/daemon/server.py`
- `supervisor/loop.py`
- `supervisor/terminal/adapter.py`
- `scripts/thin-supervisor-attach.sh`
- `tests/test_daemon.py`
- `tests/test_sidecar_loop.py`
- `tests/test_attach_script.py`
- CI workflow files under `.github/workflows/`

### Acceptance

- no-op resume paths are covered by tests
- pause/hidden-completion regressions are guarded by tests
- at least one realistic integration lane runs in CI or a documented gated workflow

## PR2: Expand Eval Surface From Approval To Supervision Policy

**Status:** Shipped on `main`

**Objective:** Broaden offline gate coverage so improvements are evaluated against real supervisor behavior classes.

### Scope

- Extend `EvalCase` with richer assertion fields:
  - `severity`
  - `weights`
  - `expected_decision`
  - `allowed_alternatives`
  - `source_run_id`
  - `source_checkpoint_seq`
- Keep current fields (`anti_goals`, `metadata`) intact
- Add new suites:
  - `approval-adversarial`
  - `finish-gate-core`
  - `escalation-core`
  - `pause-ux-core`
  - `routing-core`
- Extend executor beyond approval-only logic

### Files to touch

- `supervisor/eval/cases.py`
- `supervisor/eval/executor.py`
- `supervisor/eval/goldens/*.jsonl`
- `tests/test_eval_cases.py`
- `tests/test_eval_executor.py`
- `tests/test_app_cli.py`

### Acceptance

- bundled eval list includes multiple supervision-policy suites
- executor handles more than approval category
- eval output reports severity/weight-aware results

## PR3: Weighted Replay And Friction-Aware Comparison

**Status:** Partially shipped; remaining work is semantic weighting, not baseline replay/canary plumbing

**Objective:** Stop treating all mismatches as equal and connect real traces to promotion decisions.

### Scope

- Classify replay differences into:
  - equivalent divergence
  - UX-only divergence
  - risky routing divergence
  - safety regression
- Add weighted comparison logic instead of pure mismatch counting
- Aggregate friction events by kind and severity
- Surface friction deltas in compare/propose outputs

### Files to touch

- `supervisor/history.py`
- `supervisor/eval/replay.py`
- `supervisor/eval/comparator.py`
- `supervisor/learning.py`
- `supervisor/eval/reporting.py`
- `tests/test_eval_replay.py`
- `tests/test_eval_comparator.py`
- `tests/test_learning.py`

### Acceptance

- replay reports contain typed diff categories
- compare output includes weighted score plus raw mismatches
- friction summaries appear in eval reports and proposal inputs

## PR4: Contract/Strategy Split For Skill Optimization

**Status:** Shipped on `main`

**Objective:** Make future optimization safe by separating frozen rules from mutable strategy fragments.

### Scope

- Split current `skills/thin-supervisor/SKILL.md` guidance into:
  - frozen contract content
  - strategy-specific fragments
- Introduce explicit file layout for strategy fragments such as:
  - `approval-boundary.md`
  - `finish-proof.md`
  - `escalation.md`
  - `pause-ux.md`
- Update skill generation or assembly path if needed

### Files to touch

- `skills/thin-supervisor/SKILL.md`
- `skills/thin-supervisor-codex/SKILL.md`
- any referenced assembly helpers if introduced
- documentation files explaining the split

### Acceptance

- contract statements are identifiable and frozen
- proposals can reference strategy fragments without mutating the whole skill
- docs explain which files are safe to optimize

## PR5: Candidate Generation With Lineage

**Status:** Shipped on `main`

**Objective:** Replace today’s fixed candidate pool with auditable strategy-fragment candidates.

### Scope

- Add candidate lineage model:
  - candidate id
  - parent id
  - objective
  - touched fragments
  - originating evidence
- Expand `proposals.py` to support mutation operators over strategy fragments
- Keep promotion manual and bounded
- Keep oracle in proposer/reflector role only

### Files to touch

- `supervisor/eval/proposals.py`
- `supervisor/eval/reporting.py`
- `supervisor/oracle/client.py`
- new candidate storage under `.supervisor/evals/candidates/` or repo-local equivalent
- `tests/test_eval_proposals.py`

### Acceptance

- proposal output includes lineage metadata
- mutation scope is fragment-level, not full skill rewrite
- proposal reports cite source failures and replay evidence

## PR6: Shadow Canary Runner

**Status:** Shipped on `main`

**Objective:** Turn README rollout advice into executable promotion workflow.

### Scope

- Add a real command surface such as:
  - `thin-supervisor eval canary`
  - or `thin-supervisor eval shadow`
- Inputs:
  - baseline candidate
  - challenger candidate
  - set of run ids or recent runs
- Outputs:
  - replay deltas
  - friction deltas
  - rollback threshold evaluation
  - promotion recommendation

### Files to touch

- `supervisor/app.py`
- `supervisor/eval/reporting.py`
- new canary runner module under `supervisor/eval/`
- README / getting-started / architecture docs
- dedicated tests for canary orchestration

### Acceptance

- shadow canary can be run from CLI with stable reports
- candidate promotion decision is based on concrete thresholds, not manual prose
- report artifacts are stored under `.supervisor/evals/`

## Metrics To Optimize

Do not optimize one metric in isolation. Promotion requires non-regression across a bundle.

### Core metrics

- `false_approval_rate`
- `missed_approval_rate`
- `repeated_confirmation_rate`
- `manual_intervention_rate`
- `unexpected_pause_confusion_rate`
- `hidden_completion_rate`
- `checkpoint_mismatch_rate`
- `completion_rate`
- `friction_events_per_run`

### Decision rule

Reject a candidate if it:
- improves one metric by making another safety-relevant metric worse
- increases risky routing divergence in replay
- increases real canary friction

## What Not To Do Yet

- no automatic merge of candidates
- no global hot-editing of `SKILL.md` from live traffic
- no direct runtime control by oracle/advisory models
- no heavy external dependency adoption before local evaluator maturity

## Next Execution Order

The original PR1-PR6 sequence is mostly complete. The next execution order should be:

1. **Rule inventory**
   Enumerate every harness-side semantic rule and classify it as mechanism vs semantics.
2. **Structured protocol bridge**
   Add explicit checkpoint/escalation fields such as progress class, evidence scope, escalation class, and reason codes.
3. **Harness simplification**
   Replace low-confidence regex branches with structured fields, while keeping a small fail-safe ruleset for deterministic hazards.
4. **Replay/eval deepening**
   Make replay weighting and synthesized eval expansion reflect the new structured protocol instead of today’s textual heuristics.
5. **Promotion hardening**
   Keep canary/promotion commands, but shift their scoring away from brittle text matching and toward typed signals.

## Verification Strategy Per PR

- Every PR must pass `pytest -q`
- New eval surfaces must include focused unit tests and at least one CLI integration test
- Replay-related changes must be validated against saved run exports
- Canary/promotion changes must persist machine-readable report artifacts

## Handoff Notes

This roadmap intentionally keeps `thin-supervisor` self-contained. The repository should first become a strong local optimizer of supervision policy. Once PR1-PR6 are stable, reassess whether direct integration with external frameworks like DSPy or Trace adds leverage without distorting the existing runtime model.
