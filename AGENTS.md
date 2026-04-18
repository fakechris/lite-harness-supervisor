# thin-supervisor repo

This repository implements the `thin-supervisor` runtime, skills, protocol
references, and operator tooling.

## Before supervised work

- Check run state with `thin-supervisor status`
- Prefer `scripts/thin-supervisor-attach.sh <slug>` when starting a new supervised task
- Do not begin implementation before attach succeeds

## If a supervised run is active

If `thin-supervisor status` shows an active run for this pane/project:

- load [skills/thin-supervisor/references/worker-checkpoint-protocol.md](/Users/chris/workspace/lite-harness-supervisor/skills/thin-supervisor/references/worker-checkpoint-protocol.md)
- follow the checkpoint protocol exactly
- do not skip verification or invent your own control flow
