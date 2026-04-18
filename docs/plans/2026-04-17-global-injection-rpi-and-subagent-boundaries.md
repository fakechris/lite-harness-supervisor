# Global Injection, RPI Mapping, and Sub-Agent Boundary Audit

**Goal:** freeze the next set of prompt/harness boundary decisions before more ad hoc edits land. This audit focuses on four related questions:

1. what this repo currently injects globally into every session
2. whether the current four-stage workflow has an explicit `Research / Plan / Implement` mapping
3. whether sub-agent use is currently defined in supervised execution
4. what to audit next so context hygiene and execution ownership stop drifting

This document is a design and audit artifact. It does **not** implement the changes below yet.

---

## Why This Audit Exists

The current repo has already done meaningful work on:

- structured checkpoint semantics
- contradiction routing
- attach-boundary hardening
- global observability
- send-key readiness

But those improvements mostly live in the runtime and protocol layers.

The article-level guidance about harness quality highlighted three additional prompt/control-plane concerns that are still only partially specified here:

- global injection should stay minimal
- stage boundaries should map cleanly to a staff-engineer-style `Research -> Plan -> Implement` workflow
- sub-agents should have explicit boundaries, especially once a supervised worker is active

Without freezing those boundaries, the same kind of drift can happen again:

- protocol details creep back into always-loaded context
- planning and execution blend together
- sub-agents get used opportunistically without a control-plane contract

---

## Scope

This audit only covers **repo-owned prompt/control surfaces** and nearby workflow documentation.

Included:

- [AGENTS.md](/Users/chris/workspace/lite-harness-supervisor/AGENTS.md)
- [skills/thin-supervisor/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md)
- [packaging/thin-supervisor-codex/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/packaging/thin-supervisor-codex/SKILL.md)
- [docs/getting-started.md](/Users/chris/workspace/lite-harness-supervisor/docs/getting-started.md)
- [docs/ARCHITECTURE.md](/Users/chris/workspace/lite-harness-supervisor/docs/ARCHITECTURE.md)
- [docs/plans/2026-04-17-fat-skill-thin-harness-rule-repartitioning.md](/Users/chris/workspace/lite-harness-supervisor/docs/plans/2026-04-17-fat-skill-thin-harness-rule-repartitioning.md)

Explicitly out of scope:

- provider-native system prompts outside this repo
- external MCP server prompt surfaces
- general multi-agent platform design

---

## A. Global Injection Audit

### Current repo-owned always-loaded surfaces

| Surface | Load mode | Current payload | Assessment |
| --- | --- | --- | --- |
| [AGENTS.md](/Users/chris/workspace/lite-harness-supervisor/AGENTS.md) | always loaded when working in this repo | repo-specific attach entrypoint, active-run check, full checkpoint template, status table, acceptance notes, execution rules | **Too heavy for the always-loaded layer.** Short, but still carries full worker protocol details even when no supervised run is active. |
| `skills/thin-supervisor` skill name + description | loaded by skill discovery only | `"Drive long-running multi-step tasks... Clarify -> Plan -> Approve -> Execute"` | Appropriate for progressive disclosure. |
| `packaging/thin-supervisor-codex` skill name + description | loaded by skill discovery only | Codex-packaged equivalent of the same skill | Appropriate for progressive disclosure, but duplicated surfaces increase drift risk. |

### Current repo-owned on-demand surfaces

These are **not** globally injected. They already follow progressive disclosure better than `AGENTS.md`.

| Surface | Load mode | Role |
| --- | --- | --- |
| [skills/thin-supervisor/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md) | on-demand when skill is invoked | full four-stage workflow |
| `skills/thin-supervisor/references/*` | on-demand via `SKILL.md` | contract, escalation, spec writing, debugging, supervision modes |
| `skills/thin-supervisor/strategy/*` | on-demand via `SKILL.md` | approval boundary, finish proof, escalation, pause UX |

### Main finding

The repo does **not** have a broad global-bloat problem. It has a **specific** one:

> `AGENTS.md` is currently carrying more worker protocol detail than belongs in the always-loaded layer.

### Proposed change

Convert `AGENTS.md` from a protocol sheet into a **minimal router**.

Keep in `AGENTS.md`:

- repo identity
- how to detect whether a supervised run is active
- the preferred attach entrypoint
- the rule that implementation must not start before attach succeeds
- the instruction that, if an active run exists, the worker protocol must be loaded from a dedicated reference file

Move out of `AGENTS.md` into a dedicated reference:

- full checkpoint block
- status table
- acceptance/verification semantics
- worker execution rules

### Proposed new file

- `skills/thin-supervisor/references/worker-checkpoint-protocol.md`

Rationale:

- it stays close to the skill contract
- it is only loaded when a worker is actually executing
- it avoids duplicating protocol text between repo-global injection and skill references

### Proposed minimal `AGENTS.md` shape

```markdown
# thin-supervisor repo

This repository implements the thin-supervisor runtime, skills, and operator tooling.

## Before supervised work

- Check run state with `thin-supervisor status`
- Prefer `scripts/thin-supervisor-attach.sh <slug>` when starting a new supervised task
- Do not begin implementation before attach succeeds

## If a supervised run is active

If `thin-supervisor status` shows an active run for this pane/project:

- load `skills/thin-supervisor/references/worker-checkpoint-protocol.md`
- follow the checkpoint protocol exactly
- do not skip verification or invent your own control flow
```

### Additional drift risk to clean up

There are two separate copies of the thin-supervisor skill body:

- [skills/thin-supervisor/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md)
- [packaging/thin-supervisor-codex/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/packaging/thin-supervisor-codex/SKILL.md)

That duplication is not a global-injection problem, but it **is** a context-drift and maintenance risk. Any AGENTS slimming change should be paired with a follow-up audit that ensures both skill bodies continue to say the same thing.

---

## B. RPI Mapping Audit

### Current state

The repo has a formal four-stage workflow:

- `Clarify`
- `Plan`
- `Approve`
- `Execute`

See:

- [skills/thin-supervisor/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md:15)
- [docs/getting-started.md](/Users/chris/workspace/lite-harness-supervisor/docs/getting-started.md:34)

But the repo does **not** currently define an explicit mapping to:

- `Research`
- `Plan`
- `Implement`

That matters because the missing mapping is exactly where the system previously leaked:

- planning/admin artifacts crossed into execution
- attach and execution were not cleanly separated
- the worker's first checkpoint could report prior-phase work

### Proposed explicit mapping

| RPI phase | thin-supervisor phase | What it means |
| --- | --- | --- |
| `Research` | `Clarify` + codebase exploration + contract confirmation | understand problem, discover repo facts, narrow ambiguity, define success |
| `Plan` | `Plan + Self-Review` | write spec, validate it, stress-test it before execution |
| `Approve` | explicit extra gate between `Plan` and `Implement` | human approval and attach boundary; not part of standard RPI, but required here because execution must not begin until the control plane is attached |
| `Implement` | `Execute` | do current-node work, verify it, emit checkpoints |

### Frozen behavioral meaning

#### `Research`

Allowed:

- codebase exploration
- prior-art lookup
- CLI/help discovery
- contract confirmation

Not allowed:

- implementation edits for the supervised task
- attach/register/resume
- execution checkpoints

#### `Plan`

Allowed:

- write spec
- write plan review
- architect/critic passes

Not allowed:

- implementation code for current supervised task
- verifier-driven step advancement

#### `Approve`

Allowed:

- human approval
- bootstrap / approve / register
- explicit attach-boundary transition

Not allowed:

- "just start coding while we are here"
- baseline-only checkpoints posing as execution progress

#### `Implement`

Allowed:

- current-node implementation
- verifier execution
- structured checkpoints

Not allowed by default:

- broad new research branches in the same execution window
- speculative side investigations that are large enough to pollute execution context

### Proposed doc changes

1. Update [skills/thin-supervisor/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md)
   - add a short section near the top:
     - `Clarify maps to Research`
     - `Plan maps to Plan`
     - `Approve is the human/attach gate between Plan and Implement`
     - `Execute maps to Implement`
2. Update [docs/getting-started.md](/Users/chris/workspace/lite-harness-supervisor/docs/getting-started.md)
   - add a short "RPI mental model" section
3. Update [docs/ARCHITECTURE.md](/Users/chris/workspace/lite-harness-supervisor/docs/ARCHITECTURE.md)
   - add one section that explicitly distinguishes:
     - workflow phases
     - runtime `TopState`

---

## C. Sub-Agent Boundary Audit

### Current state

The repo does **not** currently define a formal sub-agent execution policy.

In fact, the current documentation leans the other way:

- [docs/plans/2026-04-12-supervisor-oracle-capability-plan.md](/Users/chris/workspace/lite-harness-supervisor/docs/plans/2026-04-12-supervisor-oracle-capability-plan.md:130)
- [docs/reviews/2026-04-12-amp-supervisor-capability-review.md](/Users/chris/workspace/lite-harness-supervisor/docs/reviews/2026-04-12-amp-supervisor-capability-review.md:68)

