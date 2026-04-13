# Supervisor State Machine Review

Date: 2026-04-13

## Summary

The recent runtime incidents were not isolated tmux quirks. They exposed that the
supervisor's core loop is implemented as a state machine, but it is not yet tested
as a state machine.

Two concrete failures surfaced:

1. Long prompt injection could remain visible in the Codex composer while the
   terminal adapter treated submission as successful.
2. A `working` checkpoint in the current node could produce a `CONTINUE`
   decision without any follow-up injection, leaving the worker with no renewed
   guidance and making the run look stalled.

Both are state-transition bugs, not just UX bugs.

## Current Machine

The effective control flow lives in [supervisor/loop.py](../../supervisor/loop.py):

- `READY -> RUNNING` via initial injection
- `RUNNING + checkpoint -> GATING`
- `GATING + step_done/workflow_done -> VERIFYING`
- `GATING + blocked -> PAUSED_FOR_HUMAN`
- `GATING + working/soft confirmation -> RUNNING`
- `VERIFYING + ok -> RUNNING | COMPLETED`
- `VERIFYING + failed -> RUNNING | PAUSED_FOR_HUMAN`

The enum definitions in [supervisor/domain/enums.py](../../supervisor/domain/enums.py)
also include `AWAITING_AGENT_EVENT` and `ADVANCE_STEP`, but the main loop does
not meaningfully exercise them. That is a sign that the declared machine and the
executed machine have drifted.

## Findings

### 1. Continue transition lacked delivery semantics

`CONTINUE` only set `top_state = RUNNING`. The loop only reinjected when:

- the node changed, or
- a retry advanced the attempt counter

That meant `working -> CONTINUE` in the same node silently skipped injection.
The runtime symptom was "checkpoint appeared, then nothing came back".

### 2. Injection confirmation was too literal

The terminal adapter previously required the full injected text to remain
visible near the tail before it considered the submit path stuck. Codex/TUI
often wraps or truncates long prompts, so the adapter could accept a
half-submitted prompt as successful.

### 3. Transition tests were scenario-heavy, not table-driven

The repository already had useful scenario tests, but they mostly covered
golden-path flows. They did not explicitly assert critical transition
invariants such as:

- `CONTINUE` in the same node must be deliverable
- `VERIFY_STEP` success must emit `step_verified`
- final-node verification success must emit `run_completed`
- verification exhaustion must end in `PAUSED_FOR_HUMAN`

## Changes Landed In This Review

- Added explicit transition tests in
  [tests/test_state_machine_transitions.py](../../tests/test_state_machine_transitions.py)
- Strengthened the sidecar loop test to assert a persisted `continue`
  injection event in
  [tests/test_sidecar_loop.py](../../tests/test_sidecar_loop.py)
- Tightened prompt-stuck detection in
  [supervisor/terminal/adapter.py](../../supervisor/terminal/adapter.py)
- Fixed `CONTINUE` reinjection in
  [supervisor/loop.py](../../supervisor/loop.py)

## Remaining Gaps

These are still worth follow-up work:

1. `daemon` restart and recovery semantics are not modeled as part of the same
   state machine. Persisted `RUNNING` runs still need stronger takeover logic.
2. Enum cleanup: either wire `AWAITING_AGENT_EVENT` / `ADVANCE_STEP` into the
   real machine or remove them.
3. Transition coverage should expand to include daemon-managed resume and
   observation-only surfaces as first-class transition matrices, not just
   scenarios.

## Recommendation

Treat the supervisor loop as a finite-state machine and keep a transition
matrix alongside the implementation. For every new decision type or top state,
require:

- one transition-level unit test
- one persisted-session-log assertion
- one integration/scenario assertion if the transition crosses a control-plane
  boundary
