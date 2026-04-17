# Supervision State Machine And Human Intervention Semantics

**Goal:** describe the current end-to-end supervision state machine clearly enough that we can review the design, explain the current `Phase 17` incident from first principles, and decide where the model needs to change.

**Scope of this document:** current-state analysis first. This is not yet an implementation PRD. It is a state-machine and prompt-contract review.

---

## Why This Document Exists

The current system has at least two kinds of "human involvement":

1. **Business / product input**
   - clarify requirements
   - approve the plan
   - answer genuine external questions

2. **Operational recovery**
   - recover a stuck or mis-delivered session
   - inspect a pane that stopped producing checkpoints
   - manually resume after delivery timeout, retry exhaustion, or similar issues

Today both often collapse into the same top-level runtime state:

- `PAUSED_FOR_HUMAN`

That creates product confusion:

- is the worker waiting for a business answer?
- is the supervisor asking for operator recovery?
- is the run actually blocked, or did delivery just stall?

This document decomposes the flow so those questions can be answered from the model itself.

---

## The System Has Two Interleaved State Machines

There is not one single state machine today. There are two:

1. **Human-facing workflow state machine**
   - clarify
   - plan
   - approve
   - attach
   - execute

2. **Runtime supervision state machine**
   - `TopState`
   - `DeliveryState`
   - gate decisions
   - verification
   - intervention / pause

The current design problem is largely at the boundary between these two.

---

## Layer 1: Human-Facing Workflow State Machine

This layer is driven primarily by the skill contract in:

- [skills/thin-supervisor/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md)
- [skills/thin-supervisor/references/contract.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/references/contract.md)

### States

#### `H0_PRECHECK`

Purpose:
- environment validation before planning or execution

Entry:
- user invokes the skill

Actions:
- `thin-supervisor bootstrap`

Outputs:
- tmux presence known
- pane ownership known
- `.supervisor/` initialized if needed
- daemon availability known

Source:
- [skills/thin-supervisor/SKILL.md:58](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:58)
- [supervisor/bootstrap.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/bootstrap.py:33)

Transitions:
- success -> `H1_CLARIFY`
- conflict / pane locked / not in tmux -> stop before planning

#### `H1_CLARIFY`

Purpose:
- align on intent, outcome, scope, non-goals, acceptance, decision boundaries

Outputs:
- `.supervisor/clarify/<slug>.md`

Source:
- [skills/thin-supervisor/SKILL.md:80](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:80)

Transitions:
- enough clarity -> `H2_PLAN`
- still ambiguous -> remain in `H1_CLARIFY`

#### `H2_PLAN`

Purpose:
- generate the spec and self-review it

Outputs:
- `.supervisor/specs/<slug>.yaml`
- `.supervisor/plans/<slug>-review.md`

Source:
- [skills/thin-supervisor/SKILL.md:118](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:118)

Transitions:
- plan ready -> `H3_AWAIT_APPROVAL`
- unclear / missing design decisions -> back to `H1_CLARIFY`

#### `H3_AWAIT_APPROVAL`

Purpose:
- explicit user approval gate before execution

Source:
- [skills/thin-supervisor/SKILL.md:250](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:250)
- [skills/thin-supervisor/references/contract.md:14](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/references/contract.md:14)

Transitions:
- approve -> `H4_ATTACH`
- adjust -> back to `H2_PLAN`
- reject -> stop

#### `H4_ATTACH`

Purpose:
- approve spec
- bind the current pane to a supervised run

Actions:
- `thin-supervisor bootstrap`
- `thin-supervisor spec approve ...`
- `thin-supervisor run register ...`

Source:
- [skills/thin-supervisor/SKILL.md:294](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:294)

Transitions:
- attach succeeds -> `H5_EXECUTE`
- attach fails -> stop / tell user

#### `H5_EXECUTE`

Purpose:
- worker executes spec nodes under supervisor control

Contract:
- emit checkpoints
- do not ask "should I continue?"
- do not skip verification

Source:
- [skills/thin-supervisor/SKILL.md:314](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:314)
- [skills/thin-supervisor/references/contract.md:3](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/references/contract.md:3)

