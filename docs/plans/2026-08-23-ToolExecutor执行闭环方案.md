# ToolExecutor 执行闭环方案

> 日期：2026-08-23
> 状态：待评审
> 关联：`2026-08-23-工具决策码机制方案.md`（ToolDecision 流转）、`2026-08-22-并行工具调用方案.md`

## 1. 问题

`agent.py` 的 `step()` 循环里混了 4 层职责：

```
for call in result.tool_calls:
    # ① 事件点（tool-call bail 短路决策）     ← 编排语义
    decision = self.events.bail("agent/tool-call", ...)
    # ② 审批分发（按 action 分支：BLOCK/WARN/NEED_APPROVAL/ESCALATION）
    # ③ 并发执行（run_concurrently 并发任务）
    # ④ 结果处理（success/error/truncated 回填）
```

问题：

- **职责不分**：编排（路由）与执行（怎么做）全糊在一个方法里
- **不可单测**：审批分支/并发策略/结果封装都要经过 agent 循环才能测
- **并发策略耦合**：顺序/并行/异步的改动要改 agent.py

类比（Java）：Controller 里既做路由又写 Service 业务逻辑又拼 SQL——编排与实现全糊在一个类。

## 2. 业界做法

| 项目 | 组织方式 |
|---|---|
| **Hermes** | Agent 循环发事件（on_tool_call），工具执行走工具管理模块（execute_tool），agent 只收集 ToolResult |
| **DSH** | 工具执行完全在 tool 层（execution engine），agent 循环只拿 ToolResult 对象 |
| **Claude Code** | 工具执行在 tool use manager，agent 只决定"下一个动作" |

共识：**事件点（agent 发）→ 执行（tool 层闭环：审批 → 执行 → 结果封装）**。

## 3. 目标设计

```
agent.py（编排层，只留）：
  for call in result.tool_calls:
      decision = self.events.bail("agent/tool-call", name=..., arguments=...)  ← 事件点（短路决策）
      results = self.tool_executor.execute(decision, calls)   # 审批/并发/结果全下沉
      # 只收 ToolResult 列表 → 拼接回填

tool/executor.py（新，执行闭环）：
  class ToolExecutor:
      def execute(self, decision, calls) -> list[ToolResult]:
          ├─ 审批分发（按 decision.action / decision.code 路由）——从 agent 迁入
          ├─ 并发执行（run_concurrently）——从 agent 迁入
          ├─ 结果处理（success/error/truncated 封装）——从 agent 迁入
          └─ 执行期间发 tool/start、tool/result 事件（观测用，不短路）
```

### 3.1 事件点区分（核心设计）

| 事件 | 语义 | 归属 |
|---|---|---|
| `agent/tool-call` | **编排语义**："要不要做"（bail 短路决策） | **留 agent** |
| `tool/start` | 执行生命周期："开始做了"（观测） | tool 层发 |
| `tool/result` | 执行生命周期："做得怎么样"（观测/记录） | tool 层发 |
| `agent/step-end` | 编排语义："这一步结束"（策略链挂载） | **留 agent** |

**关键**：`bail`（短路决策）是编排语义，必须留 agent；`start/result` 是执行生命周期，tool 层发——两者不冲突，各司其职。

### 3.2 ToolExecutor 依赖注入

```
ToolExecutor(executor_registry, approval_gate?, events)
  ├─ executor_registry：工具注册表（name → 可调用）
  ├─ approval_gate：审批插件（可空，fail-closed）
  └─ events：事件总线（发 tool/start、tool/result）
```

- 复用现有 `events` 总线（不新建抽象层，D1）
- approval_gate 可空：无审批插件时 NEED_APPROVAL/ESCALATION 档直接拒绝（保持 fail-closed）

## 4. 决策点

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 是否引入新的抽象层（如 ToolManager 接口） | **否**——ToolExecutor 直接用现有组件 | 已有事件总线/注册表，再加抽象层是过度设计；等第二实现出现再抽象 |
| D2 | 事件语义二分 | **是**——tool-call 留 agent，start/result 在 tool 层 | 编排与执行生命周期的边界即于此；插件观测用 start/result，干预用 tool-call |
| D3 | 并发执行失败策略 | **聚合**——单工具失败不中断整体，结果携带 error 标记 | 与现状一致（run_concurrently 语义），避免行为变更 |
| D4 | 插件兼容 | **零破坏**——现有插件（security_guard/approval_gate/debug_logger）的事件订阅点不变 | 只移动执行逻辑，不改事件名与载荷；debug_logger 继续订阅 agent/tool-call 观测 |

## 5. 迁移清单

```
1. 新建 qi_agent/tools/executor.py（ToolExecutor，含审批分发/并发/结果封装）
2. agent.py：
   - 删除审批分发/并发执行/结果处理逻辑
   - 保留 agent/tool-call 事件点（bail）
   - step() 改为：事件点 → executor.execute(decision, calls) → 回填
3. 测试迁移：
   - 审批分支测试 → executor 单测（不经过 agent）
   - agent 集成测试 → 保留（验证编排层瘦身后的行为不变）
   - 新增 executor 单测：审批路由/并发/结果封装/失败聚合
4. 归档：docs/principles/（编排 vs 执行职责分离 + 事件语义二分原理）
```

## 6. 验收标准

```
✅ agent.py：step() 不含审批/并发/结果处理逻辑（只有事件点 + executor 调用）
✅ ToolExecutor 独立单测：审批分支（4 档）/并发/结果封装/失败聚合 全绿
✅ 行为不变：现有 431 测试全绿（审批链路/沙箱/回填文本不回归）
✅ 事件订阅兼容：现有插件零改动
✅ ruff 全过
```

## 7. 收益

```
✅ agent 瘦身（职责单一：编排+事件，不写执行策略）
✅ tool 层闭环（审批 → 执行 → 结果——一个模块完整交付工具能力）
✅ 可测试性（ToolExecutor 独立单测，不经过 agent 循环）
✅ 未来并发策略（顺序/并行/异步）改动只在 executor，agent 零改动
✅ 事件语义清晰（编排 vs 执行生命周期二分）
```
