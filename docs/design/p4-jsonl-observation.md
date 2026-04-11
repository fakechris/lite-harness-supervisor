# P4 Design: JSONL Transcript Observation Mode

> Status: **Implemented (observation side) / In Progress (injection side)**

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

## Injection: Two Approaches

### Approach A: Hook Callback (Primary)

```
Agent completes work → Stop hook fires
  → thin-supervisor check-and-respond
  → reads JSONL, parses checkpoint
  → if continue: returns instruction as hook reason
  → agent sees reason, continues working
```

### Approach B: File Handoff (Fallback)

```
thin-supervisor writes .supervisor/runtime/next_instruction.txt
Skill tells agent: "after each step, check this file"
Agent reads instruction, continues
```

## What's NOT in Scope

- Real-time JSONL streaming (inotify/kqueue) — polling is sufficient
- Direct stdin injection to agent process — too fragile
- Replacing tmux mode — JSONL is an additional surface type, not a replacement
