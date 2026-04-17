# Fat Skill / Thin Harness Rule Repartitioning

**Goal:** reduce semantic fragility in `thin-supervisor` without turning the runtime into an always-on reasoning engine. Keep deterministic mechanism rules in the harness, move semantic meaning into the skill contract and structured protocol, and add an eval subsystem that can stress the boundary without overfitting to a tiny hand-written golden set.

**This is a requirements and architecture document.** It is not yet an implementation PRD for one single slice. The purpose is to freeze the partitioning before we keep patching individual rule sites.

---

## Why This Document Exists

Recent fixes exposed the same underlying problem in several places:

- `dangerous action` is currently classified mostly by free-text regex
- `blocked` / `missing external input` is currently classified mostly by free-text regex
- the attach boundary (`ATTACHED`) still depends on textual evidence heuristics
- some recovery behavior still keys off free-form reason strings

That is not "thin harness". It is a harness doing too much semantic guessing.

At the same time, replacing every rule with skill-side or model-side reasoning would be the wrong move:

- it would increase latency on the hot path
- it would fragment control flow
- it would make the system harder to debug
- it would invite overfitting if we only optimize on a narrow eval corpus

So the right question is not:

> "rules or skill?"

It is:

> **which rules are mechanism rules and belong in the harness, and which rules are semantic rules that should move into the skill contract and structured protocol?**

---

## Core Principle

This redesign freezes one principle:

> **Harness owns mechanism. Skill owns semantic meaning.**

More concretely:

- the harness may enforce deterministic delivery, parsing, budget, state-machine, and fail-safe security rules
- the skill/protocol must carry semantic intent such as "this is blocked on external input" or "this action requires authorization"
- the runtime may use a small amount of fallback inference only when structured semantics are missing or contradictory

This is close to how Claude Code handles command safety:

- a **structured permission model** drives control flow
- command and shell safety use **deterministic mechanism rules**
- optional classifier / explainer paths are **adjacent**, not the main hot path
- risk explanation is **not** the same thing as control flow

References from Claude Code:

- [types/permissions.ts](/Users/chris/source/claude-code/src/types/permissions.ts:16)
- [bashPermissions.ts](/Users/chris/source/claude-code/src/tools/BashTool/bashPermissions.ts:1663)
- [readOnlyValidation.ts](/Users/chris/source/claude-code/src/tools/BashTool/readOnlyValidation.ts:1876)
- [permissionExplainer.ts](/Users/chris/source/claude-code/src/utils/permissions/permissionExplainer.ts:14)

---

## Background Discussion

This plan exists because three intuitions are simultaneously true:

### 1. The current harness is too semantic

Today the harness is still trying to *understand* worker intent from prose:

- "is this blocked?"
- "is this dangerous?"
- "is this really execution progress?"

That has created the same failure pattern repeatedly:

- wording drift changes runtime behavior
- attach-boundary bugs slip through because text heuristics are too loose
- recovery logic accidentally keys off log prose

That is the main fragility this plan is trying to remove.

### 2. A fully reasoning-driven runtime would also be wrong

It would be a mistake to replace every regex with a model call.

That would:

- slow down the hot path
- make failures harder to debug
- create too many hidden decision surfaces
- tempt the system to overfit to a narrow eval corpus

So the target state is **not** "semantic reasoning everywhere."

### 3. Worker-declared semantics are useful, but not self-authenticating

The worker is the best source for intent, but not the only source of truth.

Examples:

- a worker may under-report danger
- a worker may call attach/admin work "execution"
- a worker may omit a missing credential until a retry loop already started

So the design must avoid both extremes:

- **too much harness guessing**
- **too much blind trust in worker self-report**

The right answer is:

> **worker-declared semantics become the primary semantic input, but harness keeps deterministic fail-safe overrides and explicit contradiction handling.**

This is also the closest match to what Claude Code does well:

- structure first
- deterministic guardrails on the hot path
- optional classifier/explainer only at the edges

---

## Frozen Decisions Before Implementation

The following points are now frozen and should be treated as implementation constraints, not open design questions.

