# Skill Eval And Evolution System Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `thin-supervisor` skill and policy iteration into an evaluation-driven system that learns from friction without blindly rewriting global behavior.

**Architecture:** Build a four-stage loop: collect structured friction and user preference signals, convert them into curated eval datasets, compare baseline vs candidate behavior with blind A/B and replay, then promote only variants that improve metrics without causing regressions. Keep online adaptation local to the current run or user profile; keep global behavior changes offline, benchmarked, and human-reviewed.

**Tech Stack:** Existing `friction_event` and `user_preference_memory` stores, `run export/summarize/replay/postmortem`, JSONL/JSON datasets, pytest, structured comparators, optional LLM judges, optional future DSPy/GEPA integration.

---

## Why This Exists

The current repo is already strong at logging supervisor-side facts:
- run state
- checkpoint history
- decision history
- notifications
- friction events
- user preference memory

What is still missing is the layer that turns those artifacts into safer skill evolution.

The wrong fix is to edit `SKILL.md` every time the user gets annoyed. That overfits to one episode and creates global regressions. The right fix is:
- learn online inside the current run
- remember durable user preferences separately
- collect friction as structured evidence
- only evolve shared behavior offline through replay and comparison

This design keeps those layers distinct.

## What Hermes Gets Right

The `NousResearch/hermes-agent-self-evolution` project is useful mainly as an architecture pattern.

What is worth copying:
- treat skill text as an optimizable artifact, not sacred prose
- separate dataset building from optimization
- gate every candidate with constraints and tests
- require human review before promotion

What should not be copied directly:
- optimizing from synthetic-only datasets
- trusting heuristic lexical overlap as the main fitness function
- assuming a lightweight text wrapper is equivalent to the real agent runtime
- jumping to automated GEPA mutation before replay infrastructure is mature

For `thin-supervisor`, the best takeaway is not "run GEPA now". The best takeaway is "use a disciplined offline evaluation pipeline before changing defaults."

## Design Principles

1. Do not auto-edit global skills online.
2. Separate user preference from shared policy.
3. Real trace replay outranks synthetic wins.
4. Optimize multiple metrics at once.
5. Every promoted change must be auditable.
6. Human review remains the merge gate.

## Scope

This system should evaluate and evolve four classes of behavior:

1. Skill triggering
- Does `/thin-supervisor` or related skill logic activate when it should?
- Does it avoid irrelevant activation?

2. Clarify and approval behavior
- Does it ask enough questions before starting?
- Does it stop asking once the user clearly approved?

3. Runtime policy behavior
- Does the supervisor pause, notify, auto-intervene, or resume appropriately?
- Does it surface actionable next steps?

4. User experience behavior
- Does the user need to repeat commands?
- Does the system cause confusion, hidden pauses, or redundant confirmation loops?

Out of scope for the first version:
- autonomous code mutation
- automatic merge of evolved candidates
- model fine-tuning
- production multi-user ranking infrastructure

## Target Metrics

The system should track at least these metrics per baseline and candidate:

### Trigger Metrics

- `trigger_precision`
- `trigger_recall`
- `overtrigger_rate`
- `undertrigger_rate`

### Clarify And Approval Metrics

- `repeated_confirmation_rate`
- `missed_approval_rate`
- `false_approval_rate`
- `clarification_turns_before_contract`
- `approval_latency_turns`

### Runtime Metrics

- `manual_intervention_rate`
- `unexpected_pause_confusion_rate`
- `stale_injection_recovery_rate`
- `checkpoint_mismatch_rate`
- `completion_rate`

### User Friction Metrics

- `user_repeated_instruction_rate`
- `manual_override_rate`
- `negative_feedback_rate`
- `friction_events_per_run`

### Cost And Stability Metrics

- `mean_turn_count`
- `mean_token_usage` when available
- `variance_across_runs`
- `regression_count` against golden assertions

No candidate should be accepted on a single metric. In particular:
- lowering `repeated_confirmation_rate` while raising `false_approval_rate` is a regression
- lowering pause rate while increasing wrong-step execution is a regression

## Artifact Model

The evolution system should operate on five artifact types.

### 1. Golden Cases

Handwritten, high-signal test cases that define the intended behavior.

Suggested schema:

