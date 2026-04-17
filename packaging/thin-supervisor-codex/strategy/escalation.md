# Strategy Fragment: Escalation

This file is safe to optimize.

## Goal

Escalate only when forward progress genuinely requires it.

## Guidance

- Use `blocked` for missing credentials, product decisions, or unavailable external systems.
- Do not escalate just because a step is large. Keep working and emit `working` checkpoints.
- If verification fails, follow the supervisor retry instruction instead of inventing a new branch.
- If the supervisor reports a node mismatch or asks for recovery, realign to the active node before continuing.