Transitions:
- runtime decides continue / verify / pause / complete

#### `H6_HUMAN_REENTRY`

Purpose:
- user comes back after runtime has paused or completed

Important:
- this is currently overloaded
- it includes both genuine business clarification and operational recovery

---

## Layer 2: Runtime Supervision State Machine

This layer is driven by:

- [supervisor/domain/enums.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/domain/enums.py)
- [supervisor/loop.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py)
- [supervisor/instructions/composer.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/instructions/composer.py)
- [supervisor/gates/continue_gate.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/continue_gate.py)

### 2.1 TopState

Defined in:
- [supervisor/domain/enums.py:3](/Users/chris/workspace/lite-harness-supervisor/supervisor/domain/enums.py:3)

States:

- `READY`
- `RUNNING`
- `GATING`
- `VERIFYING`
- `PAUSED_FOR_HUMAN`
- `COMPLETED`
- `FAILED`
- `ABORTED`

### 2.2 DeliveryState

Defined in:
- [supervisor/domain/enums.py:13](/Users/chris/workspace/lite-harness-supervisor/supervisor/domain/enums.py:13)

States:

- `IDLE`
- `INJECTED`
- `SUBMITTED`
- `ACKNOWLEDGED`
- `STARTED_PROCESSING`
- `FAILED`
- `TIMED_OUT`

DeliveryState is not the same as TopState. It is a sub-state for "did the worker actually receive and start processing the injected instruction?"

### 2.3 Runtime transition graph

#### `R0_READY`

When a run is first registered:
- top state is `READY`

Transition:
- supervisor injects the first instruction
- then sets top state to `RUNNING`

Source:
- [supervisor/loop.py:473](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:473)

#### `R1_RUNNING`

Meaning:
- supervisor believes the current node is being worked on

Sub-state:
- delivery may be `INJECTED`, `ACKNOWLEDGED`, or `STARTED_PROCESSING`

Events that can happen:
- new checkpoint arrives
- no checkpoint arrives within timeout
- no visible output at all for idle timeout
- injection fails

#### `R2_GATING`

Meaning:
- a checkpoint or question arrived and the supervisor must decide what to do next

Entry:
- `handle_event()` receives agent output / question / timeout and transitions to `GATING`

Source:
- [supervisor/loop.py:70](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:70)

#### `R3_VERIFYING`

Meaning:
- supervisor runs the node verifiers

Entry:
- checkpoint says `step_done` or `workflow_done`

Source:
- [supervisor/loop.py:114](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:114)

#### `R4_PAUSED_FOR_HUMAN`

Meaning today:
- one catch-all state for everything that needs a human or operator

Entry sources include:
- checkpoint said `blocked`
- missing external input
- dangerous action
- retry budget exhausted
- verification budget exhausted
- delivery timeout
- idle timeout
- repeated node mismatch
- observation-only surface cannot safely inject

This is the main semantic overload.

#### `R5_COMPLETED`

Entry:
- final verification passed
- finish gate satisfied

#### `R6_FAILED / R7_ABORTED`

Terminal failure states.

---

## Runtime Decision Machine

After every accepted checkpoint, the runtime does not immediately advance. It runs a second decision machine.

### Inputs

- current spec node
- last checkpoint
- retry budget
- done nodes

Source:
- [supervisor/loop.py:95](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:95)
- [supervisor/llm/prompts/continue_or_escalate.txt](/Users/chris/workspace/lite-harness-supervisor/supervisor/llm/prompts/continue_or_escalate.txt)

### Outputs

- `CONTINUE`
- `VERIFY_STEP`
- `RETRY`
- `ESCALATE_TO_HUMAN`
- `FINISH`
- `ABORT`

### Important current rule

The default rule is:

- prefer `CONTINUE` unless there is strong evidence of completion or blocking

This is encoded both in prompt and code:

- [supervisor/llm/prompts/continue_or_escalate.txt:18](/Users/chris/workspace/lite-harness-supervisor/supervisor/llm/prompts/continue_or_escalate.txt:18)
- [supervisor/gates/continue_gate.py:24](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/continue_gate.py:24)

This default matters a lot for the current incident.