```json
{
  "case_id": "approval_explicit_yes_01",
  "category": "approval",
  "user_profile": {
    "approval_style": "terse",
    "clarify_tolerance": "low"
  },
  "conversation": [
    {"role": "user", "content": "用 thin supervisor 跑这个需求"},
    {"role": "assistant", "content": "draft spec ..."},
    {"role": "user", "content": "可以，就按这个开始"}
  ],
  "expected": {
    "should_approve": true,
    "should_reask_confirmation": false,
    "should_attach_run": true
  },
  "anti_goals": [
    "asks for approval again",
    "starts implementation before clarify"
  ]
}
```

### 2. Synthetic Cases

Model-generated variants derived from golden cases and friction clusters.

Use synthetic cases for breadth, not authority. Good transformations:
- paraphrases
- ambiguous approval language
- multilingual variants
- adversarial wording
- overtrigger negatives
- multi-turn correction patterns

Synthetic cases must be tagged with provenance:
- source golden case
- transformation type
- generator model
- review status

### 3. Replay Traces

Real historical run exports, including:
- `state.json`
- `session_log.jsonl`
- `decision_log.jsonl`
- `notes.jsonl`
- `friction_event`s

These are the strongest signal for runtime policy regression.

### 4. Candidate Variants

Candidate changes may target:
- `SKILL.md`
- clarify prompt templates
- approval detection rules
- notification phrasing
- runtime policy thresholds

Each candidate must declare:
- target component
- intended improvement
- expected metric movement
- risk level

### 5. Evaluation Reports

Every evaluation run should emit a stable report containing:
- dataset hash
- baseline version
- candidate version
- metrics
- per-case failures
- A/B decisions
- replay mismatches
- verdict

## Data Strategy

Use a three-layer dataset strategy.

### Layer 1: Golden Set

Start with 30-50 hand-curated high-value cases covering:
- clarify before run
- explicit approval
- ambiguous approval
- repeated approval frustration
- pause notification confusion
- completion visibility
- overtrigger and undertrigger
- runtime recovery after mismatch or stale injection

This is the contract set. It should be small, stable, and reviewed.

### Layer 2: Synthetic Expansion

Expand each golden case into 5-20 variants:
- lexical paraphrase
- more terse user
- more verbose user
- Chinese and English variants where relevant
- emotionally frustrated user
- misleading but not approving responses

This provides coverage and variance.

### Layer 3: Real Failure Mining

Mine actual `friction_event`s and postmortems to produce new candidate goldens.

High-priority friction kinds to auto-promote into review queues:
- `repeated_confirmation`
- `approval_misalignment`
- `unexpected_pause_confusion`
- `manual_override_needed`
- `hidden_completion`

This is how the system stays grounded in reality instead of synthetic vibes.

## Evaluation Harness

The evaluation harness should have four complementary modes.

### Mode A: Trigger Eval

Input:
- user request only

Output:
- whether the skill should trigger
- whether the wrong skill triggered

Used for:
- trigger precision and recall

### Mode B: Conversation Eval

Input:
- short multi-turn conversation
- optional user preference memory

Output:
- whether the skill clarified correctly
- whether approval was detected correctly
- whether unnecessary follow-up questions occurred

Used for:
- approval and clarify behavior

### Mode C: Replay Eval

Input:
- exported historical run
- candidate policy or prompt rules

Output:
- predicted decisions vs actual decisions
- mismatch report
- changed pause/resume/complete behavior

Used for:
- runtime regressions

### Mode D: Outcome Benchmark

Input:
- full workflow cases or selected end-to-end scenarios

Output:
- completion rate
- turn count
- friction rate
- human or LLM-judge preference

Used for:
- candidate promotion gate

## Blind A/B Comparator

This is the right primitive for `thin-supervisor`, and it maps well to the current Anthropic `Skill Creator` direction.

For each case:
- run baseline
- run candidate
- normalize obvious identifiers
- present outputs and event traces to a comparator without telling it which is A or B
- ask for a winner or tie under explicit rubric

Comparator rubric should score:
- task correctness
- contract compliance
- user friction
- overreach risk
- clarity of next action

The comparator should not be the only judge. It should be combined with:
- deterministic assertions
- metric counters
- replay mismatch counts

Suggested verdict policy:
- deterministic regressions fail immediately
- if deterministic checks pass, aggregate A/B wins
- require statistical margin, not a single-run win

## Variance And Stability

Do not trust one run.

Every benchmarked case should be run multiple times where nondeterminism matters:
- minimum 3 runs for small internal checks
- 5-10 runs for candidate promotion

