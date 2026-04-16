# Operator Channel And Explainer Architecture Plan


**Goal:** Add an operator-facing channel architecture that lets humans observe, understand, and intervene in supervised runs through TUI or IM-style surfaces without weakening the deterministic supervisor core.

**Architecture:** Keep `thin-supervisor`'s runtime control plane deterministic and acceptance-centered. Add a separate operator plane made of: (1) multi-source observation ingestion, (2) a canonical channel event stream, (3) pluggable channel adapters such as TUI / Telegram / Lark, and (4) an explainer/mediator layer that can translate, summarize, diagnose drift, and answer operator questions with codebase context.

**Tech Stack:** Python 3.10, existing `supervisor/app.py`, `supervisor/daemon/server.py`, `supervisor/history.py`, `supervisor/domain/session.py`, `supervisor/global_registry.py`, `supervisor/notifications.py`, `supervisor/adapters/*`, JSON/JSONL runtime artifacts, tmux/open-relay/jsonl surfaces, optional future tool-using explainer agent.

## Problem Statement

The current system has become strong enough to supervise real work, but it still treats operator visibility as a collection of CLI commands instead of a first-class product surface.

This creates a gap for long-running work:

- a human can see that a run exists, but not always understand what it is doing
- the worker and supervisor may exchange English checkpoint/instruction text, while the human wants a Chinese explanation
- the human may suspect the task is drifting, but there is no structured "explain what is happening and whether it still matches the plan" interface
- TUI, Telegram, Lark, and future channels do not yet share a common read model

The result is that runtime control is relatively mature, while operator mediation remains fragmented.

## Current Technical Facts

Before designing the channel plane, keep the current observation facts explicit.

### Observation Sources Today

There are three runtime observation surfaces:

1. `tmux`
2. `open_relay`
3. `jsonl`

They do **not** all read the same underlying source.

| Surface | How observation works now | Does it read agent transcript JSONL? | Can it inject interactively? |
|--------|----------------------------|--------------------------------------|------------------------------|
| `tmux` | `tmux capture-pane` + terminal text parsing | No | Yes |
| `open_relay` | `oly logs` + `oly send` | No | Yes |
| `jsonl` | Tail agent-native transcript JSONL | Yes | No, observation-only |

Relevant code:

- `supervisor/adapters/surface_factory.py`
- `supervisor/terminal/adapter.py`
- `supervisor/adapters/open_relay_surface.py`
- `supervisor/adapters/jsonl_observer.py`

### Two Different JSONL Concepts Already Exist

The system already has two different JSONL families:

1. **Agent transcript JSONL**
   - Codex/Claude transcript files
   - used only by the `jsonl` observation surface today

2. **Supervisor runtime JSONL**
   - `session_log.jsonl`
   - `decision_log.jsonl`
   - notification and shared friction logs
   - produced for all runs regardless of surface

This distinction matters because future operator channels should not directly couple themselves to one specific raw source such as tmux text or transcript JSONL.

### Current Operator Surface

Today the operator can use:

- `thin-supervisor status`
- `thin-supervisor ps`
- `thin-supervisor pane-owner`
- `thin-supervisor observe`
- `thin-supervisor dashboard`
- exported history via `run summarize`, `run replay`, and `run postmortem`

This is useful, but it is still a **query-style CLI surface**, not a general channel abstraction.

## Product Goals

The operator plane must support all of the following:

1. **Observe**
   - what is running
   - where it is running
   - what the supervisor most recently saw

2. **Explain**
   - what the agent is doing right now
   - why the supervisor decided to continue / pause / retry / verify
   - what a recent English exchange means, explained in Chinese if requested

3. **Diagnose**
   - whether the run is likely drifting from the approved plan
   - whether the current evidence still matches the spec and codebase

4. **Intervene**
   - pause
   - resume
   - request clarification
   - ask for a human-readable explanation before deciding whether to intervene

5. **Generalize across channels**
   - TUI
   - Telegram
   - Lark
   - future web/portal UI

The operator should not need to understand low-level execution surfaces in order to use these capabilities.

## Non-Goals

- Do not replace the deterministic supervisor core with a free-form agent.
- Do not make TUI the only operator entrypoint.
- Do not require transcript JSONL to exist for every runtime path.
- Do not bypass checkpoint, verifier, acceptance, or state-machine correctness in favor of conversational convenience.
- Do not build a full graphical control center in this phase.

## Architecture Decision

### Keep the Runtime Core Deterministic

The current supervisor core should remain the system of record for:

- run state machine
- checkpoint protocol
- verifier execution
- finish gate / acceptance
- pause/resume/recovery
- durable audit logs

This layer must remain deterministic, testable, and conservative.

### Add a Separate Operator Plane

The operator plane should be built as four explicit layers:

1. **Observation Ingestion Plane**
2. **Canonical Channel State / Event Plane**
3. **Channel Adapter Plane**
4. **Explainer / Mediator Plane**

This preserves correctness while allowing richer human interaction.

## Layer 1: Observation Ingestion Plane

This layer normalizes raw facts from multiple sources:

- tmux terminal capture
- open-relay session logs
- transcript JSONL
- future provider hooks
- supervisor-owned session/decision logs

The key rule is:

> Raw observation sources remain heterogeneous, but they must feed a single normalized event model.

### Required Principle

Channels must **not** read tmux, open-relay, or transcript files directly in ad hoc ways.

Instead:

- raw sources feed normalization
- normalization produces canonical operator-visible state
- channels consume canonical state

## Layer 2: Canonical Channel State / Event Plane

Introduce a stable operator-facing model that every channel reads.

### Proposed First-Class Objects

#### `RunSnapshot`

Current state of a run for operator display.

Suggested fields:

- `run_id`
- `worktree_root`
- `controller_mode`
- `surface_type`
- `surface_target`
- `top_state`
- `current_node`
- `pause_reason`
- `next_action`
- `last_checkpoint_summary`
- `last_instruction_summary`
- `updated_at`

#### `RunTimelineEvent`

Canonical timeline event for operator review.

Suggested kinds:

- `checkpoint`
- `instruction_injected`
- `instruction_delivery`
- `verification_started`
- `verification_result`
- `pause`
- `resume`
- `routing`
- `notification`
- `operator_note`

Suggested common fields:

- `run_id`
- `event_id`
- `event_type`
- `occurred_at`
- `source_type`
- `source_ref`
- `summary`
- `payload`

#### `ExchangeView`

A human-readable "what just happened between supervisor and worker" object.

Suggested fields:

- `run_id`
- `window_start`
- `window_end`
- `worker_text_excerpt`
- `supervisor_instruction_excerpt`
- `checkpoint_excerpt`
- `explanation_zh`
- `explanation_en`
- `confidence`

#### `DriftAssessment`

A structured answer to "is this run still on track?"

Suggested fields:

- `run_id`
- `status`: `on_track | watch | drifting | blocked`
- `reasons`
- `evidence`
- `codebase_signals`
- `recommended_operator_action`

## Layer 3: Channel Adapter Plane

This layer exposes the same operator semantics through different interfaces.

### Design Rule

Channels are presentation/adaptation layers, not alternative control planes.

### Proposed Channel Types

#### TUI

Primary local operator channel.

Purpose:

- watch multiple runs
- inspect a selected run
- ask for explanation
- pause/resume
- view recent exchanges

#### Telegram

Remote notification + command channel.

Purpose:

- receive pause/blocked alerts
- inspect run summary remotely
- ask "what is it doing?"
- pause/resume without opening the terminal

#### Lark / Feishu

Enterprise IM channel with similar semantics to Telegram.

Purpose:

- status and alerting
- operator-side explanation and intervention
- team-facing run visibility

### Common Channel API

Every channel should be built against one API family:

- `list_runs()`
- `get_run_snapshot(run_id)`
- `get_run_timeline(run_id, limit=...)`
- `get_recent_exchange(run_id)`
- `explain_run(run_id, language=...)`
- `explain_exchange(run_id, event_window=..., language=...)`
- `assess_drift(run_id)`
- `pause_run(run_id)`
- `resume_run(run_id)`
- `request_clarification(run_id, question=...)`
- `add_operator_note(run_id, note=...)`

## Layer 4: Explainer / Mediator Plane

This is the layer that answers the human's natural-language questions about a run.

### Why It Exists

The human does not only want raw logs. The human wants:

- translation
- explanation
- drift analysis
- clarification
- code-aware interpretation

That is a different job than deterministic supervision.

### Responsibilities

The explainer/mediator should:

- read run state and canonical timeline
- inspect approved spec and current node
- inspect recent worker/supervisor exchanges
- inspect relevant codebase files when needed
- produce human-friendly summaries and explanations
- assess whether current behavior still matches the approved plan

### Default Mode: Read-Only

By default, the explainer should be **read-only**.

It may answer:

- "What is the agent doing?"
- "Why did the supervisor pause?"
- "Translate the last exchange into Chinese."
- "Does this look like it is drifting?"
- "Which files is this step actually touching?"

It should **not** directly mutate run state unless explicitly invoked through an intervention API.

### Intervention Boundary

Interventions remain explicit:

- pause
- resume
- operator clarification request
- optional human override

The explainer may recommend an intervention, but should not silently perform one.

## Should the Supervisor Become a Stronger Tool-Using Agent?

### Recommendation

No. Do not replace the supervisor core with a stronger agent.