### A. Trust model for semantic fields

Structured semantic fields are **preferred inputs**, not unconditional truth.

Rules:

- worker-emitted semantic fields are **advisory by default**
- harness may **fail-safe override** them when deterministic evidence contradicts them
- runtime-owned fields remain authoritative for runtime-owned state

Concrete examples:

- if the worker emits `requires_authorization: false` but a high-confidence destructive primitive is detected, the runtime must still fail closed
- if the worker emits `progress_class: execution` but the first checkpoint is contradicted by deterministic attach-boundary checks, the runtime may hold `ATTACHED` and `RE_INJECT`
- if the worker emits `blocking_inputs: []` but also emits a business escalation or an explicit missing-credential reason code, the normalizer must mark the payload contradictory and route conservatively

This means the plan is **not** "trust the worker completely."
It is "stop guessing from prose when the worker already gave structured intent, but keep deterministic overrides."

### B. Protocol versioning is mandatory

The structured protocol cannot ship as an implicit silent schema change.

Required rule:

- every checkpoint schema revision must carry an explicit version marker

Recommended shape:

```text
checkpoint_schema_version: 1 | 2 | ...
```

Compatibility rules:

- **v1 / missing version** = legacy checkpoint, use compatibility normalization + heuristics
- **v2** = structured semantics available, prefer structured fields
- replay/export/eval must preserve both raw checkpoint payload and normalized semantics so historical runs remain analyzable

Do **not** make the runtime infer "new style vs old style" only from field presence.

#### Sunset policy for v1 on the live path

Live-path sunset is **not** by hard date alone and **not** by fallback-rate
alone. Both signals must hold before v1 is rejected on the live ingest path.
Historical replay / export keeps v1 read support **indefinitely**.

Live-ingest sunset trigger (both must hold):

1. heuristic fallback-rate below threshold for **14 consecutive days**
   (fallback-rate comes from the Slice 4B robustness report)
2. within the sunset observation window, **every** surface in the frozen
   live checkpoint ingress set has produced at least one successful
   `checkpoint_schema_version=2` live checkpoint (old pre-window
   observations do not count toward the trigger)

Frozen **live checkpoint ingress set** (closed set, not open-ended):

- `tmux`
- `jsonl`
- `open_relay`

Explicitly **not** ingress surfaces and therefore not part of the trigger:

- `session_detect` — discovery path, does not produce checkpoints
- `operator_channel` — operator / control plane, not a worker checkpoint ingress

Future extensibility rule: any new hook / relay ingress surface must be
explicitly added to this set via a doc revision **before** it counts toward
the v1 sunset trigger. The trigger set does not silently widen when new
surfaces ship.

Lifecycle once triggered:

- **Deprecation**: live path still accepts v1 but emits warning + upgrade hint
- **Enforcement**: live path rejects v1; `checkpoint_schema_version` absent
  or `< 2` is a compatibility error; replay / export path unchanged

### C. One canonical normalization layer

Raw checkpoint text/fields must be normalized exactly once.

Required rule:

- add one canonical normalization step that converts raw checkpoint payloads into a normalized semantic object

Everything downstream should consume that normalized object:

- loop
- continue/escalation gates
- pause summary
- recovery planner
- eval/replay

They should **not** each reinterpret raw `summary`, `needs`, `question_for_supervisor`, or raw structured fields independently.

This prevents the current drift pattern where semantics are re-encoded in:

- `rules.py`
- `loop.py`
- `pause_summary.py`
- `interventions.py`

### D. Dangerous-action precedence

Dangerous-action control flow needs an explicit precedence order.

Frozen order:

1. **Deterministic action metadata or mechanism-visible facts**
2. **Spec / acceptance / policy constraints**
3. **Worker-declared structured semantics**
4. **Small hard fail-safe rules**
5. **Optional fallback judge**

Meaning:

- if the runtime can directly observe a destructive action primitive, that beats worker self-report
- if policy says a class of action requires authorization, that also beats worker self-report
- worker declarations are still the main semantic path when deterministic action metadata is unavailable
- regex-like hard rules shrink to a narrow fail-safe set
- judge/classifier remains rare fallback only

