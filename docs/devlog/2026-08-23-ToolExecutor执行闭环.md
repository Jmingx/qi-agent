# 2026-08-23-ToolExecutor执行闭环

## 做了什么

- **方案评审通过**：`docs/plans/2026-08-23-ToolExecutor执行闭环方案.md`
  （编排层 vs 执行层职责分离）
- **新建 `qi_agent/tools/executor.py`**：ToolExecutor 执行闭环
  - 阶段1 审批分发（NEED_APPROVAL/ESCALATION → agent/tool-approval bail，
    fail-closed 拒绝）
  - 阶段2 线程池并发执行（只 execute_tool，tool/start 事件主线程发）
  - 阶段3 结果封装（BLOCK→[安全拦截] / 审批拒绝→[审批拒绝] / WARN→执行+后缀）
    + agent/tool-result 事件（事件名不变，发出位置从 agent 移到 executor）
- **agent.py 编排层瘦身**：删除审批分发/并发执行/结果封装逻辑（~100 行），
  step() 改为：事件点 → executor.execute → 回填。292 行 → 220 行
- **测试**：`tests/test_tool_executor.py`（11 个：审批路由/档位/并发/失败聚合/事件）
  + `test_parallel_actually_concurrent` patch 位置迁移

## 遇到的问题与解决

1. **工具注册 schema 自动生成**：`**kw` 被 inspect 当成必填参数 `kw`——
   手写 schema 声明测试用参数名解决（回显工具）
2. **测试 patch 位置**：execute_tool 下沉后 patch 路径从
   `qi_agent.agent` 移到 `qi_agent.tools.executor`
3. **验收断言选错命令**：`rm -rf C:/` 是审批档不是红线——改用 `format C:` 验证
   [安全拦截] 链路（行为本身正确）
4. **FakeClient 缺 assistant_message**：真实 LLM 会构造完整 assistant 消息，
   测试替身必须同样完整（协议要求），否则历史出现 `{}` 消息

## 下一步计划

- 提交（refactor: ToolExecutor 执行闭环 + 测试 + 归档）
- 待定：决策码机制提交合并、/compact 手动命令、L3 长对话事实保持评测
