# How to Write Good Specs

A spec defines what to do and how to verify it. Bad specs cause retries.
Good specs complete on the first pass.

## Rules for objectives

- **Concrete**: "write failing tests for the auth module" not "improve testing"
- **Actionable**: one clear deliverable per step
- **Verifiable**: must have at least one verify entry that actually proves completion

## Rules for verification

- **Command verifiers**: the command must exit 0 on success. `pytest -q tests/test_X.py` is good. `echo "done"` is useless.
- **Artifact verifiers**: check for specific files that the step must create.
- **Don't use verification that always passes**: `python -c "print('ok')"` proves nothing.
- **Test before you spec**: if you're not sure the verify command works, run it yourself first.

## Common mistakes

- **Too many steps**: 3-7 steps is ideal. 15 steps means your objectives are too granular.
- **Too few steps**: 1 step with "do everything" is not a plan.
- **Missing dependencies**: if step 3 requires step 2's output, the order must reflect that.
- **Verification that doesn't verify**: `exists: true` on a file that already exists proves nothing.

## Template

```yaml
steps:
  - id: <snake_case_name>
    type: task
    objective: <one sentence, concrete, actionable>
    verify:
      - type: command
        run: <command that exits 0 only if step is done>
        expect: pass
```
