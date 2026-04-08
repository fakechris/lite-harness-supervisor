# Spec Schema

V1 统一支持两类 spec：

- `linear_plan`
- `conditional_workflow`

## 通用顶层字段

```yaml
kind: linear_plan | conditional_workflow
id: unique-string
goal: human-readable goal
finish_policy:
  require_all_steps_done: true
  require_verification_pass: true
  require_clean_or_committed_repo: false
policy:
  default_continue: true
  max_retries_per_node: 3
  max_retries_global: 12
```

## linear_plan

```yaml
steps:
  - id: task1
    type: task
    objective: do something
    depends_on: []
    outputs: []
    verify:
      - type: command
        run: pytest -q tests/test_x.py
        expect: pass
```

## conditional_workflow

```yaml
nodes:
  - id: inspect_screen
    type: task
    objective: inspect current screen
    next: applicability_gate

  - id: applicability_gate
    type: decision
    decision_mode: llm_plus_rules
    options:
      - id: applicable
        next: do_workflow
      - id: skip
        next: mark_skipped
```

## verify schema

支持类型：

### command
```yaml
- type: command
  run: PYTHONPATH=. python -m pytest -q
  expect: pass
```

`expect` 支持：
- `pass`
- `fail`
- `contains:<text>`

### artifact
```yaml
- type: artifact
  path: tests/test_paddle_canonical_consumption.py
  exists: true
```

### git
```yaml
- type: git
  check: dirty
  expect: true
```

### workflow
```yaml
- type: workflow
  require_node_done: true
```

## checkpoint 协议

执行模型每一轮结束必须输出：

```text
<checkpoint>
status: working | blocked | step_done | workflow_done
current_node: node_id
summary: one-line summary
evidence:
  - modified: path
  - ran: command
  - result: short result
candidate_next_actions:
  - ...
needs:
  - none
question_for_supervisor:
  - none
</checkpoint>
```

这是强约束，不是建议。
