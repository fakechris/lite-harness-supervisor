# Changelog

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