---

## Prompt And Skill Contracts That Shape The State Machine

### Contract 1: Attach immediately after approval

Once approval is given:

- approve the spec
- attach immediately
- do not start implementation before attach succeeds

Source:
- [skills/thin-supervisor/SKILL.md:291](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:291)

Effect on the state machine:
- `H3_AWAIT_APPROVAL` transitions directly into `H4_ATTACH`, then into runtime
- there is no explicit "attached but execution has not actually started" state

### Contract 2: Checkpoint after meaningful progress

Current wording:
- "After meaningful progress, output a checkpoint block..."

Sources:
- [skills/thin-supervisor/SKILL.md:325](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:325)
- [supervisor/instructions/composer.py:67](/Users/chris/workspace/lite-harness-supervisor/supervisor/instructions/composer.py:67)
- [supervisor/llm/prompts/checkpoint_protocol.txt](/Users/chris/workspace/lite-harness-supervisor/supervisor/llm/prompts/checkpoint_protocol.txt)

Important gap:
- the contract says "meaningful progress"
- it does **not** say:
  - the first checkpoint after attach must prove real execution progress on the active node
  - planning / attach / artifact generation must not be used as execution evidence for the current node

### Contract 3: Soft confirmations should not escalate

The system explicitly suppresses "should I continue?" style behavior and prefers `CONTINUE`.

Sources:
- [skills/thin-supervisor/references/contract.md:7](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/references/contract.md:7)
- [supervisor/gates/continue_gate.py:24](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/continue_gate.py:24)

Effect:
- once a checkpoint is accepted, the system strongly prefers to keep execution flowing rather than bounce back to the user

### Contract 4: `blocked` means genuine external blocker

Sources:
- [skills/thin-supervisor/references/contract.md:19](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/references/contract.md:19)
- [skills/thin-supervisor/references/escalation-rules.md:15](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/references/escalation-rules.md:15)

Effect:
- business / authority / external-input blockers are supposed to be explicit
- the system is not supposed to ask humans for routine "keep going?" confirmations

---

## Where The Current Design Can Get Stuck

There are several structurally risky transition points.

### Risk A: Attach boundary ambiguity

Current flow:

`H3_AWAIT_APPROVAL -> H4_ATTACH -> R0_READY -> R1_RUNNING`

Missing state:

- `ATTACHED_BUT_NOT_YET_EXECUTING_CURRENT_NODE`

Without that state, the worker can emit a first `working` checkpoint that still mostly reports:

- clarify/spec artifacts
- attach success
- baseline checks

and the runtime will accept it as node progress.

### Risk B: Over-broad first checkpoint acceptance

Today, a checkpoint is accepted if:

- run id matches
- node matches current node
- it is not a duplicate

Source:
- [supervisor/loop.py:588](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:588)

What is **not** checked:

- whether the evidence actually represents execution progress for the current node objective

That is the exact hole through which the current incident passed.

### Risk C: Delivery timeout escalates to the same human state as business blockers

Current delivery timeout path:

- `RUNNING`
- injection sent
- no new checkpoint before deadline
- `delivery_state = TIMED_OUT`
- `_pause_for_human(...)`

Source:
- [supervisor/loop.py:525](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:525)

This means:
- a business blocker
- a missing credential
- a dangerous action
- a dead/stuck session

all converge into the same top state:
- `PAUSED_FOR_HUMAN`

### Risk D: Operator recovery is not a true first-class state

The code already knows about recovery-style situations:

- delivery timeout
- idle timeout
- node mismatch
- observation-only delivery failure
- retry budget exhaustion

But these remain payload reasons attached to one generic pause state instead of becoming explicit runtime categories.

### Risk E: Auto-intervention exists but is narrow

There is already an `AutoInterventionManager`, but it only handles a few cases:

- node mismatch auto-recovery
- retry budget auto-recovery

Source:
- [supervisor/interventions.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/interventions.py)

It does **not** currently absorb the full "operational recovery" class.

---

## Reconstructing The Phase 17 Incident From The State Machine

The important test of this document is:

> Can the state machine itself predict the exact problem we observed?

Yes.

### Observed event chain

