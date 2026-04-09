# Changelog

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
