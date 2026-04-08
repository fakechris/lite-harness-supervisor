# Architecture

## 核心设计

统一抽象：

`Workflow Runtime = Spec + Executor + Gate + Verifier + Event Loop`

### 数据面：大模型（Executor）

负责：
- 阅读仓库与文档
- 修改代码 / 文档
- 跑命令
- 输出 checkpoint
- 给出候选下一步与判断依据

### 控制面：Supervisor + 小模型（Judge）

负责：
- 是否默认继续
- 是否验证当前阶段
- 是否推进下一阶段
- 是否需要走分支
- 是否需要升级给人
- 是否可以结束

### 确定性面：Verifier

负责：
- 命令结果检查
- 产物存在性检查
- git 检查
- workflow 完成性检查

## 运行流

1. 载入 spec
2. 初始化 / 恢复 state
3. 让执行模型执行当前 node
4. 解析 checkpoint / transcript / stop
5. 进入 gating
6. gate 决定 continue / verify / branch / escalate / finish
7. verifier 运行
8. 推进到下一 node 或结束

## V1 技术策略

- 优先 transcript adapter，不强依赖 runtime native hooks
- 小模型只做 JSON 决策，不做 repo 修改
- verifier 先支持 command / artifact / git / workflow 四类
