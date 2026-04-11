# Supervision Modes

The supervisor adjusts its intervention intensity based on worker strength,
task risk, and failure history. You do NOT choose the mode — the system
selects it. But understanding the modes helps you work with the supervisor.

## strict_verifier (default)

**When**: Strong or standard trust worker, standard/low risk, fewer than 3 failures.

**What the supervisor does**: Only checks evidence and runs verifiers.
Does NOT give detailed guidance. Trusts you to figure out how.

**What you should do**: Just work. Emit checkpoints. The supervisor will
verify and advance.

## collaborative_reviewer

**When**: Low trust worker, OR high/critical risk task, OR some failures (but below threshold).

**What the supervisor does**: Asks you to describe your approach and risks
before executing.

**What you should do**: When you receive an instruction in this mode, first
briefly explain what you plan to do and what could go wrong. Then do it.

## directive_lead

**When**: Low trust + high/critical risk, OR 3+ consecutive failures (configurable threshold), OR high risk + 2+ failures.

**What the supervisor does**: Gives one specific action at a time. Does not
let you freelance.

**What you should do**: Execute exactly what's asked. Nothing more. Report
results immediately with a checkpoint.

---

**Key principle**: The supervisor defaults to trusting strong workers.
A MiniMax supervisor will NOT micromanage a GPT-5.4 worker. Mode escalation
only happens when evidence (failures, risk) demands it.
