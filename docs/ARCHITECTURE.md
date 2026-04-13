# Architecture

> **thin-supervisor** is an acceptance-centered, capability-aware run supervisor built around native Codex / Claude workflows. It observes, judges, advances, corrects, and accepts — without replacing the primary coding agent.

## Design Principles

1. **Takeover layer, not replacement layer.** Users stay in Codex / Claude. Supervisor intervenes only in supervised mode.
2. **Session-first.** The system's truth lives in `SessionRun`, not in any process or pane.
3. **Acceptance-first.** `WorkflowSpec` defines how to do it. `AcceptanceContract` defines what counts as done. `WorkflowSpec.approval` defines whether the plan is still draft or cleared to execute.
4. **Surface is a hand.** tmux, open-relay, transcript-backed JSONL observation, and future browsers are all just `ExecutionSurface` implementations. None is the system itself.
5. **Capability-aware.** Supervision intensity adapts to worker strength, risk, and failure history. A thin supervisor does NOT micromanage a strong worker.
6. **Collaboration supplements, doesn't replace correctness.** Notes, observation, and cross-run coordination enhance the system but never substitute for checkpoint + verifier + acceptance.

---

## Six-Layer Architecture

### Layer 1: Worker & Execution Surface

What the system talks through. Who it talks to.

| Object | Status | Purpose |
|--------|--------|---------|
| **ExecutionSurface** | ✅ Implemented | SessionAdapter protocol + tmux + open-relay adapters + JSONL observation surface |
| **WorkerProfile** | ⚠️ Maturing | Dataclass exists, config wired, consumed by policy engine |
| **OracleOpinion** | ✅ Implemented | Read-only advisory consultation result from an external or fallback oracle |

### Layer 2: Observation & Event Normalization

Converting raw terminal/transcript output into structured system events.

| Object | Status | Purpose |
|--------|--------|---------|
| **CheckpointEvent** | ✅ Implemented | Typed dataclass with identity (run_id, seq, surface_id, checkpoint_id) |

Terminal remains the primary interactive observation source, but transcript-backed JSONL observation is now implemented for Codex / Claude sessions that expose native transcript files. Broader multi-source observation (provider hooks, relay events) is still designed but not implemented — see `docs/design/p3-observation-sources.md`.

### Layer 3: SessionRun & Recovery

A run is a durable, addressable, auditable object.

| Object | Status | Purpose |
|--------|--------|---------|
| **SessionRun** | ⚠️ Maturing | Wrapper exists with state + acceptance + worker + policy properties. Not yet the single entry point for all run operations. |

Material: `state.json` snapshot, `session_log.jsonl` durable history, per-run runtime dirs, completed review acknowledgements, and spec hash / surface / workspace resume validation.
Historical analysis now has a first-class read path through `supervisor.history`: stable export, derived summary, decision replay, and markdown postmortem generation.

### Layer 4: Acceptance & Verification

Defining "truly done" and proving it.

| Object | Status | Purpose |
|--------|--------|---------|
| **AcceptanceContract** | ⚠️ Maturing | Dataclass with goal, required_evidence, forbidden_states, risk_class, must_review_by. FinishGate consumes it, and `run review --by ...` satisfies reviewer-gated completion. Loader parses optional `acceptance:` YAML section. |

Supporting services: `VerifierSuite` (command / artifact / git / workflow), `FinishGate`.

### Layer 5: Capability-Aware Supervision Policy

How strongly should the supervisor intervene?

| Object | Status | Purpose |
|--------|--------|---------|
| **SupervisionPolicy** | ⚠️ Maturing | 3 modes implemented. PolicyEngine selects based on worker trust, contract risk, failure count. Composer adapts instruction style. |

| Mode | When | Behavior |
|------|------|----------|
| `strict_verifier` | Strong worker, standard risk (DEFAULT) | Only check evidence, run verifiers |
| `collaborative_reviewer` | Low trust or high risk | Ask for approach + risks first |
| `directive_lead` | Failures ≥ threshold or critical risk | One action at a time |