This is the required guard against drifting back into:

- free-text danger regex as the main classifier
- or blind trust in worker declarations

### E. Contradiction routing is classified by dimension

Section A requires that worker-declared semantic fields be treated as
advisory, with harness fail-safe overrides when deterministic evidence
contradicts them. This section freezes the **default routing** for those
contradictions so "route conservatively" is not left undefined.

One-line principle:

> Safety contradictions fail closed, business contradictions escalate,
> execution-semantic contradictions re-inject, and runtime-owned fields
> never yield to worker self-report.

Full routing table:

| Contradiction class | Example | Action | `reason_code` |
| --- | --- | --- | --- |
| **Safety** | worker says `requires_authorization=false` but deterministic action metadata / hard rule hits a destructive primitive | `PAUSED_FOR_HUMAN`, `pause_class=safety` | `esc.authorization_contradiction` |
| **Execution semantic** | worker says `progress_class=execution` but attach-boundary deterministic checks still say admin / prior-phase | hold `ATTACHED`, `RE_INJECT` to re-request structured checkpoint; consume a dedicated re-inject budget, do **not** touch retry budget | `sem.progress_class_contradiction` / `sem.evidence_scope_contradiction` |
| **Business escalation** | `blocking_inputs=[]` but explicit missing-credential / external-input signal is present; **or** `escalation_class` disagrees with other worker-emitted business signals (e.g. `escalation_class=none` while a business blocker is declared) | `ESCALATE_TO_HUMAN`, `pause_class=business` (it is a real missing input or a miscategorized business escalation, not a mere reporting defect) | `sem.blocking_inputs_contradiction` **or** `sem.escalation_class_contradiction` |
| **Runtime-owned state** | worker emits a field like `escalation_class=recovery` that only runtime owns | runtime state wins; worker field is demoted to log + contradiction signal | `sem.runtime_owned_field_conflict` |

Notes:

- Safety contradictions stay in `esc.*` because they are fail-closed
  escalations, not mere protocol-integrity issues.
- All other contradiction codes live in `sem.*` (see Structured Protocol
  Additions → `reason_code`).
- A contradicted payload must never silently `CONTINUE`.

---

## Problem Statement

The current system has two kinds of rules mixed together:

### A. Mechanism rules

These are appropriate for the harness:

- delivery / ack / timeout
- terminal readiness
- daemon recovery ownership
- state transitions
- retry / re-inject budgets
- path constraints
- deterministic verifier execution
- high-confidence fail-safe denies

### B. Semantic rules

These are currently too embedded in the harness:

- whether a checkpoint is **blocked**
- whether the worker needs **external input**
- whether an action is **dangerous / authorization-requiring**
- whether first evidence is truly **execution evidence on the current node**
- whether a pause is **business / safety / review / recovery**

The harness is currently doing too much of B using:

- regex patterns
- keyword allowlists
- free-text `reason` matching
- duplicated mapping logic across `rules.py`, `loop.py`, `pause_summary.py`, and `interventions.py`

That is the main fragility we need to remove.

---

## Current Fragility Inventory

These are the most important current hotspots.

### 1. Free-text escalation classification

Current files:

- [supervisor/gates/rules.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/rules.py:14)
- [supervisor/gates/escalation.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/escalation.py:17)

Examples:

- `MISSING_EXTERNAL_INPUT_PATTERNS`
- `DANGEROUS_ACTION_PATTERNS`
- `BLOCKED_PATTERNS`

Problem:

- coverage is lexical, not semantic
- prompt wording drift can silently change runtime behavior
- natural variants are easy to miss

### 2. Attach-boundary execution-evidence classification

Current file:

- [supervisor/gates/rules.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/rules.py:63)

Examples:

- `EXECUTION_EVIDENCE_PATTERNS`
- `is_admin_only_evidence()`

Problem:

- first-checkpoint semantics are already defined in the skill contract
- runtime still re-guesses them from free text
- false positives / false negatives are both easy

