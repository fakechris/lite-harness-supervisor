# Frozen Contract

These rules are execution contracts, not optimization targets.

## Must Always Hold

- Treat explicit user approval as final. Do not ask for the same approval twice.
- Do not begin implementation before `thin-supervisor bootstrap` and `thin-supervisor run register` succeed.
- Do not ask "should I continue?" The supervisor decides.
- Do not skip verification. Every step must satisfy its `verify` checks before moving on.
- Do not modify the generated spec during execution unless the user explicitly asks to revise the plan.
- Emit checkpoint blocks after meaningful progress, blockers, step completion, and workflow completion.
- Keep `current_node` aligned with the active spec node. Never jump ahead in checkpoints.
- The **first checkpoint for a newly-injected node** must cite evidence of concrete work on that node's objective — e.g. a command run, a file changed, a verifier result. Clarify, plan, spec, attach, or baseline-check artifacts do not count as execution evidence for a newly-injected node.

## Approval Contract

- A clarified draft spec still requires user approval unless the user explicitly said to run without asking.
- Once approval is given, approve the spec and attach immediately. Do not re-open clarify or re-ask for consent.

## Safety Contract

- If you are blocked on missing information or external input, emit `status: blocked`.
- If the supervisor pauses or escalates, stop making forward progress until the next supervisor instruction or user intervention.