Both explicitly say that building a full sub-agent platform into `thin-supervisor` is out of scope.

That still leaves an unresolved practical question:

> when, if ever, is it acceptable for a supervised worker session to delegate work to a sub-agent?

Right now that boundary is not frozen.

### Proposed policy

#### Allowed sub-agent use

Only allow sub-agents for **read-only** or **non-authoritative** work, especially during `Research` and `Plan`:

- read-only repo exploration
- parallel hypothesis investigation
- prior-art / search fan-out
- plan critique / reviewer summaries
- eval corpus inspection
- non-authoritative synthesis, summaries, or draft proposals

The main worker may consume:

- summaries
- findings
- candidate approaches
- draft recommendations

The sub-agent must **not** own run-state transitions or semantic authority.

#### Forbidden sub-agent use while an active supervised run exists

Do **not** delegate these:

- implementing the active `current_node`
- writing to the active supervised worktree as the authoritative executor
- emitting checkpoints
- declaring authoritative structured semantics:
  - `progress_class`
  - `evidence_scope`
  - `escalation_class`
  - `requires_authorization`
  - `blocking_inputs`
  - `reason_code`
- running control-plane mutations:
  - `spec approve`
  - `run register`
  - `run resume`
  - `run review`
  - `run stop`
- taking actions that directly advance verifier / step / completion state

### Operational rule

When a supervised run is active:

> **Sub-agents may assist with read-only investigation or non-authoritative summaries, but the main worker remains the only writer of current-node code, checkpoints, structured semantics, and run-state mutations.**

### Proposed new reference doc

- `docs/reference/subagent-boundaries.md`

Suggested content:

- allowed sub-agent patterns
- forbidden sub-agent patterns
- phase-by-phase matrix
- explicit note that this is a control-plane boundary, not a capability judgment

---

## D. Audit Workstreams

The repo should not jump directly from this discussion into runtime edits. The next audits should be run in order.

### 1. Global injection audit

Output:

- one table of always-loaded repo-owned surfaces
- one slimming proposal per surface
- a concrete `AGENTS.md` replacement draft

### 2. RPI mapping audit

Output:

- one phase mapping table
- one "allowed vs forbidden" table per phase
- one list of docs that must state the same mapping

### 3. Sub-agent boundary audit

Output:

- one allowed-use matrix
- one forbidden-use matrix
- one active-run hard-boundary rule set

### 4. Harness semantic-rule audit

This connects directly to:

- [2026-04-17-fat-skill-thin-harness-rule-repartitioning.md](/Users/chris/workspace/lite-harness-supervisor/docs/plans/2026-04-17-fat-skill-thin-harness-rule-repartitioning.md)

Output:

- rule inventory
- `mechanism` vs `semantics`
- keep / migrate to skill / move to structured protocol / delete fallback

---

## Concrete File Change Proposal

If the repo accepts this audit, the next implementation slice should touch:

- [AGENTS.md](/Users/chris/workspace/lite-harness-supervisor/AGENTS.md)
  - slim to minimal router
- `skills/thin-supervisor/references/worker-checkpoint-protocol.md`
  - new file
- [skills/thin-supervisor/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/SKILL.md)
  - add explicit RPI mapping
- [packaging/thin-supervisor-codex/SKILL.md](/Users/chris/workspace/lite-harness-supervisor/packaging/thin-supervisor-codex/SKILL.md)
  - mirror the same RPI mapping
- [docs/getting-started.md](/Users/chris/workspace/lite-harness-supervisor/docs/getting-started.md)
  - add RPI mental model and link to worker protocol reference
- [docs/ARCHITECTURE.md](/Users/chris/workspace/lite-harness-supervisor/docs/ARCHITECTURE.md)
  - add workflow-phase vs runtime-state distinction
- `docs/reference/subagent-boundaries.md`
  - new file

---

## Recommended Order

1. Freeze this audit
2. Slim `AGENTS.md`
3. Add worker protocol reference file
4. Add explicit RPI mapping to skill + docs
5. Freeze sub-agent boundaries
6. Then resume the rule inventory / protocol migration work

---

## Immediate Next Step

The next concrete step should be:

> **Implement the `AGENTS.md` slimming slice first, because it is the only true always-loaded repo-owned injection surface today.**

That gives the best context-hygiene payoff for the least behavioral risk.
