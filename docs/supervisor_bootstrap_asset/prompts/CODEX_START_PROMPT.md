# Codex Start Prompt

你要实现的是一个 **thin supervisor**，不是完整 agent platform。

请严格按以下目标实现，不要跑偏：

## 产品目标

实现一个本地、薄、可恢复的 supervisor，用于驱动长任务持续执行。
默认自动继续，只有在缺外部输入、缺权限、危险操作、分支不确定、或重试预算耗尽时才升级给人。

## V1 范围

只实现：

1. spec schema
2. runtime state.json
3. continue_gate
4. verifier_suite
5. main loop 的最小闭环
6. tests

不要实现：
- 多 agent
- Web UI
- 数据库
- 复杂 branch engine
- deploy / PR orchestration

## 关键不变量

1. 执行模型不得直接向用户求确认
2. 小模型不得修改 repo
3. finish 必须经过 verifier
4. branch 只能发生在 spec 允许的 options 中
5. 默认策略是 continue
6. escalate 必须带 machine-readable reason

## 第一批工作

按以下顺序开发：

### Phase 1
- 完善 `domain/models.py`
- 完善 `plan/loader.py`
- 完善 `storage/state_store.py`
- 让 `tests/test_spec_loader.py` 与 `tests/test_state_store.py` 通过

### Phase 2
- 完善 `gates/rules.py`
- 完善 `llm/judge_client.py` stub 接口
- 完善 `gates/continue_gate.py`
- 让 `tests/test_continue_gate.py` 通过

### Phase 3
- 完善 `verifiers/*.py`
- 完善 `verifiers/suite.py`
- 让 `tests/test_verifier_suite.py` 通过

### Phase 4
- 完善 `loop.py`
- 完善 `app.py`
- 让 `tests/test_integration_minimal.py` 通过

## 输出要求

- 先补齐代码，再跑测试，再修测试
- 每轮迭代结束输出一个简短 checkpoint
- 不要引入额外重依赖
- 不要改变目录结构，除非确有必要且写明原因

## 完成定义

以下命令通过：

```bash
python -m pytest -q
```
