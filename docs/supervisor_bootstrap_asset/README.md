# Thin Supervisor Bootstrap Asset

这是一套面向 **长任务连续执行** 的最小开发资产，目标是让 Codex 能直接按包开工，而不是从零理解需求。

## 这套资产解决什么问题

目标问题很窄：

- 已经有明确 plan / workflow
- 默认希望大模型持续执行，不要频繁向人确认
- 只有在缺权限、缺外部输入、分支不确定、危险操作、或重试预算耗尽时，才升级给人
- 最终完成不能靠“口头说完了”，必须经过 verifier

## V1 范围（必须先做）

只做四件套：

1. `spec schema`
2. `state.json`
3. `continue_gate`
4. `verifier_suite`

在此基础上补齐最小运行框架：

- spec loader
- state machine
- event/log store
- transcript-based runner adapter
- CLI 入口
- 示例 spec
- 最小测试

## 关键不变量

1. 执行模型不得直接向最终用户求确认
2. 小模型不得修改 repo
3. `finish` 必须经过 verifier
4. 分支只能在 spec 允许集合中发生
5. 默认策略是 `continue`
6. 所有 `escalate` 必须输出 machine-readable reason

## 目录速览

- `docs/`：面向实现者的说明
- `supervisor/`：最小 Python scaffold
- `specs/examples/`：线性计划和条件工作流示例
- `runtime/`：本地运行状态样例
- `tests/`：最小测试

## 推荐启动顺序

1. 先读 `docs/05-codex-build-contract.md`
2. 再读 `docs/04-spec-schema.md`
3. 之后按 `docs/06-implementation-plan.md` 开发
4. 先跑通 `spec -> state -> continue_gate -> verifier_suite`
