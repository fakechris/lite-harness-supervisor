# Worker Checkpoint Protocol

Load this file when a supervised run is already active and you need the
exact worker execution protocol.

This reference is for the **execution** phase only. Planning, approval,
and attach semantics still come from:

- `references/contract.md`
- `strategy/approval-boundary.md`
- `strategy/finish-proof.md`

## Required checkpoint shape

After meaningful execution progress, emit a checkpoint in this shape:

```text
<checkpoint>
run_id: <run_id>
checkpoint_seq: <incrementing integer>
checkpoint_schema_version: 2
status: <working | blocked | step_done | workflow_done>
current_node: <step_id>
summary: <one-line description>
progress_class: <execution | verification | admin>
evidence_scope: <current_node | prior_phase | unknown>
escalation_class: <none | business | safety | review>
requires_authorization: <true | false>
blocking_inputs:
  - <missing input, or leave empty>
reason_code: <esc.* | rec.* | ver.* | sem.* | empty>
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

## Status values

| Status | When to use |
| --- | --- |
| `working` | still making progress on the current node |
| `blocked` | genuinely need external input you cannot obtain yourself |
| `step_done` | current node is complete and ready for verification |
| `workflow_done` | all steps are complete |

## Structured semantic fields

- `checkpoint_schema_version: 2`
  - required when you fill any structured semantic fields below
- `progress_class`
  - `execution`: concrete work on the current node
  - `verification`: running a test or acceptance check
  - `admin`: clarify / plan / spec / attach / review artifacts only
- `evidence_scope`
  - `current_node`: evidence comes from this node
  - `prior_phase`: evidence comes from earlier phases
  - `unknown`: you cannot attribute it yet
- `escalation_class`
  - `none`: continue normally
  - `business`: real missing credential / artifact / human decision
  - `safety`: dangerous or irreversible action needs authorization
  - `review`: completion proof is ready and a human must sign off
- `requires_authorization: true`
  - only when a safety-class action is pending
- `blocking_inputs`
  - list concrete missing items for a business escalation
- `reason_code`
  - use one of the frozen `esc.*`, `rec.*`, `ver.*`, `sem.*` codes

## First-checkpoint rule

The **first** checkpoint for a newly injected `current_node` must cite
real work on that node:

- a command you actually ran
- a file you actually modified
- a verifier or test result

The following do **not** count as execution evidence for a new node:

- clarify notes
- spec files
- plan review artifacts
- attach success
- baseline checks from earlier phases

If the truthful answer is "I only have prior-phase/admin work so far",
emit:

- `progress_class: admin`
- `evidence_scope: prior_phase`

Do **not** inflate that into `execution`.

## Continue / block / finish rules

- Do not ask "should I continue?" The supervisor decides.
- Do not skip verification. Every node must satisfy its `verify` checks.
- Keep `current_node` aligned with the active spec node.
- Use `blocked` only for genuine external blockers, not pane or delivery faults.
- If the supervisor pauses or escalates, stop forward progress until the next instruction or human intervention.
