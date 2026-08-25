# devlog: AgentContext 统一合并（主/子 agent 运行环境归一）

日期：2026-08-24
方案：docs/plans/2026-08-24-AgentContext统一合并方案.md（用户评审通过）

## 做了什么

1. **AgentContext（新）**：`qi_agent/context/context.py`——统一数据载体
   （消息/轮数/用量 + 状态机 + 控制面 + 事件总线），10 个单测
2. **Agent 接入**：`agent.py` 组合持有 context，messages/_turn/_usage 迁入
   （薄委托保留方法名），无状态执行者
3. **SubagentContext 继承**：`subagent.py` 继承 AgentContext + 子专属
   （write_paths/timeout/context_text），Manager 操作统一 Context
4. **delegate_task 适配**：_ContextAdapter 字段对齐（context_text）

## 怎么做的（TDD）

- 先写 AgentContext 测试（10 个：状态机/控制面/信号/事件）→ RED → 实现
- Agent 接入 → 跑 agent 相关测试（发现回归：显式 events 被忽略）→ 修复
- SubagentContext 继承 → 测试 import 迁移（ContextStatus）→ 全绿
- 手工验收：同一 context 被两个 Agent 实例接管，上下文完整保留

## 遇到的问题与解决

| 问题 | 解决 |
|---|---|
| 显式 events 被忽略（回归） | context 创建时复用传入 events（向后兼容） |
| SubagentContextStatus 命名 | 统一为 ContextStatus（context/context.py） |
| history property 重复定义 | 删文件末尾旧定义（ruff F811） |
| _ContextAdapter 字段 | context → context_text（对齐 SubagentContext） |

## 关键架构洞察（用户拍板 D2/D3）

- 消息/轮数/用量迁入 Context（数据载体）——session 接入点统一
- Agent 变无状态——同一 context 可被新实例接管（断线续聊基础）
- 控制面通用化——用户未来也能 steer/stop 主 agent

## 验证

- 474 全绿（+10 AgentContext 单测）+ ruff 全过
- 手工验收：实例2 看到完整上下文（第一句+第二句），轮数 1→2 累计

## 下一步

- session 系统（TODO-1）：persist 字段落盘
- 主 agent CLI /stop 控制面（机制就绪，调用者 v2）
