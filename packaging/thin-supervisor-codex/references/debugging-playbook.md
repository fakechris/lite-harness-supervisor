# When Verification Fails

You received a retry instruction because the verifier rejected your work.
Follow this process — do NOT blindly retry the same approach.

## Step 1: Read the failure details

The instruction includes "Previous verification failed: ..." with specifics.
Read them carefully. Common failures:

- **command: pytest failed** → read the test output, find the actual assertion error
- **artifact: file not found** → check spelling, path, and whether you actually created it
- **git: repo dirty** → commit or stash your changes

## Step 2: Diagnose root cause

Before retrying, ask yourself:
- Did I misunderstand the objective?
- Did I implement the wrong thing?
- Is the verification command wrong? (If so, do NOT modify the spec — escalate)

## Step 3: Fix and verify locally

Run the verification command yourself before emitting `step_done`:
```bash
# Whatever the spec says in verify.run
pytest -q tests/test_X.py
```

If it passes locally, emit `step_done`. If it doesn't, keep working.

## Step 4: If stuck after 2 retries

If you've tried twice and still can't pass verification:
- Emit `status: blocked` with a clear explanation in `needs`
- The supervisor will escalate to a human
- Do NOT keep retrying the same approach
