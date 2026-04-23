# Changelog

## Unreleased

## 0.3.7 (2026-04-22)

### Operator-initiated clarification escalation

- Added `thin-supervisor clarify <run_id> <question> [--escalate] [--operator <name>]` — the first CLI entry point that runs a clarification and, on operator opt-in, records an escalation decision against the run's session log.
- Added a daemon IPC handler (`escalate_clarification`) and matching `DaemonClient.escalate_clarification` / `operator.actions.do_escalate_clarification` so every surface (CLI, TUI, IM) routes through the same canonical action and capability model. Works through the daemon when one is attached; falls back to writing directly to the run's session log for local/completed runs.
- Wired a TUI `E` keybind: when a clarification answer comes back below the escalation threshold, `format_clarification` surfaces an "escalate" hint and pressing `E` records the escalation against the selected run without leaving the channel.
- Added a `/escalate <run_id> [question]` IM command to `command_dispatch`. With no explicit question it pulls the most recent `clarification_response` from the run's session log; with an override it uses the operator's text verbatim.
- Emits a single `clarification_escalated_to_worker` timeline event — `transport="pending_0_3_8"` — so the audit trail makes clear that the actual side-instruction transport to the worker is deferred to 0.3.8 while the operator decision is already durable.

## 0.3.6 (2026-04-22)

### Operator Channel polish

- Added `deep_explainer_model` (+ `deep_explainer_temperature`, `deep_explainer_max_tokens`) so `assess_drift` can opt into a heavier reasoner while routine `explain_run` / `explain_exchange` / `request_clarification` keep using the cheaper `explainer_model`. Falls back to the routine model, then to the structured stub, when the deep model is unset or the call fails.
- Added frozen `DriftAssessment` and `ExchangeView` dataclasses (`supervisor.operator.models`) with `from_dict()` classmethods that accept both the legacy `last_*_summary` exchange shape and the post-explainer shape. Unknown drift statuses collapse to `watch`; non-numeric confidences collapse to `None`. Channel adapters can consume type-safe projections instead of stringifying raw dicts.
- Emitted a dedicated `explainer_answer` timeline event alongside the existing `clarification_response`, tagged with `source="explainer"` so future worker-side clarification flows can be distinguished without touching existing consumers.
- Added a confidence-gated escalation recommendation: when a clarification answer's confidence falls below `clarification_escalation_confidence` (default `0.4`), the result now carries `escalation_recommended=True` and a `clarification_escalation_recommended` event is written to the session log. Escalation is advisory — the operator still chooses whether to route the question to the worker.

## 0.3.5 (2026-04-21)

### JSONL injection via Stop hook

- Added `thin-supervisor hook stop` — the agent-side Stop-hook handler that reads a pending `.supervisor/runtime/instructions/<session_id>.json` handoff, writes a `.delivered.json` ACK atomically, and prints the content to stderr with exit 2 so Claude Code / Codex treat it as the next `reason`. Falls back to a generic "run is active" message when no instruction is pending but a supervisor run is still live.
- Added `thin-supervisor hook install` / `hook uninstall` to idempotently merge the Stop hook into `~/.claude/settings.json` and `~/.codex/hooks.json`. Existing user hooks are preserved; only the supervisor entry is added or removed.
- Upgraded `JsonlObserver` with `inject_with_id` (atomic JSON handoff file with instruction_id + content hash) and `poll_delivery` (ACK tail). Observation-only runs now wait for the Stop-hook ACK instead of immediately pausing — the delivery state transitions `INJECTED -> ACKNOWLEDGED` on ACK, `TIMED_OUT` after `OBSERVATION_HOOK_ACK_TIMEOUT_SEC` (10 min default) so stalled runs still surface to a human.

### A2A inbound adapter + boundary guard

- Added `supervisor/boundary/`: a transport-agnostic ingress safety layer with independently-toggleable auth (bearer + localhost fallback), sliding-window per-IP rate-limit, 8-pattern injection scan, JWT / GitHub / Slack / AWS / OpenAI-key redaction, and an append-only audit log that stores SHA-256 text hashes instead of raw bodies.
- Added `supervisor/adapters/a2a/`: a stdlib-only A2A (Google Agent-to-Agent) inbound adapter. `thin-supervisor a2a serve` hosts `.well-known/agent.json` plus JSON-RPC 2.0 `tasks/send` and `tasks/get`, routing through the boundary guard into `EventPlaneIngest`. Returned `task_id == request_id`, so A2A task identity is durable across adapter and daemon restarts.
- Extended the `system_events.jsonl` v1 allowlist with `a2a_started` / `a2a_stopped`, so `overview` now surfaces the listener host, port, and auth mode at a glance.

