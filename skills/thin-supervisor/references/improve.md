# After a Run Completes: Learn and Improve

When a supervised run finishes (COMPLETED or PAUSED_FOR_HUMAN), analyze
what happened and propose improvements.

## What to analyze

Read `.supervisor/runtime/runs/<run_id>/session_log.jsonl` and look for:

1. **Retries**: Which steps had `current_attempt > 0`? Why did verification fail?
2. **Escalations**: Were there `routing` events? Could they have been avoided?
3. **Mismatches**: Were there `checkpoint_mismatch` events? Why was the agent out of sync?
4. **Time distribution**: Which steps took the most iterations?

## What to propose

Based on patterns found:

- **Spec improvements**: "Step 3's objective was ambiguous — suggest rewording to X"
- **Verification improvements**: "Step 2's verify command always passes — suggest using X instead"
- **Skill improvements**: "Agents keep asking for confirmation at step 4 — add explicit guidance"
- **Escalation rules**: "This type of error was escalated but could have been auto-resolved"

## How to apply

Write proposed changes as a note:
```bash
thin-supervisor note add "Spec improvement: step 3 objective should be ..." --type finding
```

These notes are visible to the user and to other runs via `thin-supervisor note list`.
