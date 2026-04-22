# P4 Design: JSONL Transcript Observation Mode

> Status: **Implemented** (observation + Stop-hook injection).

## Motivation

The terminal-based observation model (tmux capture-pane) requires users to
run inside tmux. The JSONL observation model reads the agent's native
transcript files directly — no tmux needed.

## Architecture

```
Agent (Codex / Claude Code) → writes JSONL transcript
                                    ↓ file system (read-only)
thin-supervisor daemon → JsonlObserver tails JSONL
                                    ↓ parse checkpoints from events
                        gate → verify → decision
                                    ↓ inject via hook / file handoff
```

## Observation: Reading JSONL

**Codex transcripts**: `~/.codex/sessions/YYYY/MM/DD/rollout-{timestamp}-{uuid}.jsonl`
- `session_meta.payload.cwd` — project directory
- `turn_context.payload.cwd` — per-turn directory
- `event_msg.payload.content` / `response_item.payload.content` — agent output

**Claude Code transcripts**: `~/.claude/transcripts/ses_{id}.jsonl`
- `tool_result.payload.content` — tool output (contains checkpoints)
- `tool_use.payload.tool_input` — what was executed
- cwd derived from `~/.claude/projects/<encoded-path>/` directory name

## Session Discovery: Skill-Driven

The Skill runs inside the agent process and knows the current session:

```bash
SESSION_ID="$(thin-supervisor session detect)"
JSONL_PATH="$(thin-supervisor session jsonl)"
thin-supervisor run register --spec ... --session-jsonl "$JSONL_PATH"
```

Daemon does NOT scan for sessions. Skill tells daemon which JSONL to watch.

## Injection: Stop-Hook Handoff (Implemented)

The supervisor writes the next instruction to a per-session handoff file, and
the agent's Stop hook picks it up and returns it as the hook `reason`.

```
Supervisor loop (observation-only surface)
  → JsonlObserver.inject_with_id(content, instruction_id=…)
  → writes .supervisor/runtime/instructions/<session_id>.json
  → polls .supervisor/runtime/instructions/<session_id>.delivered.json

Agent completes a turn → Stop hook fires
  → `thin-supervisor hook stop`
  → reads pending instruction for the auto-detected session
  → prints content to stderr + exit 2  (Claude Code / Codex `reason` convention)
  → writes the `.delivered.json` ACK atomically
  → agent treats the stderr text as its next step and keeps working
```

Delivery state transitions on an observation-only surface:

| Step                         | `delivery_state` |
| ---------------------------- | ---------------- |
| Loop writes handoff          | `INJECTED`       |
| Stop hook writes ACK         | `ACKNOWLEDGED`   |
| Agent emits next checkpoint  | `STARTED_PROCESSING` |
| Timeout with no ACK          | `TIMED_OUT`      |

The ACK window defaults to ``OBSERVATION_HOOK_ACK_TIMEOUT_SEC`` (10 min) since
the hook fires only when the agent voluntarily stops. On timeout, the run
pauses for human attention instead of silently spinning.

### Setup

Install the hook once per workstation:

```bash
thin-supervisor hook install            # both agents (default)
thin-supervisor hook install --agent claude
thin-supervisor hook install --agent codex
```

This merges the Stop entry into `~/.claude/settings.json` /
`~/.codex/hooks.json` — idempotent, and preserves any existing hooks.
`thin-supervisor hook uninstall` removes only the supervisor entry.

### File-handoff contract

`instruction.v1` (supervisor → hook):

```json
{
  "schema": "instruction.v1",
  "instruction_id": "...",
  "run_id": "...",
  "node_id": "...",
  "content": "text the agent should read",
  "content_sha256": "…",
  "written_at": "2026-04-21T10:00:00Z"
}
```

`instruction_ack.v1` (hook → supervisor):

```json
{
  "schema": "instruction_ack.v1",
  "instruction_id": "...",
  "content_sha256": "...",
  "session_id": "...",
  "delivered_at": "2026-04-21T10:00:05Z"
}
```

Files are written atomically (`tempfile` + `os.replace`). The hook is a no-op
(exit 0) when no instruction is pending and no supervisor run is active in
the cwd, so enabling it globally is safe.

## What's NOT in Scope

- Real-time JSONL streaming (inotify/kqueue) — polling is sufficient
- Direct stdin injection to agent process — too fragile
- Replacing tmux mode — JSONL is an additional surface type, not a replacement
