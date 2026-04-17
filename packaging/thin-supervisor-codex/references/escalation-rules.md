# When to Escalate vs. Continue

## ESCALATE to human when:

- Agent genuinely needs credentials, API keys, or external access it cannot obtain
- Agent proposes irreversible destructive actions (delete production data, drop tables, force push to main)
- Agent is genuinely stuck — not making progress, repeating the same failed approach
- Retry budget is exhausted (3+ failures on same step)
- Evidence is insufficient to determine if the task is actually complete

## CONTINUE (do NOT escalate) when:

- Agent asks "should I continue?" or "is this OK?" — that's a soft confirmation. Just continue.
- Agent reports intermediate progress — just continue.
- Agent makes a minor mistake — let the verifier catch it and retry.
- Agent asks a question that can be answered from the codebase — explore first, don't escalate.

## BLOCKED vs. WORKING

If the agent emits `status: blocked`, that means it genuinely cannot proceed.
Escalate immediately — don't wait for retry.

If the agent emits `status: working`, that means it's making progress.
Do not interrupt.

## Key judgment

The hardest case: agent says "I think this is done" but hasn't run verification.
**Do NOT accept verbal completion.** Gate to VERIFY_STEP and let the verifier
decide. "Done" means the verifier passed, not that the agent said so.