**Key rule**: GPT-5.4/Opus worker + MiniMax supervisor → `strict_verifier`. The system does NOT let a thin supervisor micromanage a strong worker.

### Layer 6: Coordination & Escalation

Routing when supervisor can't resolve alone.

| Object | Status | Purpose |
|--------|--------|---------|
| **RoutingDecision** | ⚠️ Maturing | Created on ESCALATE_TO_HUMAN, logged to session_log, and can carry an advisory `consultation_id` back to a prior oracle note. Stronger reviewer and worker switching are planned but not implemented. |

Escalation paths:
- **Human** — high risk, multi-failure, evidence gaps
- **Stronger Reviewer** — reviewer-gated finish acknowledgement is implemented; broader bounded-review routing remains planned
- **Alternate Executor** — planned (worker switching)

Collaboration plane (`list`, `observe`, `note`) is implemented as CLI + daemon IPC.
Advisory consultation is now available through `thin-supervisor oracle consult`; its outputs are intentionally non-authoritative and can be persisted into the collaboration plane as structured oracle notes. When a later escalation is informed by that note, the routing event can reference the consultation ID for auditability.
Human-pause visibility now has a dedicated notification layer: the loop derives a stable pause summary (`pause_reason` + `next_action`), records a `human_pause` session event, and dispatches it through pluggable notification channels. Built-in channels today are `tmux_display` for pane-local alerts and `jsonl` for durable notification records; future Feishu/Telegram adapters plug into the same `NotificationChannel` interface in `supervisor/notifications.py`.
Testing-oriented pause handling now sits above that layer: `pause_handling_mode=notify_then_ai` means “notify first, then let the agent attempt a bounded automatic recovery.” The current heuristic auto-intervention engine handles blocked checkpoints, repeated node mismatch, and retry-budget exhaustion, while reviewer-gated pauses remain human-owned.

---

## First-Class Objects

### Stable (code-proven, test-covered)

| Object | File | Key Fields |
|--------|------|------------|
| WorkflowSpec | `domain/models.py` | kind, id, goal, steps/nodes, finish_policy, acceptance, approval |
| Checkpoint | `domain/models.py` | status, current_node, summary, run_id, checkpoint_seq, checkpoint_id, surface_id |
| SupervisorDecision | `domain/models.py` | decision_id, decision, reason, confidence, gate_type, triggered_by_seq |
| HandoffInstruction | `domain/models.py` | instruction_id, content, node_id, triggered_by_decision_id, trigger_type |

### Maturing (dataclass exists, integrated, but user-facing coverage light)

| Object | File | Key Fields |
|--------|------|------------|
| AcceptanceContract | `domain/models.py` | goal, required_evidence, forbidden_states, risk_class, must_review_by |
| WorkerProfile | `domain/models.py` | worker_id, provider, model_name, role, trust_level |
| SupervisionPolicy | `domain/models.py` | mode, reason, risk_class, failure_threshold |
| RoutingDecision | `domain/models.py` | routing_id, target_type, scope, reason, triggered_by_decision_id, consultation_id |
| OracleOpinion | `domain/models.py` | provider, model_name, mode, question, files, response_text, source |
| SessionRun | `domain/session.py` | state + acceptance_contract + worker_profile + supervision_policy + routing_history |
| ExecutionSurface | `adapters/session_adapter.py` | read, inject, current_cwd, session_id, doctor |
| RunHistory Export | `history.py` | schema_version, state, decision_log, session_log, notes |

### Planned (design docs exist, not implemented)

| Object | Design Doc |
|--------|------------|
| ObservationSource | `docs/design/p3-observation-sources.md` |
| PortalSurface | `docs/design/p2-external-surfaces.md` |

---

## Causality Chain

Every instruction traces back through decision to the checkpoint that triggered it:

```
Checkpoint(seq=N) → SupervisorDecision(triggered_by_seq=N) → HandoffInstruction(triggered_by_decision_id=X)
```

Run events are appended to `session_log.jsonl` with run_id and sequence numbers for durable audit. Project-level bootstrap and scaffold repairs are appended to `.supervisor/runtime/ops_log.jsonl` so pre-run operational incidents are preserved too. Historical analysis reads those append-only artifacts and produces:
- stable JSON exports
- derived run summaries
- gate-decision replay without live injection
- markdown postmortems under `.supervisor/reports/`

The first learning substrate now also lives beside those artifacts:
- `.supervisor/runtime/shared/friction_events.jsonl` for append-only UX/behavior failures such as repeated confirmation or pause confusion
- `.supervisor/runtime/shared/user_preferences.json` for durable per-user preference memory such as approval style or clarify tolerance

These files are intentionally advisory. They are input to hindsight, replay, and future skill/policy tuning. They are not a second source of truth for run state.

---

## V1 / V2 Boundary

### V1 (current)

- Native Codex / Claude UX preserved
- Terminal plus transcript-backed JSONL observation
- AcceptanceContract, WorkerProfile, SupervisionPolicy, RoutingDecision dataclasses
- SupervisionPolicyEngine with 3 modes
- FinishGate consuming AcceptanceContract
- tmux + open-relay + JSONL surfaces
- Single daemon, multi-run
- Collaboration plane: list / observe / note
- Advisory oracle consultation plane (`thin-supervisor oracle consult`)
- Reviewer-gated completion with explicit human / stronger reviewer acknowledgement

### V2 (planned)

- Provider-adjacent hook observation source (not main control plane)
- Stronger reviewer auto-routing
- Limited worker switching
- Browser/portal surface design validation
- Richer skill/policy eval loop with replay traces, blind comparator runs, and candidate promotion gates before changing shipped behavior

### Transitional Eval Layer (now)

- `friction_event` and `user_preference_memory` act as the learning substrate
- `run export`, `run summarize`, `run replay`, and `run postmortem` provide offline evidence
- `thin-supervisor eval` now exposes deterministic golden-suite executors for `approval-core`, `routing-core`, `escalation-core`, and `finish-gate-core`, plus a replay wrapper that converts historical run replays into eval-style reports, a blind comparator for baseline-vs-candidate suite outcomes, a canary runner that aggregates replay plus friction over real runs into a promote/hold/rollback signal, deterministic synthetic expansion with provenance tags, a constrained proposal surface that combines failure-case summaries with advisory/self-review guidance without auto-promoting candidates, optional report persistence under `.supervisor/evals/reports/`, candidate-lineage manifests under `.supervisor/evals/candidates/`, a bounded `review-candidate` surface for manual promotion review, and a `gate-candidate` command that combines compare plus optional canary results into a promotion recommendation
- Global behavior changes remain offline and human-reviewed; online adaptation stays scoped to the current run or user preference memory

### Skill Optimization Boundary

- `skills/thin-supervisor*/references/contract.md` is the frozen execution contract
- `skills/thin-supervisor*/strategy/*.md` are the allowed optimization surfaces
- `SKILL.md` is now the coordinator that points agents at the right contract and strategy documents
- policy optimization work should mutate strategy fragments, not the full skill document

### Not planned

- Full provider-native orchestration
- Remote IDE
- Agent society
- Large-scale workflow runtime

---

## Risks & Anti-Patterns

1. **Anti-pattern: thin supervisor micromanages strong worker.** Fix: default to `strict_verifier`. Only escalate supervision mode on evidence (failures, high risk).

2. **Anti-pattern: terminal as sole truth.** Fix: multi-source observation with terminal as fallback. Provider-native events as future primary source.

3. **Anti-pattern: premature platform.** Fix: stay focused on "run supervisor". Only add collaboration and routing as minimal contracts.