### 3. Recovery recipe selection by free-form reason strings

Current file:

- [supervisor/interventions.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/interventions.py:28)

Examples:

- `"no checkpoint received within delivery timeout"`
- `"idle timeout"`
- `"inject failed"`
- `"retry budget exhausted"`

Problem:

- recovery control is partially keyed by prose
- reason wording becomes a compatibility boundary

### 4. Pause UX and operator next-action semantics are partly duplicated

Current files:

- [supervisor/pause_summary.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/pause_summary.py:28)
- [supervisor/operator/session_index.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/operator/session_index.py:211)

Problem:

- business semantics are projected from runtime strings into operator UX
- changes in runtime reasons can drift into status / dashboard / TUI

---

## Frozen Partition: What Stays In Harness

These rule families stay in the harness and remain deterministic.

### 1. Transport and delivery mechanism

- terminal readiness
- inject / defer / offline
- delivery ack / timeout
- pane liveness
- daemon resume / orphan recovery wiring

These are mechanism rules, not semantic rules.

### 2. State-machine progression

- `TopState`
- `DeliveryState`
- retry budgets
- re-inject budgets
- verifier routing
- transition legality

These must stay deterministic and cheap.

### 3. Deterministic, syntax-level or path-level fail-safe rules

Examples:

- path / filesystem boundaries
- explicit observation-only surfaces
- deterministic shell / command constraints
- high-confidence destructive primitives when directly observable from structured action metadata

Important:

The harness may still keep **small, high-confidence fail-safe rules**.
What it must stop doing is broad semantic classification from free-form checkpoint prose.

### 4. Recovery execution

- auto-intervention planning and execution
- retry limits
- recovery escalation after exhaustion

But recovery selection must move from **reason string matching** to **structured reason codes**.

---

## Frozen Partition: What Moves To Skill + Structured Protocol

These rule families should no longer be primarily inferred from free text inside the harness.

### 1. Escalation semantics

The worker should explicitly encode:

- whether it is blocked on external input
- whether it requires authorization
- whether it is asking for business clarification
- whether it is requesting review

### 2. Progress semantics

The worker should explicitly encode:

- whether the checkpoint is execution work vs admin work
- whether the evidence targets the current node
- whether this is first valid execution evidence for the node

### 3. Pause semantics

The runtime should not derive `pause_class` from ad hoc text if it can instead receive:

- `escalation_class`
- `requires_authorization`
- `blocking_inputs`
- `review_required_by`

### 4. Finish semantics

The worker should continue to emit concrete finish evidence under the skill contract, and the harness should verify structurally rather than re-guessing textual meaning where possible.

---

## Structured Protocol Additions

This redesign requires a more expressive checkpoint and escalation protocol.

The checkpoint payload should gain semantic fields like:

```text
checkpoint_schema_version: 2
progress_class: execution | verification | admin
evidence_scope: current_node | prior_phase | unknown
escalation_class: none | business | safety | review
requires_authorization: true | false
blocking_inputs:
  - <external input or credential>
risk_level: low | medium | high
reason_code: <esc.* | rec.* | ver.* | sem.*>
```

The following wire names are **frozen** (downstream slices, Slice 4A
invariants, and the contradiction routing table all reference them by
these exact names):

- `checkpoint_schema_version`
- `progress_class`
- `evidence_scope`
- `escalation_class`
- `requires_authorization`
- `blocking_inputs`
- `reason_code` (see dedicated subsection below)

Other fields shown above (e.g. `risk_level`) remain **requirements, not
final wire names** — their final key may still change before Slice 2
ships, as long as the frozen set above is preserved.

### Meaning of the new fields

#### `progress_class`

What kind of work the checkpoint is reporting:

- `execution`
- `verification`
- `admin`

This should replace most of the current attach-boundary guesswork.

#### `evidence_scope`

What the evidence actually applies to:

- `current_node`
- `prior_phase`
- `unknown`

This lets the `ATTACHED` boundary be enforced structurally instead of by keyword patterns.

