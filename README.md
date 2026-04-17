# thin-supervisor

[![PyPI version](https://img.shields.io/pypi/v/thin-supervisor)](https://pypi.org/project/thin-supervisor/)

**Long-running AI coding tasks fail silently.** The agent asks "should I continue?", you're not watching, and the task stalls. Or worse — the agent says "done" but didn't actually pass the tests.

thin-supervisor fixes this. It's an acceptance-centered run supervisor that sits alongside your existing coding agent (Claude Code, Codex, or any CLI agent), watches what the agent does, and makes structured decisions: continue, re-inject, verify, retry, branch, recover, escalate, or finish. "Done" means the verifier passed and the acceptance contract is satisfied — not that the agent said so. You stay in your familiar agent UI. The supervisor handles the rest.

> **Architecture deep-dive**: See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the six-layer architecture, first-class objects, and design principles.
>
> **Docs hub**:
> - [docs/getting-started.md](docs/getting-started.md) — install and run tmux, open-relay, and JSONL workflows
> - [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — object model, layers, and current implementation status
> - [CHANGELOG.md](CHANGELOG.md) — release notes and unreleased changes
> - [docs/design/p2-external-surfaces.md](docs/design/p2-external-surfaces.md) — surface abstraction and roadmap
> - [docs/design/p3-observation-sources.md](docs/design/p3-observation-sources.md) — observation sources and normalization
> - [docs/design/p4-jsonl-observation.md](docs/design/p4-jsonl-observation.md) — transcript-backed observation mode
> - [docs/reviews/2026-04-11-deep-code-review.md](docs/reviews/2026-04-11-deep-code-review.md) — latest deep code review log and remaining-risk audit
> - [docs/reviews/2026-04-12-amp-supervisor-capability-review.md](docs/reviews/2026-04-12-amp-supervisor-capability-review.md) — Amp-vs-thin-supervisor capability review and oracle-layer roadmap

## Current Status (0.3.2)

- **Global-first observability is live.** `status`, `dashboard`, `tui`, and `observe` now read from one canonical session index, so runs stay visible across worktrees even after daemon idle shutdown.
- **The runtime state machine is split by intent.** `ATTACHED`, `RECOVERY_NEEDED`, and `pause_class` now distinguish attach-boundary tightening, operational recovery, and true human-owned pauses.
- **Operator IM channels are live.** Telegram and Lark/Feishu provider instances are merged into one logical command surface per bot/app, with one inbound owner and multi-target outbound delivery.
- **tmux injection is harder to wedge.** A readiness gate now checks whether the pane is still changing, actively typing, or actually idle before issuing `send-keys`.
- **The policy-tuning loop is end-to-end.** `thin-supervisor-dev eval` now covers compare, canary, candidate review/status, gating, promotion, and the one-command `eval improve` wrapper.

## Install from PyPI

```bash
pip install -U thin-supervisor
thin-supervisor skill install
```

`pip install -U thin-supervisor` gets the runtime CLI from PyPI. `thin-supervisor skill install` then installs the Codex / Claude skills into your local agent environment.

```text
┌────────────────────────────┐  ┌──────────────────────────┐
│  Your Agent (visible pane) │  │  Supervisor (sidecar)    │
│  Claude Code / Codex       │  │  reads pane output       │
│                            │  │  parses checkpoints      │
│  ... working ...           │  │  gates decisions         │
│                            │  │  runs verifiers          │
│  <checkpoint>              │──│  injects next step       │
│  status: step_done         │  │                          │
│  </checkpoint>             │  │  state: RUNNING → VERIFY │
└────────────────────────────┘  └──────────────────────────┘
                 tmux session
```

## When to use this

| Scenario | Without supervisor | With supervisor |
|----------|-------------------|-----------------|
| 10-step implementation plan | Agent asks permission at every step | Runs to completion, verifies each step |
| Test-driven workflow | Agent says "done" without running tests | Verifier runs tests, rejects if failing |
| Agent asks "should I continue?" | You miss it, task stalls for hours | Supervisor auto-answers, keeps going |
| Dangerous operation detected | Agent proceeds silently | Supervisor escalates to you |

## Core Concepts

### Runtime Objects (stable)

| Object | Question it answers | What it is |
|--------|-------------------|------------|
| **WorkflowSpec** | What should be done? | YAML task definition with steps, verification criteria, and finish policy |
| **CheckpointEvent** | What did the agent just report? | Structured status with seq tracking, evidence, and needs |
| **SupervisorDecision** | What does the control plane think? | Typed gate decision with confidence, reasoning, and causality link |
| **HandoffInstruction** | What should the agent do next? | Composed instruction with full traceability to the triggering decision |
| **ExecutionSurface** | How do we talk to the agent? | Protocol for read/inject/cwd — tmux, open-relay, and JSONL observation surfaces |
| **SessionRun** | Who is this run? | Identity + durable event history — survives crashes, enables recovery |

### Emerging Architecture (implemented, maturing)

| Object | Purpose |
|--------|---------|
| **AcceptanceContract** | Defines "what counts as truly done" — required evidence, forbidden states, risk class, reviewer gating |
| **WorkerProfile** | Explicit worker capabilities — provider, model, trust level. Drives supervision intensity. |
| **SupervisionPolicy** | Three modes: `strict_verifier` (default) / `collaborative_reviewer` / `directive_lead`. Prevents thin supervisor from micromanaging strong worker. |
| **RoutingDecision** | Escalation routing — human, stronger reviewer, or alternate executor |

These form a **causality chain**: every instruction traces back to the decision that caused it, which traces back to the checkpoint that triggered it.

```text
CheckpointEvent(seq=3) → SupervisorDecision(triggered_by_seq=3) → HandoffInstruction(triggered_by_decision=X)
```

## Quick Start

> **Full guide**: See [docs/getting-started.md](docs/getting-started.md) for step-by-step instructions covering tmux, open-relay, JSONL observation, and Codex/Claude/OpenCode/Droid workflows.

```bash
# Install
pip install thin-supervisor

# Install the Codex / Claude skills automatically when supported
thin-supervisor skill install

# Initialize in your project
cd your-project
thin-supervisor init
# If .supervisor/ exists but is missing config, repair the scaffold in place
thin-supervisor init --repair

# Write a spec (or let the Skill generate one)
cat > .supervisor/specs/my-plan.yaml << 'EOF'
kind: linear_plan
id: my_feature
goal: implement feature X with tests
approval:
  required: true
  status: draft
finish_policy:
  require_all_steps_done: true
  require_verification_pass: true
policy:
  default_continue: true
  max_retries_per_node: 3

steps:
  - id: write_tests
    type: task
    objective: write failing tests for feature X
    verify:
      - type: artifact
        path: tests/test_feature_x.py
        exists: true

  - id: implement
    type: task
    objective: implement feature X until tests pass
    verify:
      - type: command
        run: pytest -q tests/test_feature_x.py
        expect: pass

  - id: final_check
    type: task
    objective: run full test suite
    verify:
      - type: command
        run: pytest -q
        expect: pass
EOF

# Approve the draft spec, then attach
thin-supervisor spec approve --spec .supervisor/specs/my-plan.yaml --by human
scripts/thin-supervisor-attach.sh my-plan
```

Execution entry points reject draft specs. This is deliberate: the
clarify/approve step is part of the contract.

## What happens next

1. Supervisor reads the agent's pane output every 2 seconds
2. Agent emits a `<checkpoint>` block after completing work
3. Supervisor parses the checkpoint and makes a gate decision:
   - **Continue** — agent is making progress, don't interrupt
   - **Re-inject** — run is attached but the first checkpoint still only cites attach/spec/admin work, so tighten the current-node instruction
   - **Verify** — agent says step is done, run the verifier
   - **Retry** — verification failed, inject retry instruction with failure details
   - **Branch** — decision node in workflow, select a path
   - **Recover** — delivery/session-health fault; supervisor attempts bounded auto-recovery before surfacing it
   - **Escalate** — missing credentials, dangerous action, explicit review, or low confidence — pause for human
   - **Finish** — all steps done, all verifiers pass, finish policy and review requirements satisfied
4. If continuing or retrying, supervisor injects the next instruction into the pane
5. Run-level decisions are logged to `session_log.jsonl`; project-level bootstrap and repair incidents are logged to `.supervisor/runtime/ops_log.jsonl`

Historical runs can now be turned into stable artifacts and reports:

```bash
thin-supervisor run export <run_id> > run.json
thin-supervisor run summarize <run_id> --json
thin-supervisor run replay <run_id> --json
thin-supervisor run postmortem <run_id>
```

`run replay` re-evaluates historical checkpoints with the current gate logic but does not inject or verify against live surfaces. `run postmortem` writes a markdown report under `.supervisor/reports/` by default.

If your spec sets `acceptance.must_review_by`, the run pauses at the finish gate until someone acknowledges review:

```bash
thin-supervisor run review <run_id> --by human
# or
thin-supervisor run review <run_id> --by stronger_reviewer
```

When a run enters `PAUSED_FOR_HUMAN`, thin-supervisor now derives two user-facing fields:
- `pause_reason` — why the supervisor stopped
- `next_action` — the exact recovery command to run next

By default the daemon also emits pause notifications through two built-in channels:
- `tmux_display` — a `tmux display-message` alert on the supervised pane
- `jsonl` — durable records in `.supervisor/runtime/notifications.jsonl`

Pause handling is now also policy-driven:
- `pause_handling_mode: notify_only` — notify and remain paused
- `pause_handling_mode: notify_then_ai` — notify first, then let the agent attempt an automatic recovery for selected cases such as blocked checkpoints, repeated node mismatch, or retry-budget exhaustion

The default is currently tuned for test periods:

```yaml
pause_handling_mode: notify_then_ai
max_auto_interventions: 2
```

The default config now includes:

```yaml
notification_channels:
  - kind: tmux_display
  - kind: jsonl
pause_handling_mode: notify_then_ai
max_auto_interventions: 2
```

Built-in notification channels today are `tmux_display`, `jsonl`, `telegram`, and `lark`. Telegram and Lark can also run in command mode through `OperatorChannelHost`, which merges provider-instance config into one logical command surface with a single inbound owner.

## Checkpoint Protocol

Agents must emit structured checkpoints for the supervisor to parse:

```text
<checkpoint>
run_id: <run_id from thin-supervisor status>
checkpoint_seq: <incrementing integer, start from 1>
status: working | blocked | step_done | workflow_done
current_node: <step_id>
summary: <one-line description>
evidence:
  - modified: <file path>
  - ran: <command>
  - result: <short result>
candidate_next_actions:
  - <next action>
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
```

The Codex/Claude Code Skills teach agents this protocol automatically.

For a newly attached node, the **first** checkpoint must cite execution evidence for the current node's objective. Clarify/spec/attach/baseline artifacts are prior-phase work and do not count as execution progress on the newly injected node.

## Verification Types

| Type | Fields | Description |
|------|--------|-------------|
| `command` | `run`, `expect` | Run a shell command. `expect`: `pass`, `fail`, `contains:<text>` |
| `artifact` | `path`, `exists` | Check if a file exists |
| `git` | `check`, `expect` | Check git state (e.g., `check: dirty`, `expect: false`) |
| `workflow` | `require_node_done` | Check if current node is marked done |

All verifiers run in the agent's working directory (pane cwd), not the supervisor's.

## CLI

```bash
thin-supervisor init [--force|--repair]                   # Create or repair .supervisor/ directory
thin-supervisor deinit [--force]                           # Remove .supervisor/
thin-supervisor bootstrap                                  # Init + daemon + surface validation fast path

thin-supervisor daemon start [--config <path>]             # Start background daemon
thin-supervisor daemon stop                                # Stop daemon
thin-supervisor stop                                       # Legacy alias for daemon stop

thin-supervisor run register --spec <spec> --pane <target> [--surface tmux|open_relay|jsonl]
thin-supervisor run foreground --spec <spec> --pane <target> [--surface ...]
thin-supervisor run stop <run_id>
thin-supervisor run resume --spec <spec> --pane <target> [--surface ...]
thin-supervisor run review <run_id> --by human|stronger_reviewer
thin-supervisor run export <run_id> [--output file]
thin-supervisor run summarize <run_id> [--json]
thin-supervisor run replay <run_id> [--json]
thin-supervisor run postmortem <run_id> [--output file]
thin-supervisor spec approve --spec <spec> [--by human]

thin-supervisor status                                     # Every run across every known worktree (global-first)
thin-supervisor status --local                             # Restrict to the current worktree only
thin-supervisor list                                       # Detailed active-run view
thin-supervisor dashboard                                  # Interactive dashboard with drill-in
thin-supervisor tui                                        # Operator TUI with explain/drift/pause actions
thin-supervisor ps                                         # Registered daemon processes across worktrees
thin-supervisor pane-owner <pane>                          # Show which run owns a pane
thin-supervisor observe <run_id>                           # Read-only snapshot; works even when no daemon is live
thin-supervisor note add <text> [--type ...] [--run ...]  # Shared notes for coordination
thin-supervisor note list [--type ...] [--run ...]

thin-supervisor session detect                             # Detect current agent session ID
thin-supervisor session jsonl                              # Resolve current transcript path
thin-supervisor session list                               # List recent sessions and cwd
thin-supervisor config set <key> <value>                   # Persist config updates

thin-supervisor skill install                              # Install Codex / Claude skills
thin-supervisor bridge <action> [args]                     # tmux bridge operations
```

`thin-supervisor` is the runtime CLI. It is the only command family normal task users should need.

```bash
thin-supervisor-dev learn friction add --kind <kind> --message "..." [--run-id <run_id>] [--signal <signal>]
thin-supervisor-dev learn friction list [--run-id <run_id>] [--kind <kind>] [--json]
thin-supervisor-dev learn friction summarize [--run-id <run_id>] [--kind <kind>] [--json]
thin-supervisor-dev learn prefs set --key <key> --value <value>
thin-supervisor-dev learn prefs show [--json]
thin-supervisor-dev eval list
thin-supervisor-dev eval run [--suite approval-core|approval-adversarial|clarify-contract-core|routing-core|escalation-core|finish-gate-core|pause-ux-core] [--json]
thin-supervisor-dev eval replay --run-id <run_id> [--json]
thin-supervisor-dev eval compare --suite approval-core --candidate-policy <policy> [--json]
thin-supervisor-dev eval canary --run-id <run_id> [--run-id <run_id> ...] [--candidate-id <candidate_id>] [--phase shadow|limited] [--json]
thin-supervisor-dev eval expand --suite approval-core --output <path> [--variants-per-case 2]
thin-supervisor-dev eval propose --suite approval-core --objective <goal> [--json]
thin-supervisor-dev eval review-candidate --candidate-id <candidate_id> [--json]
thin-supervisor-dev eval candidate-status --candidate-id <candidate_id> [--json]
thin-supervisor-dev eval gate-candidate --candidate-id <candidate_id> [--run-id <run_id> ...] [--json]
thin-supervisor-dev eval promote-candidate --candidate-id <candidate_id> --approved-by <name> [--run-id <run_id> ...] [--json]
thin-supervisor-dev eval improve --suite approval-core --objective <goal> [--approved-by <name>] [--run-id <run_id> ...] [--json]
thin-supervisor-dev eval promotion-history [--json]
thin-supervisor-dev eval rollout-history [--candidate-id <candidate_id>] [--json]
thin-supervisor-dev oracle consult --question "..." [--file path ...]
```

`thin-supervisor-dev` is the devtime/operator CLI. Use it for local tuning, offline evals, candidate rollout, learning signals, and advisory second opinions. Do not expose it to normal runtime users.

Add `--save-report` to `run`, `replay`, `compare`, `canary`, `propose`, `review-candidate`, `gate-candidate`, `promote-candidate`, or `improve` to persist a JSON report under `.supervisor/evals/reports/`. When used with `eval propose`, `thin-supervisor-dev` also writes a candidate-lineage manifest under `.supervisor/evals/candidates/`, `eval review-candidate` turns that manifest back into a bounded human review summary, `eval candidate-status` assembles the manifest, latest related reports, and promotion-registry state into one lifecycle dossier, `eval gate-candidate` combines compare plus optional canary signals into a promotion recommendation, and `eval promote-candidate` records an approved promotion in `.supervisor/evals/promotions.jsonl`. `eval improve` is the one-command wrapper for this path: it runs propose -> review/status -> gate and only promotes when `--approved-by` is supplied and the gate allows promotion (or `--force` is used).

If a daemon-managed run pauses, `status` and `list` now show the human-readable reason and the suggested next command. For non-active persisted runs, the same hint appears under `Local state found:`.

### Global observability plane

`status`, `dashboard`, and `tui` all read from a single canonical session index (`supervisor/operator/session_index.py`) that unions discovery across:

- the current cwd
- `list_known_worktrees()` (persisted registry, survives daemon/pane shutdown)
- live daemon cwds
- live pane-owner cwds
- `git worktree list` for the current repo (read-only)

As a result:

- Every operator read surface sees the same run universe. If `status` shows a run, `dashboard` and `tui` see it too, and vice versa.
- A run that outlives its daemon (persisted to disk, daemon idle-shutdown) stays visible from any cwd — it tags as `orphaned` instead of disappearing.
- `observe <run_id>` resolves globally. When no daemon is live for the run, it reads the snapshot and recent events directly from the run's on-disk state + `session_log.jsonl`, so a paused run in a child worktree is still inspectable from the root workspace.
- `status --local` narrows the view to the current worktree; `ps` is process-oriented (which daemon processes are alive) and is distinct from run-oriented `status`.

`thin-supervisor-dev eval` is the first offline evaluation surface for the new skill-evolution work. Bundled suites now cover more than approval copy: `approval-core` checks explicit approval vs re-ask behavior, `approval-adversarial` covers tricky mixed signals and repeat-approval cases, `clarify-contract-core` checks whether the system locks the right delivery contract instead of silently narrowing “real UAT” work into a mock/dev baseline, `routing-core` checks deterministic `step_done/workflow_done -> VERIFY_STEP` routing, `escalation-core` checks `blocked -> ESCALATE_TO_HUMAN`, `finish-gate-core` checks reviewer and completion contracts, and `pause-ux-core` checks externally visible pause/completion summaries. `thin-supervisor-dev eval replay --run-id ...` wraps the existing history replay path into the same evaluation surface so policy candidates can be checked against real historical traces. `thin-supervisor-dev eval compare ...` adds a blind `A/B`-style comparator over deterministic suite results so baseline and candidate policies can be compared without hard-coding one output format into the report consumer. `thin-supervisor-dev eval canary ...` aggregates replay pass-rate, mismatch kinds, and friction over a set of real runs so shadow-canary promotion decisions become a command instead of a checklist; when you pass `--candidate-id`, the same command also records a rollout attempt under `.supervisor/evals/rollouts.jsonl`. `thin-supervisor-dev eval expand ...` generates provenance-tagged synthetic variants from the golden suite so coverage can grow without mutating the original contract set. `thin-supervisor-dev eval propose ...` is the constrained candidate-generator surface: it summarizes failure cases, consults the advisory/self-review layer, recommends a policy candidate for a stated objective without automatically changing shipped defaults, and can persist a candidate-lineage manifest for later comparison and promotion review. `thin-supervisor-dev eval review-candidate ...` loads one of those manifests and emits the bounded human-review summary for the next promotion step. `thin-supervisor-dev eval candidate-status ...` turns the manifest, related eval reports, promotion-registry state, and recorded rollout attempts into one lifecycle dossier. `thin-supervisor-dev eval rollout-history ...` exposes the rollout ledger directly. `thin-supervisor-dev eval gate-candidate ...` then combines that bounded review with deterministic compare output and optional real-run canary signals before a human decides whether to promote. `thin-supervisor-dev eval improve ...` is the current-main-native convenience wrapper around that same flow, so the old “proposal improvement loop” UX exists without reviving a parallel implementation. `thin-supervisor-dev eval promote-candidate ...` records an approved promotion in the promotion registry so candidate history and current promoted policies are queryable later.

### Real Canary Loop

Yes, you should run real canaries. A safe sequence is:

1. Offline gate
   Run `eval run`, `eval replay`, `eval compare`, and optionally `eval propose`, all with `--save-report`.
2. Shadow canary
   Pick 3-5 real tasks and keep the baseline behavior in charge. Record each finished run with:
   `thin-supervisor run summarize <run_id>`
   `thin-supervisor run postmortem <run_id>`
   `thin-supervisor-dev eval replay --run-id <run_id> --save-report`
   `thin-supervisor-dev eval canary --run-id <run_id> ... --candidate-id <candidate_id> --phase shadow --save-report`
   `thin-supervisor-dev eval rollout-history --candidate-id <candidate_id> --json`
3. Limited rollout
   If shadow canary stays clean, run 10-20 real tasks with the candidate under close observation.

For each real canary, log friction explicitly when needed:

```bash
thin-supervisor-dev learn friction add \
  --kind repeated_confirmation \
  --message "user had to approve twice" \
  --run-id <run_id> \
  --signal user_repeated_approval
```

Then summarize what actually accumulated for a run:

```bash
thin-supervisor-dev learn friction summarize --run-id <run_id> --json
```

### Bridge subcommands

```bash
thin-supervisor bridge read <pane> [lines]   # Capture pane output
thin-supervisor bridge type <pane> <text>     # Send text (no Enter)
thin-supervisor bridge keys <pane> <key>...   # Send special keys
thin-supervisor bridge list                   # Show all panes
thin-supervisor bridge id                     # Current pane ID
thin-supervisor bridge doctor                 # Check tmux connectivity
```

## Configuration

`.supervisor/config.yaml`:

```yaml
surface_type: "tmux"              # tmux | open_relay | jsonl
surface_target: "agent"           # pane label / oly session ID / transcript path
poll_interval_sec: 2.0            # seconds between reads
read_lines: 100                   # lines captured per read

# LLM judge (null = offline stub mode, rules-only)
judge_model: null                 # e.g., anthropic/claude-haiku-4-5-20251001
judge_temperature: 0.1
judge_max_tokens: 512
```

`jsonl` is observation-only: the supervisor can watch checkpoints from a transcript file, but instruction delivery still depends on the agent skill / hook path.

Override with environment variables: `SUPERVISOR_SURFACE_TYPE`, `SUPERVISOR_SURFACE_TARGET`, `SUPERVISOR_PANE_TARGET`, `SUPERVISOR_JUDGE_MODEL`, etc.

## Design Philosophy

Inspired by [Anthropic's Scaling Managed Agents](https://www.anthropic.com/engineering/managed-agents):

1. **The system's memory lives in SessionRun, not in the model's context.** Crashes don't lose history. Everything is in `session_log.jsonl`.

2. **The execution surface is just a "hand", not the system.** Today that includes tmux, open-relay, and transcript-backed JSONL observation. Tomorrow it could be a PTY wrapper or a remote session. The `SessionAdapter` protocol keeps the supervisor decoupled.

3. **Harnesses change, primitives don't.** The current sidecar loop is one harness. The 6 first-class objects (WorkflowSpec, SessionRun, ExecutionSurface, CheckpointEvent, SupervisorDecision, HandoffInstruction) are the stable interface.

4. **Verification is deterministic, not verbal.** "Done" means the verifier passed, not that the agent said so.

5. **Skill evolution happens from structured hindsight, not ad-hoc prompt edits.** `friction_event`s and `user_preference_memory` give the system a durable learning substrate. The intended loop is: capture friction -> summarize/postmortem -> replay/eval candidate policy changes -> update skills/rules only when the offline signal says they are better.

## Skill Integration

Install for Claude Code:
```bash
cp -r skills/thin-supervisor ~/.claude/skills/
```

Install for Codex:
```bash
cp -r packaging/thin-supervisor-codex ~/.codex/skills/thin-supervisor
```

Invoke with `/thin-supervisor` to start the default flow:
- clarify ambiguous goals
- generate a draft spec
- wait for approval
- attach and execute only after approval

The skill is now split into two layers:
- frozen contract: `skills/thin-supervisor*/references/contract.md`
- optimizable strategy fragments under `skills/thin-supervisor*/strategy/`

Future policy optimization should target the strategy fragments, not the whole `SKILL.md`.

## Oracle Consultation

If you want an Amp-style "oracle" second opinion without giving up supervisor control, use:

```bash
thin-supervisor-dev oracle consult \
  --mode review \
  --question "Review the retry policy design" \
  --file supervisor/loop.py \
  --file supervisor/gates/supervision_policy.py
```

When an external provider key is configured, thin-supervisor calls that provider as a read-only advisor. Without an external key, it falls back to a self-adversarial review scaffold instead of failing hard. Add `--run <run_id>` to persist the consultation into the shared notes plane for the active supervised run.

## Development

```bash
git clone https://github.com/fakechris/thin-supervisor
cd thin-supervisor
pip install -e ".[dev]"
pytest -q
```

For repo-specific setup and examples, start with [docs/getting-started.md](docs/getting-started.md).

## License

MIT
