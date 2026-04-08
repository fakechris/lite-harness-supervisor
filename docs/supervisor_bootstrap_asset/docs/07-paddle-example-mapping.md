# Paddle Example Mapping

这个例子说明如何把现有 Paddle 长计划映射到 supervisor spec。

## 对应关系

你现有的实现计划天然适合 `linear_plan`：

- Task 1: 锁定 canonical contract 的 failing tests
- Task 2: 加 canonical tables 和 store methods
- Task 3: 加 canonical refresh builder
- Task 4: 加 company_master
- Task 5: 加 company_document_profile
- Task 6: 加 analysis-ready output
- Task 7: 刷新文档
- Task 8: 端到端验证和 commit

每个 task 都有：
- 明确目标
- 文件列表
- 验证命令
- expected pass/fail
- 最终收敛条件

这正是 supervisor 最适合的任务形态。

## 落地建议

- 先不要让 supervisor 自动 commit
- 先让 finish policy 只要求所有 node done + verification pass
- commit gate 作为 V2 再加