### Layered System Observability

- Added `thin-supervisor overview` (plus `--json` and `--watch`) so operators can see the whole system — daemons, live/orphaned/completed sessions, event-plane backlog, alerts, and a cross-run timeline — from any directory without tailing logs.
- Emitted `state_transition` as a first-class event on every real `top_state` change through a new `StateStore.transition_and_record()` wrapper, and added a frozen allowlist + shared `.supervisor/runtime/shared/system_events.jsonl` log for cross-run observability (daemon lifecycle, high-signal transitions, mailbox arrivals, wake decisions, wait expiries).
- Folded an event-plane summary (`waits_open`, `mailbox_new`, `mailbox_acknowledged`, `latest_mailbox_item_id`, `latest_wake_decision`) into both `RunSnapshot` and `SessionRecord`, so `status` now tags sessions with `mailbox:N` / `awaiting-review` and `observe` renders backlog + latest wake decision inline.
- Added a TUI global mode (`g` toggle) that renders the same `SystemSnapshot` projection as `overview` without leaving the run-centric view.

## 0.3.4 (2026-04-18)

### Session-First Event Plane

- Added the first shipped session-first deferred review substrate: durable `Session` identity, append-only external task/result storage, session waits, session mailbox items, and daemon-owned wake-decision bookkeeping.
- Added operator-facing CLI surfaces for async review request/result ingestion plus mailbox/wait inspection, and folded event-plane records into run history export for audit and replay.
- Shipped the first concrete review source adapter (`external_review`) and the base contract for future sources such as GitHub review/check ingestion.

### Post-Merge Hardening

- Addressed the first post-merge review round on the event-plane line with additional daemon, ingest, and session-identity coverage.
- Tightened event-plane validation and sequencing around request/result correlation, append-only mailbox state, and reaped-run session-log sequencing.
- Clarified the release line around the new deferred-review capability in docs and operator-facing status surfaces.

## 0.3.3 (2026-04-17)

### Structured Protocol

- Added a canonical checkpoint normalizer and frozen `reason_code` families (`esc.*`, `rec.*`, `ver.*`, `sem.*`) so runtime decisions can consume typed semantics instead of repeatedly re-parsing raw checkpoint prose.
- Extended the worker checkpoint wire format with v2 semantic fields for progress, evidence scope, escalation class, authorization, blocking inputs, and machine-readable reason codes.
- Tightened `reason_code` validation so only known frozen codes survive normalization; invented but regex-shaped tails are now rejected.

### Routing & Recovery

- Added contradiction routing by class: safety contradictions fail closed, business contradictions escalate, execution-semantic contradictions re-inject, and runtime-owned fields no longer silently override runtime truth.
- Preserved attach-boundary semantics across pause/resume by recording the pre-pause top state and restoring `ATTACHED` when a run is resumed from an attach-boundary pause.
- Added a recovery fail-safe for sidecars that restart while persisted in `RECOVERY_NEEDED`, preventing an empty-pane spin loop after a mid-recovery crash.

### Sunset & Eval

- Added the Slice 4A/4B regression and robustness harnesses, including golden scenarios, contradiction-routing coverage, v2 synthetic corpora, and sunset-trigger evaluation.
- Encoded the v1 live-ingest sunset lifecycle (`NORMAL -> DEPRECATION -> ENFORCEMENT`) while keeping permanent replay/export compatibility for legacy checkpoints.
- Locked the sunset trigger to the current observation window and frozen ingress surfaces, and added regression coverage for stale and future-dated coverage signals.

## 0.3.2 (2026-04-17)

### Observability

- Added a canonical global session collector so `status`, `dashboard`, `tui`, and `observe` can see live, orphaned, and completed runs across worktrees from any directory.
- Made global session tags explicit for `attached`, `recovery`, and pause classes so operator surfaces stop collapsing distinct runtime states into one generic view.

### Runtime State Machine

- Split recovery-oriented runtime flow from business pauses with `RECOVERY_NEEDED`, `pause_class`, and attach-boundary handling for first-checkpoint enforcement.
- Tightened escalation precedence so missing external input, blocked states, and destructive authorization requests are escalated before attach-boundary re-injection logic.

### Delivery Robustness

- Hardened tmux injection with a readiness gate that defers send-keys while the pane buffer is still changing or the runtime is actively typing.
- Fixed idle prompt detection so an empty `› ` / `❯ ` prompt is treated as ready, while real user typing still defers injection.

## 0.3.1 (2026-04-15)

- Standardized the PyPI release flow so publishing only happens from `v*` tags.
- Added a guard that requires the release tag to point to the current `main` HEAD.
- Prepared the next release line after the `0.3.0` E2E cut.

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
