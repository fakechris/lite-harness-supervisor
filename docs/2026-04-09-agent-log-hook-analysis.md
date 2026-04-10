# Agent Hook And Log Analysis

> Scope: consolidate our local source analysis for `claude-code`, `codex`, and `opensessions`, with emphasis on what data hooks expose, how session/transcript logs are written, and whether a seen JSON event can be treated as a barrier proving earlier events are already in the log.

## Why This Document Exists

We currently supervise agents from the terminal surface:

- read pane text from [supervisor/terminal/adapter.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/terminal/adapter.py)
- parse explicit checkpoints from [supervisor/adapters/transcript_adapter.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/adapters/transcript_adapter.py)
- gate, verify, and inject next instructions from [supervisor/loop.py](/Users/chris/workspace/lite-harness-supervisor/supervisor/loop.py)

That is a terminal-text control plane, not an agent-native event plane.

If we want to add watcher or hook based observability, we need to answer four questions:

1. What data does each agent expose to hooks?
2. What data is written to transcript or rollout files?
3. How does `opensessions` observe those agents today?
4. Can a specific JSON line be used as a fence: "if I see this line, everything before it is already written"?

## Executive Summary

### Short Version

- `Claude Code`
  - hooks expose useful point-in-time context
  - transcript writes are asynchronous and batched
  - hook execution is not a transcript flush barrier
  - seeing one JSONL line does not strictly prove all prior semantic events are flushed

- `Codex`
  - hooks expose explicit request payloads and lifecycle events
  - rollout writing is append-only, single-writer, per-line `flush()`
  - once rollout is materialized, seeing a line in the rollout is a practical in-file ordering barrier
  - this is still file-handle flush, not `fsync`

- `opensessions`
  - `claude-code` and `codex` are watched from local transcript files
  - `amp` in current source is watched from Amp API + DTW WebSocket, not local JSON
  - supported watchers in current tree are `amp`, `claude-code`, `codex`, `opencode`, `pi`
  - there is no standalone `droid` watcher in the current repository tree

### Barrier Strength Matrix

| Source | Data source | Realtime quality | Can it prove prior log lines are already written? | Notes |
| --- | --- | --- | --- | --- |
| Claude hook input | in-process hook payload | high | no | hook and transcript persistence are decoupled |
| Claude transcript JSONL | local JSONL append queue | medium-high | weak | ordered within appended file content, but no hook-to-flush fence |
| Codex hook input | in-process hook payload | high | no by itself | useful context, not a file barrier |
| Codex `HookStarted` / `HookCompleted` in rollout | rollout JSONL | high | yes, practical | only after rollout materialization; still not `fsync` |
| Amp watcher in `opensessions` | API + WebSocket | high | not a local file question | source of truth is remote state, not local transcript |
| OpenCode watcher in `opensessions` | SQLite polling | medium | row-level only | not JSONL |
| Pi watcher in `opensessions` | local JSONL | medium-high | watcher-local only | same caveat as transcript-based systems |

## Our Current Position

Our supervisor today does not consume agent-native hooks or structured transcript events.

What we have:

- rendered terminal text from `tmux capture-pane`
- explicit `<checkpoint>` blocks embedded in that text

What we do not have:

- a first-class watcher layer for Codex or Claude transcript JSONL
- a hook ingestion layer
- a barrier primitive that can say "up to here, transcript persistence is complete"

That matters because watcher-derived states like `running`, `waiting`, `stale`, or `done` are useful as fallback signals, but they are weaker than our existing checkpoint + verifier chain.

## Claude Code

### What Hooks Can See

Claude hook inputs are defined in [src/entrypoints/sdk/coreSchemas.ts](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts).

All hook inputs share a base shape, see [coreSchemas.ts:387](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L387):

- `session_id`
- `transcript_path`
- `cwd`
- `permission_mode?`
- `agent_id?`
- `agent_type?`

Important event-specific payloads:

`UserPromptSubmit`, see [coreSchemas.ts:483](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L483)

- base fields
- `hook_event_name: "UserPromptSubmit"`
- `prompt`

`PreToolUse`, see [coreSchemas.ts:407](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L407)