#### `escalation_class`

The worker-side semantic meaning of escalation:

- `none`
- `business`
- `safety`
- `review`

`recovery` should generally remain a supervisor/runtime-owned class, not a worker-emitted class.

#### `requires_authorization`

Independent from `risk_level`.

This is the main control-flow bit for dangerous actions:

- `true` means the worker believes the current path crosses an authorization boundary
- the harness may still override to safe fail-closed on high-confidence hard rules

#### `blocking_inputs`

Explicit list of what is missing.

This should replace a large fraction of the current `MISSING_EXTERNAL_INPUT` regex dependence.

#### `risk_level`

Used for explanation and UI.

Important:

> `risk_level` is **not** the primary control-flow field.

Like Claude Code's permission explainer, it should mostly support operator understanding, not replace structured authorization semantics.

#### `reason_code`

A stable machine-readable code used to eliminate prose matching across
escalation, recovery, verification, and contradiction routing.

**Frozen wire shape (Slice 1):**

- exactly one `reason_code` field on the wire
- every value carries a scoped **prefix** drawn from a closed set of
  four families: `esc.*`, `rec.*`, `ver.*`, `sem.*`
- the normalizer is the **only** component allowed to decode these
  prefixes into internal typed semantics (e.g. projecting into
  `escalation_reason_code` / `recovery_reason_code` /
  `verification_reason_code`)
- internal evolution of that projection **never** requires a protocol
  bump; the wire stays at one field

**Family responsibilities (frozen):**

- `esc.*` — escalation causes, including fail-closed safety
  contradictions (primarily worker-declared need for human input /
  authorization, plus runtime-raised `esc.authorization_contradiction`)
- `rec.*` — recovery causes (runtime-owned failure modes)
- `ver.*` — verification failures (test / acceptance results)
- `sem.*` — semantic contradictions and protocol-integrity issues

Keep these responsibilities undiluted — in particular, `sem.*` is where
non-safety contradictions go, and `rec.*` stays reserved for
runtime-owned recovery causes only.

**Examples (by family):**

- `esc.missing_credentials`
- `esc.authorization_required`
- `esc.review_required`
- `esc.authorization_contradiction` *(safety contradiction — stays in `esc.*`)*
- `rec.delivery_timeout`
- `rec.idle_timeout`
- `rec.inject_failed`
- `rec.node_mismatch_persisted`
- `rec.retry_budget_exhausted`
- `ver.test_failed`
- `sem.progress_class_contradiction`
- `sem.evidence_scope_contradiction`
- `sem.blocking_inputs_contradiction`
- `sem.escalation_class_contradiction`
- `sem.runtime_owned_field_conflict`

> Do **not** let `reason_code` become an untyped catch-all, and do **not**
> split it into multiple wire fields. The one-field + four-prefix shape is
> the frozen protocol.

---

## Dangerous Action Contract

This needs special treatment because it is currently the clearest example of harness overreach.

### Current problem

Today the runtime mostly decides dangerousness from lexical patterns in:

- [supervisor/gates/rules.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/rules.py:21)

That is too weak as a primary semantic mechanism.

### New rule

Dangerousness becomes a **hybrid** system.

#### Worker / skill responsibilities

The worker must explicitly declare when a proposed action crosses an authorization boundary:

- `requires_authorization: true`
- `escalation_class: safety`
- `risk_level: high`
- `reason_code: esc.authorization_required`

#### Harness responsibilities

The harness keeps only a **small fail-safe set** of high-confidence hard detections.

Examples of acceptable hard detections:

- explicit destructive git operations (`push --force` to protected targets if that is structurally visible)
- explicit destructive data primitives
- explicit production deletion primitives

These hard rules are not meant to classify all danger.
They exist to fail closed when the worker under-reports.

#### Optional judge responsibilities

If the worker omits or contradicts these fields, an optional fallback judge may classify the case as:

- `safety`
- `business`
- `review`
- `none`

But that fallback must not become the default hot path.

---

## Attach Boundary Contract