### Reason

The deterministic runtime and the explanatory side-channel solve different problems:

- the runtime core enforces correctness and acceptance
- the explainer helps humans understand and steer

If these are merged into one agentic controller, the project risks:

- weaker determinism
- harder testing
- fuzzier trust boundaries
- more ambiguous recovery semantics

### Recommended Model

Keep:

- **Supervisor Core** = deterministic runtime

Add:

- **Explainer/Mediator Agent** = stronger tool-using sidecar with codebase access

This stronger agent may use broader tools, but it should sit **beside** the runtime, not replace it.

## TUI Scope

TUI is necessary, but it should be treated as the **first channel implementation**, not the whole product.

### TUI v1 Goal

Give the operator one local interface that can:

- list runs across worktrees
- inspect one selected run
- show current state, node, and ownership
- show recent timeline events
- ask the explainer for human-readable summaries
- issue basic interventions

### TUI v1 Layout

Recommended layout:

- **Left pane:** run list
- **Center pane:** selected run summary + timeline
- **Right pane:** explanation / drift / next action
- **Bottom command line:** operator prompts such as:
  - "用中文解释它现在在做什么"
  - "判断这是不是跑偏了"
  - "暂停这条 run"

### TUI v1 Non-Goals

- no complex curses-heavy process manager
- no full visual IDE
- no direct raw tmux/open-relay control surface

## Example User Flows

### Flow A: Local tmux run with Chinese explanation

1. Worker runs in tmux under daemon-owned supervision
2. Operator opens TUI
3. TUI shows active run and latest checkpoint
4. Operator asks:
   - "用中文解释它刚才为什么没有进入下一步"
5. Explainer reads:
   - latest checkpoint
   - latest injected instruction
   - verifier result
   - relevant code files if needed
6. TUI shows a Chinese explanation plus recommended next action

### Flow B: Remote alert through Telegram

1. Run hits `PAUSED_FOR_HUMAN`
2. Notification channel pushes summary to Telegram
3. Operator replies:
   - `/explain run_x`
4. Explainer returns summary and drift assessment
5. Operator chooses:
   - `/resume run_x`
   - or `/pause run_x`
   - or `/clarify run_x 请解释你为什么改了 exports 路径`

### Flow C: Observation-only JSONL session

1. Supervisor observes native transcript JSONL
2. Run is still observation-only and cannot guarantee delivery
3. Channel still shows:
   - what the worker reported
   - what the supervisor inferred
   - why the run is now paused for human
4. Explainer helps the human understand the pause, even when the control path is weaker

## Implementation Strategy

### Phase 1: Canonical Operator Model

Build:

- `RunSnapshot`
- `RunTimelineEvent`
- read APIs over existing runtime/session artifacts
- normalized provenance across tmux/open-relay/jsonl

Do not build TUI first. First make the read model stable.

### Phase 2: Explainer Read Path

Build:

- `explain_run`
- `explain_exchange`
- `assess_drift`

Use:

- current run state
- session history
- spec context
- targeted codebase reads

Keep it read-only.

### Phase 3: TUI v1

Build:

- run list
- run inspect
- timeline view
- explanation panel
- pause/resume actions

### Phase 4: IM Channel Adapters

Build:

- Telegram adapter
- Lark/Feishu adapter

against the same channel API, not separate business logic.

## Acceptance Criteria

The design is not considered implemented until all of the following are true:

1. `tmux`, `open_relay`, and `jsonl` observation paths all feed a canonical operator-visible state model.
2. TUI/Telegram/Lark do not need direct knowledge of tmux pane capture, relay logs, or transcript parsing internals.
3. An operator can ask what a run is doing and receive a code-aware explanation in Chinese or English.
4. An operator can request a drift assessment that references the approved plan and current codebase evidence.
5. The explainer has broader read capability than the runtime core, but does not silently mutate runtime state.
6. The deterministic supervisor remains the authority for checkpoint, verifier, acceptance, pause/resume, and recovery behavior.
7. TUI is delivered as the first channel implementation, but the architecture remains channel-agnostic.

## Open Questions

1. Should the canonical operator event model be a new durable log, or a derived view over existing `session_log.jsonl` + state snapshots?
2. Should the explainer run inside the same process as the daemon, or as a separate service/agent?
3. How much codebase access should the explainer have by default for security-sensitive repos?
4. Should operator clarifications be appended as first-class runtime events, or stored as side notes?
5. For IM channels, should intervention commands require a second confirmation for risky actions such as overriding a blocked pause?

## Recommendation

Build this in the following order:

1. canonical operator state/event model
2. read-only explainer APIs
3. TUI v1
4. IM channel adapters

Do **not** start with a heavy TUI and do **not** replace the deterministic runtime with a stronger conversational agent.
