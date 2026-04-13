# Changelog

## Unreleased

### CLI Boundaries

- Split runtime and operator workflows into separate entrypoints: `thin-supervisor` is now runtime-only, while `thin-supervisor-dev` owns oracle, learning, eval, canary, and promotion commands.
- Updated user-facing next actions and documentation so local policy tuning flows point to `thin-supervisor-dev ...` instead of leaking devtime commands through the runtime CLI.

### Advisory Consultation

- Added `thin-supervisor-dev oracle consult`, a lightweight second-opinion path that can call an external reasoning provider or fall back to a self-adversarial review scaffold.
- Introduced first-class `OracleOpinion` records so advisory consultations can be audited and persisted into the collaboration plane as shared notes.

### Observation & Session Binding

- `session jsonl` now prefers the active Codex / Claude session ID before falling back to the newest transcript, which makes JSONL observation bind to the right workspace more reliably.
- JSONL observation mode now keeps rolling transcript buffers aligned with checkpoint processing so multi-step runs do not silently stall or lose observation state.
- Open-relay session reads now treat the startup cwd as advisory and fall back to the persisted workspace root during verification, avoiding false passes and false failures after runtime `cd` changes.

### Finish Gate & Recovery

- Reviewer-gated acceptance is now satisfiable: runs can pause for `must_review_by` and resume completion after `thin-supervisor run review <run_id> --by human|stronger_reviewer`.
- Resume and review acknowledgement now reject spec drift instead of silently re-binding a run to a modified plan.
- Resume now updates paused state only after the pane lock is acquired, preventing zombie `RUNNING` states when recovery fails.

### Skills & Documentation

- Claude Code and Codex skills now load the reference docs conditionally instead of pulling extra context on every supervised task.
- Deep review findings for the 2026-04-11 stabilization pass are now recorded in `docs/reviews/2026-04-11-deep-code-review.md`.

## 0.2.0 (2026-04-10)

### Architecture

- Six-layer architecture with 10 first-class objects
- `docs/ARCHITECTURE.md` — canonical architecture document with implementation status matrix

### Object Model

- **AcceptanceContract**: defines "what counts as truly done" — required evidence, forbidden states, risk class, reviewer gating
- **WorkerProfile**: explicit worker capabilities (provider, model, trust level)
- **SupervisionPolicy**: 3 supervision modes (strict_verifier / collaborative_reviewer / directive_lead)
- **RoutingDecision**: escalation audit trail with causality links
- **SupervisionPolicyEngine**: rule-based mode selection from worker + contract + state

### Surface Abstraction

- SessionAdapter Protocol with `doctor()` method
- OpenRelaySurface adapter for oly sessions
- `surface_factory.create_surface()` runtime dispatch
- Config: `surface_type` ("tmux" | "open_relay")

### Daemon & Multi-Run

- Single daemon manages concurrent runs across tmux sessions
- Unix socket IPC: register, stop, list_runs, observe, note_add, note_list
- Per-run isolated state directories

### Collaboration Plane

- `thin-supervisor list` — cross-run visibility
- `thin-supervisor observe <run_id>` — read-only observation
- `thin-supervisor note add/list` — shared coordination memory

### Stability

- Injection confirmation (detect stuck input)
- Global pane ownership registry
- Graceful injection failure → PAUSED_FOR_HUMAN
- SIGTERM handler saves state before exit

### Naming

- Unified to `thin-supervisor` across repo, skills, scripts, PyPI

---

## 0.1.0 (2026-04-09)

Initial release.

### Core

- **6 first-class primitives**: WorkflowSpec, SessionRun, ExecutionSurface, CheckpointEvent, SupervisorDecision, HandoffInstruction
- **Causality chain**: every instruction traces back through decision to the checkpoint that triggered it
- **State machine**: 10 top-level states (INIT, READY, RUNNING, GATING, VERIFYING, PAUSED_FOR_HUMAN, COMPLETED, FAILED, ABORTED)
- **Spec loader**: `linear_plan` and `conditional_workflow` YAML specs
- **Continue gate**: regex rules first, LLM judge fallback (via LiteLLM)
- **Branch gate**: LLM-based option selection for decision nodes
- **Finish gate**: enforces `finish_policy` (all steps done, verification pass, git clean)
- **Verifier suite**: command, artifact, git, workflow — all with pane cwd support

### Infrastructure

- **Terminal adapter**: tmux pane read/inject with read-before-act guard, socket auto-detection, label-based pane addressing
- **Bridge CLI**: `thin-supervisor bridge read/type/keys/list/id/doctor`
- **Daemon mode**: `thin-supervisor run --daemon` with PID file and `thin-supervisor stop`
- **Session log**: append-only `session_log.jsonl` for durable history
- **Resume validation**: spec hash + pane target consistency check before resume
- **Config layering**: YAML file + env vars + defaults

### Skills

- Claude Code skill (`skills/lh-supervisor/`)
- Codex skill (`skills/lh-supervisor-codex/`)
- Stop hook preventing agent exit while supervisor is active

### Testing

- 74 tests covering all primitives, gates, verifiers, sidecar loop, resume, and causality chain