The `ATTACHED` state already exists. The missing piece is that the boundary still relies too much on heuristic evidence detection.

### Required future rule

`ATTACHED -> RUNNING` should primarily depend on:

- `progress_class == execution`
- `evidence_scope == current_node`

The runtime may retain a narrow heuristic fallback only when those fields are missing.

### Fallback policy

If structured semantics are missing:

- first try a focused `RE_INJECT`
- only use heuristic `is_admin_only_evidence()` as temporary compatibility fallback
- log that compatibility fallback was used

This makes the heuristic path observable and reducible over time.

---

## Recovery Contract

`RECOVERY_NEEDED` is now a real top-level state. The remaining requirement is to stop selecting recovery behavior from prose.

### Required future rule

Every recovery entry must carry a structured `reason_code`.

The auto-intervention manager must route on `reason_code`, not on human-facing reason text.

### Example mapping

```text
rec.delivery_timeout           -> re-inject with progress-check wording
rec.idle_timeout               -> re-inject with status-now wording
rec.inject_failed              -> one more inject attempt, then recovery pause
rec.node_mismatch_persisted    -> resume current node with explicit node reset
rec.retry_budget_exhausted     -> recovery pause or controlled retry recipe
```

The human-facing `reason` string remains useful for logs and UI, but it is no longer the key for control flow.

---

## Performance Contract

This redesign must not turn the runtime into an inference-heavy system.

### Hard constraints

1. **No LLM or judge call on every checkpoint by default**
2. **No LLM or judge call on every delivery event**
3. **No per-rule classifier fanout**
4. **Deterministic structured fields must remain the primary hot path**

### Allowed inference usage

Inference is allowed only for:

- explicit fallback when structured semantic fields are missing
- low-frequency ambiguity resolution
- operator-facing explanation
- offline eval and synthesis

### Performance principle

> Replace many scattered semantic regexes with a few structured fields, not with many tiny judge calls.

This is the only way to make the system both cleaner and faster.

---

## Eval Subsystem Requirements

This work needs its own eval subsystem.

The subsystem should not only run hand-authored goldens. It must also synthesize dynamic cases so we do not optimize only for known examples.

### Eval corpus layers

#### Layer 1: Hand-authored goldens

Small, curated, high-value cases:

- real incidents (like the Phase 17 attach failure)
- representative blocked / safety / review / recovery cases
- known regressions

These are stability anchors.

#### Layer 2: Templated synthetic expansions

Programmatically mutate the goldens:

- vary wording
- vary ordering of fields
- vary evidence phrasing
- vary number / order of needs and question lines
- vary mixed-language phrasing
- vary irrelevant admin artifacts vs real execution artifacts

This checks lexical robustness without requiring random free-form generation.

#### Layer 3: Dynamic compositional generation

Generate cases by combining dimensions:

- `progress_class`
- `evidence_scope`
- `escalation_class`
- `requires_authorization`
- `blocking_inputs`
- checkpoint status
- top state

This tests state-machine invariants and conflict handling.

#### Layer 4: Property / invariant checks

Assert broad invariants such as:

- `ATTACHED` never advances to `RUNNING` without execution evidence on current node
- `requires_authorization=true` never leads to `CONTINUE`
- `reason_code=rec.delivery_timeout` never becomes `PAUSED_FOR_HUMAN[business]`
- `blocking_inputs != []` never routes to `RE_INJECT`

### Overfitting guardrails

To avoid fitting the implementation too tightly to one corpus:

1. keep a held-out eval set not used during prompt/rule iteration
2. separate hand-authored goldens from synthetic expansions
3. mutate wording independently from logic labels
4. track robustness by class, not only pass/fail aggregate
5. measure fallback-rate:
   - how often heuristics were used
   - how often structured fields were missing
   - how often judge fallback triggered

If the system only passes because prompts were tuned to one narrow eval phrasing, this plan has failed.

---

## Migration Plan

This should ship in explicit slices.

### Slice 1: Rule inventory and structured reason codes

Deliverables:

- inventory every semantic rule site in harness
- introduce stable `reason_code` where runtime currently keys on prose
- freeze the versioning and normalization contract for legacy (`v1`) vs structured (`v2`) checkpoints
- **freeze the wire-level `reason_code` format**: a single field with
  four prefix families `esc.*` / `rec.*` / `ver.*` / `sem.*`; the
  normalizer is the only decoder
- no behavioral widening yet

### Slice 2: Structured checkpoint semantics

Deliverables:

- extend checkpoint protocol with semantic fields
- update skill contract and prompts
- emit explicit schema version markers (every v2 skill must emit
  `checkpoint_schema_version: 2`)
- keep current regexes as compatibility fallback

### Slice 3: Harness consumption switch

Deliverables:

- `ATTACHED` gate reads structured fields first
- escalation reads structured fields first
- recovery planner routes on `reason_code`
- regex paths remain only as fallback

**Merge gate:** Slice 3 may merge only after Slice 4A is in place and
its invariant checks are green. Without that gate, flipping the hot
path from regex to structured fields has no semantic oracle and
regressions will land silently.

### Slice 4A: Minimum regression harness (merge-gate for Slice 3)

Deliverables:

- hand-authored goldens, including the Phase 17 attach-failure replay
- invariant checks covering:
  - `ATTACHED` never advances without `progress_class=execution` **and** `evidence_scope=current_node`
  - `requires_authorization=true` never leads to `CONTINUE`
  - `blocking_inputs != []` never routes to `RE_INJECT`
  - `reason_code` families (`esc.*` / `rec.*` / `ver.*` / `sem.*`)
    never cross-route; in particular `sem.*` contradictions must route
    per Section E, not silently fall into `esc.*` / `rec.*` paths
- v1 / v2 mixed-version cases

Ships in parallel with Slice 3 but must land first.

### Slice 4B: Full eval subsystem

Deliverables:

- templated synthetic expansions
- dynamic compositional state-machine generation
- robustness reporting
- fallback-rate trend analysis (feeds the Slice 5 sunset trigger)

Follows or parallels Slice 3; not a merge gate.

### Slice 5: Fallback reduction and v1 live-path sunset

Deliverables:

- shrink regex classifiers
- remove duplicated semantic mapping
- keep only small hard fail-safe rules and compatibility fallback
- **v1 live-path sunset** per Section B: dual-signal trigger
  (fallback-rate threshold from Slice 4B robustness report **plus**
  every frozen ingress surface — `tmux` / `jsonl` / `open_relay` —
  observed emitting `checkpoint_schema_version=2` within the sunset
  observation window); deprecation → enforcement phases
- v1 read support remains permanent on the replay / export path

---

## Success Criteria

This redesign succeeds when all of the following are true:

1. dangerous / blocked / missing-input routing is primarily driven by structured protocol fields, not regex
2. `ATTACHED` boundary is primarily enforced by structured semantics, not keyword evidence detection
3. recovery auto-intervention uses `reason_code`, not prose matching
4. contradiction handling routes by class (safety → fail-closed,
   business → escalate, execution-semantic → re-inject, runtime-owned →
   runtime wins), never silently `CONTINUE`s on a contradicted payload
5. hot-path latency remains close to today's deterministic path
6. eval robustness improves on both curated and synthetic corpora
7. fallback heuristic usage is measurable and trends downward over time

---

## Non-Goals

This plan does **not** require:

- removing every heuristic from the harness
- replacing every rule with LLM reasoning
- moving path / transport / terminal / retry logic into the skill
- introducing an expensive judge call into every loop iteration

The target state is not "no rules".

The target state is:

> **deterministic harness for mechanism, structured protocol for semantics, and narrow fallback inference only where necessary.**

---

## Immediate Next Step

Before changing control flow further, create a **rule inventory table** with one row per current rule family:

- file / function
- current purpose
- category: mechanism vs semantics
- current implementation mode: hard rule / regex / prose match / heuristic / fallback judge
- target home: harness / skill / structured protocol / optional judge
- migration slice

That inventory is the required bridge from this document to actual implementation work.
