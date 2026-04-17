# Strategy Fragment: Finish Proof

This file is safe to optimize.

## Goal

Produce evidence that helps the finish gate pass on the first verification cycle.

## Guidance

- Make checkpoint evidence concrete: exact files changed, commands run, and short results.
- Use `workflow_done` only when all planned steps are complete and the final verification evidence is already in hand.
- If reviewer-gated completion is configured, tell the user clearly that a review acknowledgement is the next action.
