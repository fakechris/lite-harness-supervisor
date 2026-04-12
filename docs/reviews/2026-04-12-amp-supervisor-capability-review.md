# Amp Supervisor Comparison Review

Date: 2026-04-12

## Summary

Amp's decomposition is useful because it separates three different jobs that are easy to blur together:

1. `oracle` — get an independent advisory opinion
2. `supervisor` — decide what to execute next and orchestrate the work
3. `acceptance gate` — independently verify that the work is actually done

thin-supervisor was already strongest in layer 3 and reasonably mature in the execution-control parts of layer 2, but it had no explicit layer-1 advisory object. This made capability discussions fuzzy: collaboration notes existed, supervision policy existed, and reviewer gating existed, but there was no first-class "second opinion" surface analogous to Amp's oracle.

This review treats Amp as a reference decomposition, not a product blueprint. The goal is not to clone Amp's platform-level agent runtime. The goal is to make thin-supervisor's own contracts cleaner.

## Capability Matrix

| Capability | Amp framing | thin-supervisor before | thin-supervisor after this pass |
|---|---|---|---|
| Independent second opinion | `oracle` | Missing as a first-class capability | `thin-supervisor oracle consult` + `OracleOpinion` |
| Worker orchestration | built-in supervisor | Implemented as sidecar loop + daemon + policy engine | unchanged |
| Deterministic acceptance | acceptance testing loop | Implemented strongly via verifier suite + finish gate | unchanged |
| Human review gating | reviewer / escalation | Implemented via `must_review_by` + `run review` | unchanged |
| Collaboration memory | notes / handoff | Implemented via `note add/list`, `observe`, `list` | now can persist oracle outputs too |
| Sub-agent platform | `Task` / task dispatch | intentionally absent in product runtime | still absent |
| Thread handoff runtime | `handoff` | intentionally absent in product runtime | still absent |

## Current Assessment

### What thin-supervisor already does well

- **Acceptance-first discipline** is the strongest part of the system. `FinishGate`, verifier commands, explicit state transitions, and persisted session logs are already better defined than a typical "agent supervisor" story.
- **Execution surfaces are decoupled from policy**. tmux, open-relay, and transcript-backed JSONL observation all fit the same surface contract.
- **Supervisor intensity is capability-aware**. `strict_verifier`, `collaborative_reviewer`, and `directive_lead` are a good start toward "thin supervisor, strong worker."

### What was missing

- There was **no explicit advisory plane**. A user could manually ask another model, or stuff thoughts into notes, but the product had no object or command representing "external independent advice."
- This made it harder to discuss or audit questions like:
  - Was this recommendation external or internal?
  - Which files were shown to the advisor?
  - Was the advisory result persisted anywhere?
  - Did the supervisor treat advisory input as binding, or just as context?

## Enhancement Added

This pass adds a lightweight oracle layer without turning thin-supervisor into a multi-agent runtime:

- **New first-class object**: `OracleOpinion`
- **New CLI**: `thin-supervisor oracle consult`
- **Provider model**:
  - external provider when `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, or `ANTHROPIC_API_KEY` is present
  - deterministic self-adversarial fallback when no external provider is configured
- **Run-aware persistence**: `--run <run_id>` stores the oracle output into the shared notes plane as note type `oracle`

That gives thin-supervisor a real layer-1 "consult" capability while preserving the existing contract:

- advisory only
- read-only
- supervisor still decides
- acceptance gate still proves correctness

## Deliberate Non-Goals

These are tempting, but they are not good next steps for thin-supervisor right now:

- **Not** building a full sub-agent orchestration platform into thin-supervisor itself
- **Not** adding autonomous file-reading or repo-crawling behavior inside the oracle layer
- **Not** letting oracle output bypass verification or mutate run state directly
- **Not** turning consultation into a hidden control channel

Those would push the project away from "thin supervisor" and toward a full agent platform.

## Recommended Next Enhancements

### 1. Oracle-backed review templates

Add structured modes on top of `oracle consult`:

- `--mode review`
- `--mode plan`
- `--mode debug`

These already exist as prompt variants at the CLI surface; the next step is to make the output format more structured so downstream automation can reason about it.

### 2. Advisory-to-routing bridge

Allow a future `RoutingDecision` to reference an `OracleOpinion` ID when escalation was informed by a consultation. That preserves causality without making the oracle authoritative.

### 3. Spec-level advisory hooks

Allow a spec to request optional oracle consultation before risky nodes or before final acceptance on critical-risk workflows. This should remain opt-in and should never weaken deterministic verification.

## Bottom Line

Amp's decomposition is directionally right: advisory reasoning, execution control, and acceptance proof are different layers.

thin-supervisor was already credible on execution control and acceptance proof. After this pass, it also has a real, auditable advisory layer. That makes the architecture cleaner without violating the project's core constraint: the supervisor stays thin, and correctness still comes from verification, not rhetoric.
