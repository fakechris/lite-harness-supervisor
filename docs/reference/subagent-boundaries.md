# Sub-Agent Boundaries

This document freezes how sub-agents may participate around a
`thin-supervisor` run.

The goal is simple:

> sub-agents may help with read-only investigation and non-authoritative
> summaries, but the main supervised worker remains the only authority for
> current-node execution, checkpoints, structured semantics, and run-state
> mutation.

## Why This Boundary Exists

`thin-supervisor` is intentionally **not** a full sub-agent orchestration
platform. The runtime assumes there is one authoritative worker for the
active run.

Without a hard boundary, sub-agents can accidentally:

- pollute the active execution context
- race the main worker on the same code path
- emit conflicting semantics
- mutate run state outside the main control loop

## Allowed Uses

Sub-agents are allowed for:

- read-only repo exploration
- prior-art lookup
- CLI/help discovery
- parallel hypothesis investigation
- plan critique and reviewer-style summaries
- eval corpus inspection
- non-authoritative synthesis or recommendation drafting

The main worker may consume:

- summaries
- findings
- proposed approaches
- draft recommendations

## Forbidden Uses During an Active Supervised Run

Do not delegate these to a sub-agent:

- implementation of the active `current_node`
- authoritative writes to the active supervised worktree
- checkpoint emission
- declaration of authoritative structured semantics:
  - `progress_class`
  - `evidence_scope`
  - `escalation_class`
  - `requires_authorization`
  - `blocking_inputs`
  - `reason_code`
- control-plane mutation:
  - `spec approve`
  - `run register`
  - `run resume`
  - `run review`
  - `run stop`
- any action that directly advances verification, step completion, or workflow completion

## Phase-by-Phase Guidance

| Phase | Sub-agent use |
| --- | --- |
| `Research / Clarify` | allowed for read-only exploration and synthesis |
| `Plan` | allowed for critique, comparison, and plan-review assistance |
| `Approve` | not allowed to take over the attach or approval boundary |
| `Implement / Execute` | only allowed for read-only side investigations or non-authoritative summaries |

## Operational Rule

When a supervised run is active:

> The main worker is the only writer of current-node code, checkpoints,
> structured semantics, and run-state mutations.

Sub-agents may assist only when a summary of their work is sufficient and
their intermediate reasoning is not needed in the authoritative execution
thread.
