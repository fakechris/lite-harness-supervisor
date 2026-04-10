# P3 Design: Multi-Source Observation (TODO — Not Implemented)

> Status: **Design only — future work**

## Motivation

Terminal/transcript is currently the only observation source. Provider-native
events (Codex hooks, Claude SDK events) are higher quality and more structured.
The system should support multiple observation sources with priority layering.

## Proposed ObservationSource Abstraction

```python
class ObservationSource(Protocol):
    source_type: str  # "terminal" | "provider_hook" | "relay_event" | "manual"
    priority: int     # higher = more trusted
    
    def poll(self) -> list[CheckpointEvent]: ...
    def is_available(self) -> bool: ...
```

## Source Priority (highest first)

1. **provider_hook** — Codex/Claude native events (structured, reliable)
2. **relay_event** — open-relay session events (structured)
3. **terminal_transcript** — tmux capture-pane + checkpoint parsing (current)
4. **manual_note** — human-injected observations

## Integration Points

- `CheckpointEvent` already has `surface_id` — add `observation_source` field
- `TranscriptAdapter` becomes one implementation of ObservationSource
- Future: Codex `post_tool_use` hook → structured checkpoint without parsing
- Future: Claude Code `Stop` hook → supervisor decision without polling

## Key Principle

Terminal remains as fallback and human-visible surface.
Provider-native events, when available, are the primary truth source.
The system should not require provider hooks to function — terminal-only
mode must always work.

## When to Build This

After provider hook ecosystems stabilize. Currently:
- Codex hooks: `pre_tool_use`, `post_tool_use`, `session_start`, `stop`
- Claude Code hooks: `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`

Both support `command` type hooks that could call `thin-supervisor` to
report events. The missing piece is a reliable, structured event format
from these hooks that doesn't depend on terminal text parsing.

## Not in Scope

- Real-time streaming from provider APIs
- WebSocket/SSE integration
- Full SDK-driven orchestration (contradicts our "sidecar" model)