Track:
- mean score
- stddev
- worst-case failure
- confidence interval or at least win/loss/tie counts

Acceptance should be conservative:
- strong deterministic pass rate
- candidate wins or ties enough to beat baseline with confidence
- no new catastrophic failure mode

## Online vs Offline Evolution

### Online Adaptation

Allowed:
- current-run override
- per-user preference updates
- auto-logging friction

Not allowed:
- mutating shipped skill text
- changing default approval policy globally

### Offline Evolution

Allowed:
- generate candidate skill or policy variant
- run eval + replay + benchmark
- produce report + PR

Required before merge:
- golden pass
- replay non-regression
- acceptable A/B result
- human review

## Proposed Command Surface

This repo already has the substrate for `learn` and `run history`. The next layer should look like this:

```bash
thin-supervisor eval dataset build --from-goldens
thin-supervisor eval dataset expand --synthetic
thin-supervisor eval run --suite approval-core --baseline main --candidate /tmp/variant
thin-supervisor eval compare --baseline main --candidate /tmp/variant --blind
thin-supervisor eval replay --run-export exported-run.json --candidate /tmp/variant
thin-supervisor eval benchmark --suite skill-clarify --runs 5
thin-supervisor eval report --input .supervisor/evals/<id>
```

Suggested internal modules:
- `supervisor/eval/cases.py`
- `supervisor/eval/synthetic.py`
- `supervisor/eval/executor.py`
- `supervisor/eval/comparator.py`
- `supervisor/eval/replay.py`
- `supervisor/eval/reporting.py`

## Candidate Generation Strategy

Do not start with autonomous GEPA mutation.

Recommended progression:

### Phase 1

Human-authored candidate patches plus replay/eval.

This is the fastest path to useful signal because we already know the main failure classes.

### Phase 2

Model-assisted candidate generation:
- "here are failing cases and current skill, propose 2-3 candidate variants"

Still no auto-merge.

### Phase 3

Constrained search or GEPA-like mutation on specific text regions:
- trigger description
- approval handling block
- pause notification phrasing

Only after:
- datasets are mature
- replay harness is trusted
- blind comparator is stable

## Development Workflow

The operational loop should be:

1. Run production or internal testing.
2. Persist friction and postmortems.
3. Cluster failures by kind.
4. Promote recurring failures into goldens.
5. Generate candidate patch.
6. Run trigger eval, conversation eval, replay eval, benchmark.
7. Review report.
8. Merge only if candidate improves metrics without regressions.
9. Canary on a small subset if needed.

## Recommended First Implementation Order

### Stage 1: Dataset And Eval Skeleton

Build:
- case schema
- goldens directory
- eval report format
- simple deterministic executor for clarify/approval cases

Why first:
- this gives immediate leverage without requiring automatic mutation

### Stage 2: Replay Integration

Build:
- candidate policy adapters for replay
- replay mismatch reports
- regression summaries

Why second:
- this is the highest-value safeguard for supervisor behavior

### Stage 3: Blind Comparator

Build:
- A/B normalization
- comparator prompt
- repeated-run benchmark wrapper

Why third:
- this gives quality comparison beyond simple boolean assertions

### Stage 4: Synthetic Expansion

Build:
- transformations from goldens
- provenance tags
- validation filters

Why fourth:
- useful after contract cases exist

### Stage 5: Candidate Generator

Build:
- model-assisted candidate proposal
- bounded patch suggestions

Why last:
- generation without eval discipline just creates more noise

## Files To Touch When Implementing

Likely implementation surface:
- Create: `supervisor/eval/__init__.py`
- Create: `supervisor/eval/cases.py`
- Create: `supervisor/eval/executor.py`
- Create: `supervisor/eval/replay.py`
- Create: `supervisor/eval/comparator.py`
- Create: `supervisor/eval/reporting.py`
- Modify: `supervisor/app.py`
- Modify: `supervisor/history.py`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Create: `tests/test_eval_cases.py`
- Create: `tests/test_eval_executor.py`
- Create: `tests/test_eval_replay.py`

## Decision

The right next step for `thin-supervisor` is not autonomous skill evolution.

The right next step is:
- build eval infrastructure
- seed a small but strong golden set
- wire replay into candidate validation
- add blind A/B comparison
- only then experiment with model-generated variants

That path is slower than "let the model rewrite the skill", but it is much more likely to improve the system without making it unstable.
