# AgentManager 统一控制台 + 两级状态机技术原理

日期：2026-08-24
方案：docs/plans/2026-08-24-AgentManager统一控制台方案.md（评审通过）

## 核心架构：CLI 控制主 agent = 主 agent 控制 subagent

```
AgentManager（= SubagentManager 构建升级，统一控制台）：
  register(context, role)    # 任何 agent 注册（主 role="main"，子 role="subagent"）
  spawn()                    # 子任务（原 SubagentManager.spawn，接口不变）
  steer/stop/poll(id)        # 控制面（按 id 寻址）

主 agent：build_agent() 创建 context → register → AgentBundle(agent, manager, agent_id)
CLI：     /stop → manager.stop(agent_id)   ← 和停子 agent 一样！
          /status → manager.poll(agent_id) + context 数据
```

**为什么 CLI 通过 manager 控制（用户拍板，否决"CLI 直接持有 context"）**：
```
直接持有 context = 控制逻辑散落 CLI（每个命令自己调 context 方法）
通过 manager     = 控制逻辑收敛控制台（CLI 只认 manager 接口 + agent id）
—— 同一个控制台，两种控制者（用户/CLI vs 父 agent），协议完全统一
```

## 两级状态机（用户拍板引入）

```
会话级（ContextStatus）——"agent 活得怎么样"（整个生命周期）：
  IDLE → RUNNING → COMPLETED / FAILED / STOPPED
  （reset 后任意终态回 IDLE，可复用）

循环级（ChatPhase）——"agent 正在干什么"（单次 chat 内部阶段）：
  IDLE → TURN_START → LLM_CALL →（TOOL_EXEC → LLM_CALL 循环）→ ANSWERING → DONE
  任何阶段 stop → DONE（下轮生效本阶段；后台线程/信号下阶段实时中断）
```

**为什么两级**：
```
会话级 ≈ Java 线程生命周期（NEW/RUNNABLE/TERMINATED）——宏观
循环级 ≈ 方法内局部状态（正在执行哪一步）——微观
/status 总览读会话级；/status 明细读循环级；stop 响应读循环级（知道阻塞在哪）
```

**状态机 vs 事件点（互补不冲突）**：
```
状态机 = 可查询的当前值（/status 读它）
事件点 = 一次性广播的变化（debug_logger 监听它）
```

## 关键实现决策

### 1. 状态机更新点（chat 全生命周期）

```
chat() 入口：  begin_chat()      → RUNNING + TURN_START
循环每步：     enter_llm_call()  → LLM_CALL
有工具调用：   enter_tool_exec() → TOOL_EXEC
最终回答：     enter_answering() → ANSWERING
chat 出口：    complete_chat()   → COMPLETED + DONE（未中断时）
异常：         fail_chat()       → FAILED + DONE
stop 中断：    _run_tool_loop 返回 → 出口检查 should_stop → 不覆盖 STOPPED
```

**踩坑：中断出口不能覆盖状态**——chat() 原无条件 complete_chat()，
stop 中断后会把 STOPPED 覆盖成 COMPLETED。修：出口检查 `if not
should_stop(): complete_chat()`。

### 2. IDLE 语义：主 agent vs 子 agent

```
主 agent：新建 = IDLE（未开始，chat 入口才 RUNNING）
子 agent：spawn 即 RUNNING（spawn 语义 = 立即运行，不是"新建未开始"）
——同一枚举，两种语义（创建即开始 vs 创建待开始）
```

### 3. AgentManager 接口兼容（SubagentManager 退化为子类）

```
SubagentManager(AgentManager) —— 向后兼容别名
  spawn/_run/steer/stop/poll 全部继承（接口不变）→ 现有 subagent 测试零改动
  register 是纯新增（主 agent 注册）
```

**循环导入处理**：agent_manager 需要 SubagentContext（spawn 返回），
subagent 需要 AgentManager（继承）→ 循环。解法：agent_manager 的 spawn
内延迟 import SubagentContext（注解用 Any）。

### 4. steer 允许 IDLE 排队（主 agent 语义）

steer 不要求 RUNNING——用户先说"改方向"，agent 启动后下轮生效
（指令只是入队，运行中才消费）。subagent 原要求 RUNNING（任务没跑
不该 steer）——统一后放宽为"存在即可排队"。

## 与业界对照

| 维度 | Hermes | qi-agent（本方案） |
|---|---|---|
| 控制面 | delegate_task steer/stop/list | AgentManager steer/stop/poll（统一） |
| 主 agent 控制 | CLI 直接操作 | manager.stop(agent_id)（同一控制台） |
| 状态 | session 状态 | 两级状态机（会话级 + 循环级） |
| 返回形态 | agent 对象 | AgentBundle（agent/manager/agent_id） |

## 遗留（v2，已记 TODO）

- D3 升级：后台线程/信号——chat 阻塞在 LLM 调用时实时响应 /stop
  （状态机 phase 是精准中断的基础）
- /steer CLI 命令（机制就绪，调用者按需接）
- persist 落盘（会话持久化独立 TODO）

## 可插拔 agent 归类（2026-08-24 追加）

执行者家族收敛到 `qi_agent/agents/` 包（用户拍板"agent 作为可插拔组件归类"）：

```
qi_agent/agents/（执行者家族——"有哪些执行者可以用"）
├── __init__.py        （导出 Agent/AgentManager/SubagentContext/...）
├── agent.py           Agent 执行者（无状态循环）
├── agent_manager.py   AgentManager 统一控制台
├── subagent.py        SubagentContext + SubagentManager
└── factory.py         build_agent + AgentBundle + PROD_SYSTEM_PROMPT

不动（正交基础设施）：
  context/  = 数据载体（AgentContext——session/记忆接入点）
  events.py = 事件总线（全项目共用）
  llm.py    = LLM 客户端（全项目共用）
```

**为什么这样分（对齐 tools/plugins 分层哲学）**：
```
agents/ = "有哪些执行者"（可插拔边界——换执行者=本包加文件）
context/ = "执行者跑在什么数据上"（稳定，session 接入点）
events/llm = "用什么跑"（基础设施，共用）

换执行者（如 agents/specialist.py）→ context/events/llm 零改动
——插拔边界清晰（类比 Java：agents≈service 层可替换，
  context≈entity 层稳定，events/llm≈infrastructure 层）
```

**迁移经验**：import 全量迁移用脚本批量替换（29 个文件），
一次性 528 全绿零回归——git mv 保留历史，未跟踪文件用普通 mv。

## CLI 数据访问修正（2026-08-24 用户拍板追加）

**问题（用户指出）**：CLI 大量直连 agent 内部（agent.history/agent.messages/
agent.get_usage()/agent.context.turn）——换 agent 执行者实现就不准了。

**修正**：
```
① agent.clear_context() 删除 → context.reset_session()
   （clear 是"数据载体重置"不是"执行者行为"——agent 无状态，
     没有"清自己"的概念）
② /compact /context /status /delegate 的数据读取 → manager.get_context()
   （数据载体所有权在 manager，CLI 不直接持有 context 对象）
③ AgentBundle：context 对象 → context_id（CLI 用 manager.get_context(id) 拿）
④ context 加 system_prompt 字段（reset 重建 system 消息需要它——
   Agent 装配时写入）

最终访问路径：
  CLI 读数据 → manager.get_context(context_id)（数据载体，稳定）
  CLI 调行为 → agent.chat()（执行者，唯一行为入口）
  CLI 控制   → manager.stop/steer/poll（控制台）
  ——换 agent 执行者实现（agents/specialist.py）时，只要它消费
    context 数据载体 + 提供 chat 行为，CLI 零改动
```
