# AgentContext 统一运行环境（主/子 agent 归一）技术原理

日期：2026-08-24
方案：docs/plans/2026-08-24-AgentContext统一合并方案.md（评审通过）

## 为什么做

subagent 实施后出现两个"运行环境"概念：
- SubagentContext（子任务运行环境）：任务定义 + 状态机 + 控制面 + 独立总线
- 主 agent 内部状态：events + _turn + messages + _usage

本质相同：都是"一个 agent 运行的【环境】"——状态 + 事件 + 控制。
差异只在：控制者（用户/CLI vs 父 agent）+ 持久化（长期 vs 瞬态）。
两套概念心智分裂，未来 session 系统（TODO-1）、multi-agent（v2+）
都需要统一接入点。

## 核心架构决策（用户拍板 D2/D3——关键洞察）

### 数据载体 vs 无状态执行者

```
AgentContext = 【数据载体】——消息历史 + 会话轮数 + 用量累计
  + 状态机 + 控制面 + 事件总线。可持久化、可恢复、可归档。
Agent = 【无状态执行者】——消费/回填 Context 的消息，只跑循环。
```

为什么这样分（用户质疑触发，方案 D2 从"留 Agent"修正为"迁入"）：
1. **session/记忆系统的接入点是数据载体不是执行者**——session 只碰
   Context（消息/轮数/用量都在里面），不依赖 Agent 循环 → 解耦
2. **subagent 的 Agent 实例跑完销毁，但 Context 还在** → 消息可归档
   可持久化（原 TODO-2 卡点：消息在 Agent 里，无法持久化）
3. **无状态 Agent 可被新实例接管继续跑**——断线续聊/会话恢复的
   架构基础（手工验收：同一 context 被两个 Agent 实例接管，上下文完整保留）
4. **生命周期判据**：消息/轮数/用量是【会话级】（跨 chat、跨 Agent
   实例）→ 归 Context；step（循环步数）是【循环级】→ 留循环局部变量

### 控制面通用化（最大收益）

steer/stop/poll 对【任何 agent】可用：
- 子 agent：父 agent 通过 manager.steer/stop（现状）
- 主 agent：用户/CLI 未来也能 steer/stop（CLI /stop = context.stop()！）

SubagentManager 变成"通用控制台"——操作统一 AgentContext，
不再持有 SubagentContext 专属逻辑。

## 架构分层（Java 心智）

```
AgentContext = Entity（数据实体，可落库）——session 系统唯一接口
Agent        = Service（服务，无状态方法）——只跑循环
SubagentContext = AgentContext 子类 + 子专属配置（write_paths/timeout）
标准分层：Service 操作 Entity，Entity 可持久化
```

## 兼容设计（薄委托）

外部读取方（cli.get_usage / runner._turn / delegate_task.get_usage）
保留方法名做委托（property/method 内部读 context）——不是兼容层，
是面向外部 API 的稳定接口。D6 一次性迁移（不留兼容层）但薄委托保留。

## 踩过的坑

1. **显式传入的 events 被忽略**（回归）——Agent 改从 context 取 events 后，
   外部 `Agent(events=bus)` 的 bus 被丢弃 → 外部监听者收不到事件。
   修：context 创建时复用显式传入的 events。
2. **SubagentContextStatus 命名**——状态枚举统一为 ContextStatus
   （context/context.py），测试 import 同步迁移。
3. **history property 重复定义**（薄委托区 + 文件末尾旧定义）→ ruff F811。
4. **_ContextAdapter 字段对齐**——SubagentContext 用 context_text（背景文本）
   而旧 Adapter 用 context → 字段名同步。

## 与业界对照

| 维度 | Hermes | qi-agent（合并后） |
|---|---|---|
| 会话数据 | session DB（SQLite） | AgentContext（数据载体，persist 字段） |
| 执行者 | agent 循环 | Agent（无状态执行者） |
| 子任务 | delegate_task 独立会话 | SubagentContext（继承 AgentContext） |
| 控制面 | steer/stop/list | AgentContext.steer/stop/poll（通用） |
| 持久化 | session DB 统一 | persist 字段（v2 落盘） |

## 遗留（v2）

- persist 字段已定义未实现（落盘归 session 系统）
- 主 agent 的 CLI /stop 控制面（机制就绪，调用者 v2）
- SubagentManager 通用化（操作统一 Context，未来主 agent 也能注册被控制）
