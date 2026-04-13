# Protocol, Recovery, and Trust Hardening

## What changed

This hardening pass addressed three structural problems that were still showing
up as many separate bugs:

1. Checkpoint protocol had multiple drifting representations.
2. Recovery semantics were too optimistic for observation-only and mid-flight
   resume paths.
3. The trust boundary between judge output, checkpoint text, and finish-gate
   evidence was too soft.

## Checkpoint protocol: single schema

Added [supervisor/protocol/checkpoints.py](../../supervisor/protocol/checkpoints.py)
as the canonical checkpoint schema module.

It now owns:

- allowed status values
- example checkpoint block
- instruction text sanitization
- checkpoint payload sanitization and normalization

Consumers updated:

- [supervisor/instructions/composer.py](../../supervisor/instructions/composer.py)
- [supervisor/adapters/transcript_adapter.py](../../supervisor/adapters/transcript_adapter.py)
- [supervisor/llm/prompts/checkpoint_protocol.txt](../../supervisor/llm/prompts/checkpoint_protocol.txt)

Result:

- parser rejects unknown checkpoint statuses
- YAML-style structured evidence is normalized into one canonical string form
- composer and prompt template are aligned against one example block

## Recovery contract: less optimistic, more explicit

### Observation-only surfaces

Previously, observation-only surfaces wrote a file and pretended injection had
"worked". That was a false recovery path.

Now:

- `injection_observation_only` is still recorded
- but the run pauses because delivery cannot be confirmed

File:

- [supervisor/loop.py](../../supervisor/loop.py)

### Resume

Resume now rejects states that are not safely restartable:

- `GATING`
- `VERIFYING`

It also rejects legacy persisted runs with no `spec_hash`, because the daemon
cannot prove that the spec being resumed is the same contract that created the
run.

File:

- [supervisor/daemon/server.py](../../supervisor/daemon/server.py)

## Trust boundary hardening

### Judge output

`JudgeClient` now sanitizes and validates model output before it is reused as
control data:

- continue/finish judges can only return whitelisted decisions
- malformed or fenced JSON still parses, but invalid decisions fall back to
  the conservative stub
- `next_instruction` is stripped if it contains control markers such as
  `<checkpoint>`

File:

- [supervisor/llm/judge_client.py](../../supervisor/llm/judge_client.py)

### FinishGate evidence

`FinishGate` no longer concatenates all evidence into one giant substring
search space. Required evidence must now match within a single normalized
evidence entry.

This does not make checkpoint evidence trusted, but it removes the weakest
cross-entry spoofing path.

File:

- [supervisor/gates/finish_gate.py](../../supervisor/gates/finish_gate.py)

## Remaining work

The biggest remaining structural items are:

1. Replace ad hoc observation-only delivery with an explicit acknowledged hook
   path.
2. Continue separating trusted verifier output from agent-authored checkpoint
   text in acceptance and escalation logic.

`NodeStatus` has now been removed from the live runtime model instead of being
kept as a dead secondary state axis.
