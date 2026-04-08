# State Machine

## 顶层状态

- `INIT`
- `READY`
- `RUNNING`
- `AWAITING_AGENT_EVENT`
- `GATING`
- `VERIFYING`
- `PAUSED_FOR_HUMAN`
- `COMPLETED`
- `FAILED`
- `ABORTED`

## 当前 node 子状态

- `CURRENT_STEP_PENDING`
- `CURRENT_STEP_RUNNING`
- `CURRENT_STEP_BLOCKED`
- `CURRENT_STEP_DONE`
- `BRANCH_DECISION_PENDING`
- `RETRY_PENDING`
- `ROLLUP_VERIFY_PENDING`

## 决策枚举

- `CONTINUE`
- `RETRY`
- `VERIFY_STEP`
- `ADVANCE_STEP`
- `BRANCH`
- `ESCALATE_TO_HUMAN`
- `FINISH`
- `ABORT`

## 默认策略

只要没有命中以下情况，就默认 `CONTINUE`：

- 缺外部输入
- 缺权限 / 凭证
- 不可逆危险操作
- spec 未定义的关键分歧
- retry budget 耗尽
- verifier 给出 hard fail

## finish 原则

只有同时满足时才能进入 `COMPLETED`：

- 当前 spec 必要节点全部完成
- 所有 required verifier 通过
- 满足 finish policy
