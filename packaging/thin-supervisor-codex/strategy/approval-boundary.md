# Strategy Fragment: Approval Boundary

This file is safe to optimize.

## Goal

Detect when the user has moved from review into execution authorization.

## Default Heuristics

- Treat terse approvals such as `可以`, `同意`, `开始吧`, `go ahead`, `ship it`, `approved` as approval when the immediate prior context was an approval request.
- Treat `先给我看`, `先改`, `wait`, `not yet`, `再看看` as non-approval.
- If the user repeats approval after friction, accept it and move forward. Do not ask again.

## When Unsure

- Prefer one narrow clarify question before drafting the spec.
- Prefer user approval over over-cautious re-asking once the spec is already in `draft` state and the user says to proceed.
