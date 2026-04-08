# Codex Build Contract

这份文档是给 Codex 的开发合同。请严格按这里执行，不要擅自扩 scope。

## 目标

实现一个 V1 可运行的薄 supervisor，使其能：

1. 读取 spec
2. 初始化 / 恢复 `runtime/state.json`
3. 接收执行模型的 checkpoint / stop 事件
4. 通过 `continue_gate` 决定默认继续还是升级
5. 通过 `verifier_suite` 对当前 node 做确定性验证
6. 在 verifier 通过后推进到下一 node
7. 记录 event / decision log

## 绝对禁止

- 不要引入多 agent 编排
- 不要引入复杂数据库
- 不要引入消息队列
- 不要把小模型做成业务实现者
- 不要让 finish 只依赖模型口头陈述
- 不要把 branch 系统做重（V1 只做最小接口和示例）

## 实现要求

### A. 代码要求

- Python 3.11+
- 以标准库为主，只允许 `PyYAML`
- 所有关键 decision 都必须返回结构化 dict / dataclass
- 所有状态持久化到 `runtime/`

### B. 功能要求

#### 1. spec loader
- 支持 `linear_plan`
- 支持 `conditional_workflow` 的最小解析
- 对缺失字段给出清晰错误

#### 2. state store
- `load_or_init`
- `save`
- `append_event`
- `append_decision`

#### 3. continue gate
必须先规则，后小模型：
- 先用规则识别 soft confirmation / missing authority / missing external input / dangerous action
- 规则不能定论时，再调用 `JudgeClient`
- `JudgeClient` 先保留 stub，返回 JSON

#### 4. verifier suite
至少实现：
- command verifier
- artifact verifier
- git verifier
- workflow verifier

#### 5. main loop
- 能从 spec 启动
- 能基于 checkpoint 推进状态
- 能在 verifier pass 后推进到下一 node
- 能在 retry budget 超限时进入 `PAUSED_FOR_HUMAN`

## 完成定义

以下测试全部通过即算 V1 完成：

- spec loader tests
- state store tests
- continue gate tests
- verifier suite tests
- minimal integration test
