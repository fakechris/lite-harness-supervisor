# Implementation Plan

## Phase 1: Spec + State

1. 实现 `domain/models.py`
2. 实现 `plan/loader.py`
3. 实现 `storage/state_store.py`
4. 写 `tests/test_spec_loader.py`
5. 写 `tests/test_state_store.py`

## Phase 2: Continue Gate

1. 实现 `gates/rules.py`
2. 实现 `llm/judge_client.py` stub
3. 实现 `gates/continue_gate.py`
4. 写 `tests/test_continue_gate.py`

## Phase 3: Verifier Suite

1. 实现 `verifiers/command_verifier.py`
2. 实现 `verifiers/artifact_verifier.py`
3. 实现 `verifiers/git_verifier.py`
4. 实现 `verifiers/workflow_verifier.py`
5. 实现 `verifiers/suite.py`
6. 写 `tests/test_verifier_suite.py`

## Phase 4: Main Loop

1. 实现 `events/event_types.py`
2. 实现 `events/bus.py`
3. 实现 `adapters/transcript_adapter.py`
4. 实现 `loop.py`
5. 实现 `app.py`
6. 写 `tests/test_integration_minimal.py`

## Phase 5: Polish

1. 增加示例 spec
2. 增加 `runtime/state.example.json`
3. 清理日志格式
4. 写 `README` 使用方法

## 非目标

- 真正的 branch classifier 完整实现
- 并行 worker
- Web UI
- PR / Deploy gate
- 多仓库协调
