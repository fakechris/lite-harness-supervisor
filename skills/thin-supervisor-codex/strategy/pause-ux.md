# Strategy Fragment: Pause UX

This file is safe to optimize.

## Goal

Make supervisor pauses and completions legible to the human operator.

## Guidance

- When the supervisor pauses for human input, state the pause reason and the next command if it is visible.
- When a run completes, state that the supervisor completed verification rather than only saying the task is done.
- If a run is still `RUNNING`, do not imply it is paused just because the pane is quiet.