- base fields
- `hook_event_name: "PreToolUse"`
- `tool_name`
- `tool_input`
- `tool_use_id`

`PostToolUse`, see [coreSchemas.ts:434](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L434)

- base fields
- `hook_event_name: "PostToolUse"`
- `tool_name`
- `tool_input`
- `tool_response`
- `tool_use_id`

`Stop`, see [coreSchemas.ts:512](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L512)

- base fields
- `hook_event_name: "Stop"`
- `stop_hook_active`
- `last_assistant_message?`

`SessionStart`, see [coreSchemas.ts:492](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L492)

- base fields
- `hook_event_name: "SessionStart"`
- `source: startup | resume | clear | compact`
- `agent_type?`
- `model?`

`SessionEnd`, see [coreSchemas.ts:758](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L758)

- base fields
- `hook_event_name: "SessionEnd"`
- `reason`

`TaskCompleted`, see [coreSchemas.ts:614](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L614)

- base fields
- `hook_event_name: "TaskCompleted"`
- `task_id`
- `task_subject`
- `task_description?`
- `teammate_name?`
- `team_name?`

`SubagentStop`, see [coreSchemas.ts:550](/Users/chris/source/claude-code/src/entrypoints/sdk/coreSchemas.ts#L550)

- base fields
- `hook_event_name: "SubagentStop"`
- `stop_hook_active`
- `agent_id`
- `agent_transcript_path`
- `agent_type`
- `last_assistant_message?`

### What Hooks Do Not Give Us

Claude hooks do not hand us the full structured session history.

They give us:

- current session identity
- current cwd
- current prompt or tool IO for this hook point
- last assistant message for stop-like events

They do not give us:

- the entire transcript as a structured event list
- a guaranteed "all earlier transcript entries are now flushed" signal
- hidden model state beyond what Claude explicitly surfaces

### How Claude Transcript Writes Work

Transcript persistence is managed in [src/utils/sessionStorage.ts](/Users/chris/source/claude-code/src/utils/sessionStorage.ts).

Key facts:

- the session file is not materialized until the first real `user` or `assistant` message, see [sessionStorage.ts:972](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L972) and [sessionStorage.ts:1003](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L1003)
- writes are queued per file with `enqueueWrite()`, see [sessionStorage.ts:606](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L606)
- default flush timer is `100ms`, see [sessionStorage.ts:567](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L567)
- actual file appends happen in `drainWriteQueue()`, see [sessionStorage.ts:645](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L645)
- the append uses `fsAppendFile`, not `fsync`, see [sessionStorage.ts:634](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L634)
- many callers enqueue writes with `void this.enqueueWrite(...)`, so they do not await transcript persistence, see [sessionStorage.ts:1160](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L1160)

Claude does have an internal `flush()` method, see [sessionStorage.ts:839](/Users/chris/source/claude-code/src/utils/sessionStorage.ts#L839), which:

- cancels the timer
- waits for the active drain
- drains remaining queued writes
- waits for `pendingWriteCount` to reach zero

But that is an internal API. External hooks do not automatically imply that this flush has completed.

### Hook Timing Relative To Transcript

Claude also has a separate hook event bus in [src/utils/hooks/hookEvents.ts](/Users/chris/source/claude-code/src/utils/hooks/hookEvents.ts), with event types like:

- `started`
- `progress`
- `response`

That bus is earlier than the transcript file.

Implication:

- hook fired: yes
- transcript definitely flushed through that point: no

### What We Can Safely Infer From Claude JSONL

Weak inference:

- if we see line `N` in the current file view, earlier bytes already exist in that file view

Unsafe inference:

- because `Stop` or `UserPromptSubmit` hook ran, all prior semantic events are in transcript
- because we saw one hook-related JSON line, all causally earlier events have flushed

### Claude Watcher Behavior In `opensessions`

Current watcher: [claude-code.ts](/tmp/opensessions/packages/runtime/src/agents/watchers/claude-code.ts)

How it works:

- watches `~/.claude/projects/<encoded-path>/*.jsonl`
- uses `fs.watch` plus `POLL_MS = 2000`, see [claude-code.ts:101](/tmp/opensessions/packages/runtime/src/agents/watchers/claude-code.ts#L101)
- reads appended bytes and derives status from journal entries
- resolves the mux session from the encoded project directory

Status mapping:

- assistant `tool_use` or `thinking` or `stop_reason = null` -> `running`
- assistant `stop_reason = end_turn` -> `done`
- user interrupt marker -> `interrupted`
- stalled tool use after `TOOL_USE_WAIT_MS` -> `waiting`
- no file growth while `running` or `waiting` -> `stale`

This is a practical observer, not a persistence barrier.

## Codex

### What Hooks Can See

Codex hook requests are defined in `codex-rs/hooks/src/events/*`.

`SessionStartRequest`, see [session_start.rs:35](/Users/chris/source/codex/codex-rs/hooks/src/events/session_start.rs#L35)

- `session_id`
- `cwd`
- `transcript_path`
- `model`
- `permission_mode`
- `source`

`UserPromptSubmitRequest`, see [user_prompt_submit.rs:21](/Users/chris/source/codex/codex-rs/hooks/src/events/user_prompt_submit.rs#L21)

- `session_id`
- `turn_id`
- `cwd`
- `transcript_path`
- `model`
- `permission_mode`
- `prompt`

`PreToolUseRequest`, see [pre_tool_use.rs:20](/Users/chris/source/codex/codex-rs/hooks/src/events/pre_tool_use.rs#L20)

- `session_id`
- `turn_id`
- `cwd`
- `transcript_path`
- `model`
- `permission_mode`
- `tool_name`
- `tool_use_id`
- `command`

`PostToolUseRequest`, see [post_tool_use.rs:22](/Users/chris/source/codex/codex-rs/hooks/src/events/post_tool_use.rs#L22)

- everything above
- `tool_response`

`StopRequest`, see [stop.rs:22](/Users/chris/source/codex/codex-rs/hooks/src/events/stop.rs#L22)

- `session_id`
- `turn_id`
- `cwd`
- `transcript_path`
- `model`
- `permission_mode`
- `stop_hook_active`
- `last_assistant_message`

Codex also emits hook lifecycle events in the protocol, see [protocol.rs:1503](/Users/chris/source/codex/codex-rs/protocol/src/protocol.rs#L1503):

- `HookStartedEvent`
- `HookCompletedEvent`

These include:

- `turn_id`
- `run: HookRunSummary`

Those lifecycle events summarize hook execution, but the hook stdin JSON carries the richer request fields.

### How Codex Rollout Writes Work

Rollout persistence is implemented in [rollout/src/recorder.rs](/Users/chris/source/codex/codex-rs/rollout/src/recorder.rs).

Key facts:

- the rollout writer is a single async writer loop, see [recorder.rs:709](/Users/chris/source/codex/codex-rs/rollout/src/recorder.rs#L709)
- new sessions may buffer items in memory until explicit `persist()`
- `persist()` materializes the file and flushes buffered items in order, see [recorder.rs:768](/Users/chris/source/codex/codex-rs/rollout/src/recorder.rs#L768)
- each written line does `write_all()` then `file.flush().await`, see [recorder.rs:978](/Users/chris/source/codex/codex-rs/rollout/src/recorder.rs#L978)
- there is no `sync_all` or `fsync`

Materialization boundary:

- `hook_transcript_path()` first calls `ensure_rollout_materialized()`, see [codex.rs:4229](/Users/chris/source/codex/codex-rs/core/src/codex.rs#L4229)
- user prompt recording also materializes rollout after recording the prompt, see [codex.rs:3898](/Users/chris/source/codex/codex-rs/core/src/codex.rs#L3898)

Tests explicitly verify hook-visible transcript paths exist:

- session start, see [hooks.rs:576](/Users/chris/source/codex/codex-rs/core/tests/suite/hooks.rs#L576)
- pre tool use, see [hooks.rs:1086](/Users/chris/source/codex/codex-rs/core/tests/suite/hooks.rs#L1086)
- post tool use, see [hooks.rs:1438](/Users/chris/source/codex/codex-rs/core/tests/suite/hooks.rs#L1438)

### Hook Event Ordering Relative To Rollout

This is the strongest part of Codex for our purposes.

`send_event_raw()` first persists the event to rollout, then delivers it to clients, see [codex.rs:2751](/Users/chris/source/codex/codex-rs/core/src/codex.rs#L2751).

That means:

- once a hook lifecycle event is visible in the rollout file
- earlier rollout items for that file have already been written in order

This is not a power-loss durability guarantee, but it is a strong practical barrier for log observation.

### What We Can Safely Infer From Codex JSONL

Safe practical inference, after materialization:

- if line `N` is present in the rollout file, earlier rollout lines for that file have already been written and flushed to the file handle

Unsafe stronger claim:

- the data is `fsync` durable
- every other side channel is synchronized

### Codex Watcher Behavior In `opensessions`

Current watcher: [codex.ts](/tmp/opensessions/packages/runtime/src/agents/watchers/codex.ts)

How it works:

- watches `~/.codex/sessions/**/*.jsonl` or `$CODEX_HOME/sessions/**/*.jsonl`
- reads `$CODEX_HOME/session_index.jsonl` for recent titles
- uses recursive `fs.watch` plus `POLL_MS = 2000`, see [codex.ts:158](/tmp/opensessions/packages/runtime/src/agents/watchers/codex.ts#L158)
- resolves project directory from `session_meta.payload.cwd` or `turn_context.payload.cwd`

Status mapping:

- `user_message`, assistant commentary, reasoning, tool activity -> `running`
- delayed `function_call` with no growth -> `waiting`
- `task_complete` or `final_answer` -> `done`
- `turn_aborted` -> `interrupted`
- no growth while active -> `stale`

This matches the source model well because Codex rollout is already close to an append-only event log.

## Amp

### Important Clarification

Current `opensessions` source does not implement Amp as a local JSONL watcher.

The live implementation is [amp.ts](/tmp/opensessions/packages/runtime/src/agents/watchers/amp.ts), and it is:

- thread discovery by Amp HTTP API
- real-time status by DTW WebSocket

Specifically:

- poll `GET /api/threads?limit=20&after=<ts>` every `10s`, see [amp.ts:117](/tmp/opensessions/packages/runtime/src/agents/watchers/amp.ts#L117)
- request worker token with `POST /api/durable-thread-workers`
- connect WebSocket with subprotocol `["amp", wsToken]`
- consume `cf_agent_state` messages as the status source

Status mapping, see [amp.ts:165](/tmp/opensessions/packages/runtime/src/agents/watchers/amp.ts#L165):

- `working`, `streaming`, `running_tools` -> `running`
- `tool_use` -> `tool-running`
- `awaiting_approval` -> `waiting`
- `idle` -> `done`
- `error` -> `error`

It resolves `projectDir` from `env.initial.trees[0].uri`, see [amp.ts:201](/tmp/opensessions/packages/runtime/src/agents/watchers/amp.ts#L201).

### About `opensessions` Docs Drift

The current [CONTRACTS.md](/tmp/opensessions/CONTRACTS.md) still says Amp "watches `~/.local/share/amp/threads/T-*.json`", but the actual watcher source has moved to API + WebSocket.

For implementation decisions, trust the watcher source over the stale contracts note.

### Barrier Semantics For Amp

There is no local JSONL barrier question here in the same sense as Claude or Codex.

Amp watcher strength:

- high-quality near-real-time state transitions
- no local append-only transcript fence from `opensessions` source itself

If we support Amp-like systems, we should treat them as native remote event streams, not as transcript parsers.

## OpenCode And Pi

These are not the primary focus of our current work, but they matter because they show `opensessions` already supports non-JSONL sources.

### OpenCode

Watcher: [opencode.ts](/tmp/opensessions/packages/runtime/src/agents/watchers/opencode.ts)

- source is SQLite, not JSONL
- polls `~/.local/share/opencode/opencode.db` every `3s`
- resolves session from DB `directory`

This proves the watcher abstraction is not transcript-specific.

### Pi

Watcher: [pi.ts](/tmp/opensessions/packages/runtime/src/agents/watchers/pi.ts)

- source is JSONL under `~/.pi/agent/sessions/...`
- uses `fs.watch` plus `POLL_MS = 2000`
- resolves project directory from transcript content or encoded path

### About "Droid"

In the current `opensessions` repository snapshot inspected here, there is no dedicated `droid` watcher.

Supported watcher set visible in source:

- `amp`
- `claude-code`
- `codex`
- `opencode`
- `pi`

If we need `droid`, we should treat it as a separate future watcher and not assume `opensessions` already solved it.

## How `opensessions` Resolves A JSON File Back To A Session

The main mapping is not "pane -> exact transcript file".

It is:

1. watcher reads transcript or remote thread state
2. watcher extracts `projectDir`
3. server resolves `projectDir -> mux session`

Resolution logic lives in [server/index.ts](/tmp/opensessions/packages/runtime/src/server/index.ts#L337).

It first tries exact directory matches, then parent-child prefix matching, and for Claude can also handle encoded path fallback.

This is important for us because the clean abstraction is:

- `Watcher`: parse source-specific transcript or API
- `Resolver`: map `projectDir` to our run or session
- `Normalizer`: emit a small, stable event model

Not:

- "let the core loop understand every agent's file format"

## What `opensessions` Normalizes

The normalized event model is in [contracts/agent.ts](/tmp/opensessions/packages/runtime/src/contracts/agent.ts#L1).

Important statuses:

- `idle`
- `running`
- `tool-running`
- `done`
- `error`
- `waiting`
- `interrupted`
- `stale`

The tracker in [tracker.ts](/tmp/opensessions/packages/runtime/src/agents/tracker.ts) then folds per-agent watcher updates into session-level state.

This is exactly the right pattern for us if we add watchers:

- source-specific parser
- normalized event
- tracker or state fold
- supervisor consumes only normalized signals

## Practical Rules For Us

### Rule 1: Separate "hook payload" from "log barrier"

Hook payload answers:

- what is happening now
- what tool or prompt is involved
- what is the current session, cwd, turn, or last assistant message

It does not automatically answer:

- has the transcript flushed through this point

### Rule 2: Claude hook is not a persistence fence

On Claude:

- use hook payloads for context
- use transcript JSONL for observation
- do not claim strict completeness from either without an explicit internal flush

### Rule 3: Codex rollout can act as a practical file-order fence

On Codex:

- once rollout is materialized
- and a hook or completion event is visible in the rollout
- earlier rollout lines in that file are already written and flushed

This makes Codex much more suitable for transcript-driven watcher logic than Claude.

### Rule 4: Native remote streams are a different class

Amp shows another category:

- no local transcript fence
- status comes from remote WebSocket or API
- completeness is defined by remote protocol semantics, not local file append order

## Recommended Internal Vocabulary

To avoid mixing concepts, we should use three separate terms:

- `Hook Context`
  - in-process payload delivered to the hook command

- `Transcript Observation`
  - lines or rows observed from JSONL, SQLite, or remote API/WebSocket

- `Persistence Fence`
  - an event or API whose semantics justify saying earlier relevant records are already visible

Under that vocabulary:

- Claude `Stop` hook is `Hook Context`, not a `Persistence Fence`
- Codex rollout `HookCompleted` line is both `Transcript Observation` and a practical `Persistence Fence`
- Amp WebSocket state is `Transcript Observation` equivalent, but not a local-file fence

## Bottom Line

If we build a watcher layer on top of our current supervisor:

- `Claude` should be treated as a best-effort transcript observer plus useful hook context
- `Codex` can support stronger transcript-based reasoning because rollout ordering is much tighter
- `Amp` should be treated as a native remote state source, not a local JSON parser
- `opensessions` gives us a good normalization pattern, but not a universal guarantee that "seeing one event means all prior causal state is durably in storage"

The strongest reusable idea from this whole analysis is:

- do not let the control loop depend directly on raw transcript formats
- build per-agent watchers
- normalize to a small lifecycle model
- treat persistence guarantees as source-specific, not universal
