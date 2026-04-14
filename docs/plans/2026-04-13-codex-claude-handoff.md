# Codex / Claude Code Handoff

**Audience:** the next coding agent taking over `thin-supervisor`

**Date:** 2026-04-13

**Purpose:** transfer the current runtime/devtime state, the main architectural changes shipped over the last week, and the highest-value next steps.

---

## 1. Current Repository Status

The repository is no longer in the early "tmux sidecar prototype" phase. It now has:

- a daemon-managed runtime with explicit state transitions
- a split between runtime CLI and devtime/operator CLI
- a first-generation offline eval / replay / compare / canary / promotion loop
- a contract-vs-strategy split for the `thin-supervisor` skill
- multiple rounds of control-plane hardening around pause/resume/recovery/injection

Recent merged PRs that matter most:

- `#30` expanded supervision-policy eval coverage
- `#31` split the skill into frozen contract + strategy fragments
- `#32` added candidate promotion gates
- `#33` added promotion registry and lifecycle dossier tooling
- `#34` added rollout bookkeeping and fixed canary phase regressions
- `#35` split runtime CLI and devtime CLI
- `#36` completed the protocol / recovery / trust-boundary hardening wave
- `#37` fixed zero-poll busy-spin loops
- `#38` added `clarify-contract-core` evals and replay resume semantics
- `#39` fixed orphaned-run recovery semantics and completion observability

The latest merged PR is:

- PR `#39`
- merge commit: `26f2116ee610c7dea9e9e5b2002bc73493d6b55d`

As of the last verification run:

- full test suite: `383 passed in 35.80s`

---

## 2. What Was Shipped In The Last Week

### A. Runtime / Devtime separation

This is now a hard product boundary:

- `thin-supervisor` is the runtime CLI for real task execution
- `thin-supervisor-dev` is the devtime/operator CLI for eval, replay, canary, candidate review, and promotion

This was done so users do not have to understand policy-optimization plumbing in order to run supervised work.

Relevant files:

- [supervisor/app.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/app.py:1)
- [supervisor/dev_app.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/dev_app.py:1)

### B. Clarify -> Plan -> Approve -> Execute became the enforced default

The runtime no longer treats an under-specified request as permission to immediately run.

Key changes shipped earlier in the week:

- specs can require approval
- draft specs are blocked from execution
- `spec approve` exists
- explicit user approval phrases are treated as final approval

This closed the previous failure mode where the supervisor silently narrowed a request into a weaker spec and executed that weaker contract.

### C. Offline eval / evolution skeleton is in place

The repo now supports:

- `eval run`
- `eval replay`
- `eval compare`
- `eval expand`
- `eval propose`
- `eval review-candidate`
- `eval candidate-status`
- `eval gate-candidate`
- `eval canary`
- `eval promote-candidate`
- `eval promotion-history`

Bundled suites now include:

- `approval-core`
- `approval-adversarial`
- `routing-core`
- `escalation-core`
- `finish-gate-core`
- `pause-ux-core`
- `clarify-contract-core`

This is enough to run a first operator-side improvement loop, but not yet equivalent to a full DSPy/GEPA-style reflective optimizer.

Relevant files:

- [supervisor/eval](/Users/chris/workspace/lite-harness-supervisor/supervisor/eval)
- [supervisor/history.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/history.py:1)
- [supervisor/learning.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/learning.py:1)

### D. Skill contract vs strategy split is in place

The optimizer should not mutate the whole skill as a single string anymore.

The shipped split is:

- frozen contract:
  - `skills/thin-supervisor/references/contract.md`
  - `skills/thin-supervisor-codex/references/contract.md`
- optimizable strategy fragments:
  - `strategy/approval-boundary.md`
  - `strategy/finish-proof.md`
  - `strategy/escalation.md`
  - `strategy/pause-ux.md`

This is the repo’s current analogue to "prompt parameters" for future optimizer work.

### E. Control-plane hardening was the dominant theme

Several important bug classes were fixed:

- `working -> CONTINUE` on the same node now reinjects instructions correctly
- `FINISH` no longer bypasses `FinishGate`
- `VERIFYING` is no longer overwritten by new agent events
- seq-reset and mismatch-consume bugs were fixed
- checkpoint protocol now has a single schema source
- trust boundary hardening landed around judge output / evidence matching / checkpoint sanitization
- zero-poll sidecar loops no longer spin at 100% CPU

Relevant files:

- [supervisor/loop.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py:1)
- [supervisor/protocol/checkpoints.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/protocol/checkpoints.py:1)
- [supervisor/llm/judge_client.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/llm/judge_client.py:1)
- [supervisor/gates/finish_gate.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/gates/finish_gate.py:1)
- [supervisor/domain/state_machine.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/domain/state_machine.py:1)

### F. Recovery semantics are stricter now

The latest work in PR `#39` addressed a subtle but important recovery defect:

- daemon restart could leave persisted `RUNNING` state that looked live but had no active daemon worker
- these are now downgraded to explicit recoverable pause states
- foreground-owned local runs are not rewritten as daemon orphans
- local status fallback now renders daemon-owned orphaned runs as paused instead of healthy `RUNNING`

Completion observability also improved:

- `run_completed` and `human_pause` tmux notifications now stay visible longer

Relevant files:

- [supervisor/daemon/server.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/daemon/server.py:1)
- [supervisor/notifications.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/notifications.py:1)
- [supervisor/app.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/app.py:1)
- [supervisor/storage/state_store.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/storage/state_store.py:1)

---

## 3. Current External Validation State

The main real-world dogfood target during this week was:

- `~/workspace/dingreport`

Important runtime facts from that workspace:

- `run_b250f22f9169` is complete
- it finished at `step_5_browser_e2e_and_uat_handoff`
- the main confusion was not a real deadlock; it was poor completion observability in the tmux pane
- `run_fe0d2aa29d90` was an orphaned persisted run and is now intentionally surfaced as `PAUSED_FOR_HUMAN`

Implication:

- there is no currently active `dingreport` run to rescue
- if more runtime validation is needed, the next agent should start or resume deliberately, not assume `%0` still represents an active daemon-managed run

---

## 4. What Still Needs To Be Done

The repo is much stronger, but the work is not "finished forever". The highest-value remaining items are below.

### Priority 1: Replace heuristic injection confirmation with an acknowledged delivery path

Current state:

- injection confirmation is much better than before
- but it still relies on pane/UI heuristics
- this is still the weakest runtime surface

Why it matters:

- many of the most frustrating runtime incidents were caused by prompt text being in the pane without being truly consumed
- heuristic confirmation is good enough for now, but not the final architecture

Target direction:

- explicit delivery/ack model for tmux-like surfaces
- or a delivery adapter that can distinguish "typed", "submitted", and "agent started processing"

Related earlier issue cluster:

- previous multi-review item `U-22`

### Priority 2: Resolve the policy contradiction around `blocked`

Current state:

- escalation docs and runtime auto-intervention behavior are not perfectly aligned
- one part of the system implies blocked should escalate immediately
- another part still attempts auto-recovery in some blocked-like cases

Why it matters:

- this is a policy contradiction, not just a code nit
- it affects both runtime behavior and eval expectations

Related earlier issue cluster:

- previous multi-review item `U-18`

### Priority 3: Improve concurrency hygiene in state/session storage

Current state:

- `_session_seq` is still an in-memory counter without explicit concurrency protection
- practically this has not been the primary source of incidents, but it is still weak

Why it matters:

- future delivery/heartbeat work will likely make this more important

Related earlier issue cluster:

- previous multi-review item `U-20`

### Priority 4: Upgrade the devtime optimizer from constrained proposal to reflective candidate generation

Current state:

- `thin-supervisor-dev eval propose` exists
- candidate lineage, dossiers, canary, and promotion registry exist
- but candidate generation is still deliberately conservative and rule-driven

Why it matters:

- the repo now has enough eval / replay / canary substrate to support a more ambitious optimizer
- but that optimizer should operate only on strategy fragments, not the frozen contract layer

Target direction:

- keep current gate/promotion flow
- upgrade only the candidate-generation layer
- treat DSPy / GEPA as inspiration for reflective mutation and trace-informed proposal generation

### Priority 5: Make operator workflows easier to run

Current state:

- the underlying commands exist
- but the devtime/operator workflow is still somewhat scattered

Likely next UX step:

- add a thin high-level `thin-supervisor-dev` workflow wrapper
- keep it devtime-only
- do not pollute the runtime CLI again

---

## 5. Recommended Next Steps For The Next Agent

If a new Codex or Claude Code session takes over, the recommended order is:

1. Read:
   - [docs/plans/2026-04-12-supervision-policy-optimizer-roadmap.md](/Users/chris/workspace/lite-harness-supervisor/docs/plans/2026-04-12-supervision-policy-optimizer-roadmap.md:1)
   - [docs/plans/2026-04-12-skill-eval-and-evolution-system.md](/Users/chris/workspace/lite-harness-supervisor/docs/plans/2026-04-12-skill-eval-and-evolution-system.md:1)
   - [docs/reviews/2026-04-13-protocol-recovery-trust-hardening.md](/Users/chris/workspace/lite-harness-supervisor/docs/reviews/2026-04-13-protocol-recovery-trust-hardening.md:1)

2. Treat the next work item as runtime-hardening first, optimizer second.

3. Highest-value likely implementation:
   - explicit submission/delivery acknowledgement path for tmux-backed execution surfaces

4. After that:
   - resolve `blocked` policy semantics and align runtime + strategy docs + eval suites

5. Only after both are stable:
   - upgrade `thin-supervisor-dev eval propose` toward a trace-reflective candidate generator

---

## 6. How To Operate The System Today

### Runtime

Use `thin-supervisor` only.

Typical commands:

```bash
thin-supervisor daemon start
thin-supervisor status
thin-supervisor run register --spec <spec> --pane %0
thin-supervisor run resume --spec <spec> --pane %0 --surface tmux
thin-supervisor run summarize <run_id>
```

### Devtime / Operator

Use `thin-supervisor-dev` only.

Typical commands:

```bash
thin-supervisor-dev eval list
thin-supervisor-dev eval run --suite clarify-contract-core --json
thin-supervisor-dev eval replay --run-id <run_id> --json
thin-supervisor-dev eval compare --suite approval-core --candidate-policy <candidate> --json
thin-supervisor-dev eval propose --suite approval-core --objective reduce_false_approval --json
thin-supervisor-dev eval canary --run-id <run_id> --candidate-id <candidate_id> --phase shadow --json
thin-supervisor-dev eval gate-candidate --candidate-id <candidate_id> --run-id <run_id> --json
thin-supervisor-dev eval promote-candidate --candidate-id <candidate_id> --approved-by human --json
```

Operator rule:

- runtime users should not need to know the devtime commands
- devtime experiments should not leak back into runtime UX

---

## 7. Notes On DSPy / GEPA

Do **not** treat DSPy or GEPA as something to wire directly into the runtime.

The correct insertion point is:

- strategy-fragment candidate generation

Not:

- daemon runtime
- state machine
- control plane
- finish gate

The repo already has its own governance loop:

- generate candidate
- offline eval
- replay
- canary
- gate
- promote
- human merge

If DSPy / GEPA are introduced, they should be subordinate to that loop, not replace it.

---

## 8. Final Handoff Summary

The project made a large jump this week:

- runtime and devtime are cleanly separated
- the eval/promotion lifecycle exists end-to-end
- the skill is split into safe and optimizable layers
- major control-plane bugs were hardened
- orphaned-run recovery is now explicit instead of misleading

The system is now good enough to iterate systematically.

The next agent should focus on:

1. delivery acknowledgement / non-heuristic injection confirmation
2. blocked-policy alignment
3. then a stronger reflective optimizer on top of the current devtime gates
