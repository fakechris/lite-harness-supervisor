# Supervisor Checkpoint Protocol

This project uses `thin-supervisor` to drive long-running tasks with
deterministic verification. When a supervisor is active, you MUST follow
the checkpoint protocol described below.

## Check if supervisor is active

```bash
thin-supervisor status
```

If status shows an active run, follow the protocol below.

When starting a new supervised run in this repository, prefer:

```bash
scripts/lh-supervisor-attach.sh <slug>
```

That script binds the current pane to the generated spec. Do not begin
implementation before it succeeds.

## Checkpoint protocol

After completing meaningful work on a step, output a checkpoint block:

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

### Status values

| Status | When to use |
|--------|-------------|
| `working` | Still making progress on current step |
| `blocked` | Cannot proceed without external input |
| `step_done` | Current step is complete, ready for verification |
| `workflow_done` | All steps complete |

## Rules

1. Do NOT ask "should I continue?" — the supervisor decides
2. Do NOT skip steps or verification
3. Emit checkpoints after every significant action
4. The supervisor will inject next-step instructions when ready