From:
- [state.json](/Users/chris/Documents/openclaw-template/.worktrees/phase17-research-graph-visualization/.supervisor/runtime/runs/run_89576d49897f/state.json)
- [session_log.jsonl](/Users/chris/Documents/openclaw-template/.worktrees/phase17-research-graph-visualization/.supervisor/runtime/runs/run_89576d49897f/session_log.jsonl)

The run did this:

1. user approved the spec
2. skill attached immediately
3. runtime injected `step_1_graph_api`
4. worker emitted **checkpoint #1**
5. that checkpoint mostly reported:
   - clarify/spec/review artifacts exist
   - test baseline is clean
   - attach succeeded
6. runtime accepted that checkpoint
7. gate defaulted to `CONTINUE`
8. runtime injected again
9. no second checkpoint arrived within 60s
10. delivery timed out
11. runtime entered `PAUSED_FOR_HUMAN`

### Why this is predictable from the model

This incident is exactly what we should expect from the current model if:

- attach has no explicit post-attach/pre-execution state
- first `working` checkpoints are not constrained to current-node execution evidence
- continue gate prefers `CONTINUE`
- delivery timeout reuses the generic human-pause state

So yes: the state machine decomposition does explain the current incident.

If it could not explain it, the decomposition would be wrong. It does explain it.

---

## Current Human Intervention Semantics

Today `PAUSED_FOR_HUMAN` is carrying at least four different meanings:

1. **Business clarification needed**
   - true missing external input
   - spec ambiguity
   - blocked by user decision

2. **Safety authorization needed**
   - dangerous irreversible action
   - destructive operation

3. **Verification / review needed**
   - explicit review requirement
   - evidence insufficient

4. **Operational recovery needed**
   - delivery timeout
   - idle timeout
   - session not advancing
   - inject path unreliable
   - mismatch / retry exhaustion

From a product perspective, categories 1-3 are qualitatively different from 4.

Your current intuition is correct:

- categories 1-3 are rare and semantically meaningful
- category 4 should mostly be absorbed by the supervisor itself, or at least surfaced differently

---

## Design Conclusions From This State Machine

### Conclusion 1: business-human and operator-human are not the same state

The current system needs at least a semantic split between:

- **Business / semantic escalation**
- **Operational / recovery escalation**

Even if the underlying enum is not changed immediately, the design must treat them as different classes.

### Conclusion 2: the attach boundary is underspecified

The largest current design hole is not clarify or plan. It is the boundary between:

- `H4_ATTACH`
- `H5_EXECUTE`

The system needs a stricter contract for what counts as the **first valid execution checkpoint**.

### Conclusion 3: a delivery timeout is not a business wait

Today a delivery timeout becomes:
- `PAUSED_FOR_HUMAN`

but semantically it is:
- `SUPERVISOR_RECOVERY_NEEDED`

That distinction should become explicit in future design work.

### Conclusion 4: the current prompts are sufficient to cause the incident

This was not random model behavior.

The combination of:

- immediate attach after approval
- broad "meaningful progress" wording
- permissive first checkpoint acceptance
- default `CONTINUE`
- generic human pause on timeout

is enough to naturally generate the exact Phase 17 incident we observed.

---

## Review Checklist For The Next Iteration

Any follow-up design or implementation should be reviewed against these questions:

1. Does the model distinguish human business input from operator recovery?
2. Is there an explicit attach-to-execution boundary?
3. Can the first checkpoint after attach be rejected as "not current-node execution progress"?
4. Does delivery timeout still funnel into the same human state as real business blockers?
5. Can the supervisor absorb more recovery work before involving a human?
6. If a human is needed, is the system explicit about **what kind** of human action is needed?

---

## Recommended Next Step

Do **not** jump straight to code fixes from the current incident.

First review and freeze:

1. attach/execution boundary semantics
2. first-checkpoint contract
3. human-intervention taxonomy
4. whether operational recovery should stay in the worker/session path or move to a more explicit guard/watchdog layer

Only after those are frozen should we decide how to change:

- `TopState`
- `DeliveryState`
- checkpoint prompts
- escalation prompts
- pause summary UX

