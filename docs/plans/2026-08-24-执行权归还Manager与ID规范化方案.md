# 执行权归还 Manager + ID 规范化方案

> 日期：2026-08-24
> 状态：待评审
> 关联：`2026-08-24-AgentPool方案.md`（已实施：运行时/执行者分离 + pool 并发治理）
> 背景：AgentPool 实施后暴露 5 个设计缺口（用户评审指出）——本方案一次性修正。

## 1. 现状问题（用户评审 5 点）

```
① factory.py:98  agent_id = manager.register(context)——返回的是 context.id，
   变量名错误（build_runtime 无 agent，哪来的 agent_id）；RuntimeBundle 同时
   返回 context_id + agent_id（同一个值两个名，冗余）
② cli.py:82  私有 get_context() 薄转发——语义别扭（不是 CLI 的方法）
③ agent_manager.py:61  get_context(agent_id)——入参实为 context_id，命名错误
④ agent 无独立 id；context_id 无前缀（ctx_/agt_ 区分不了）
⑤ CLI 直接持有 agent（agent.chat）——agent 生命周期比 manager 短得多，
   CLI 不该持有执行者；执行权应归还 manager（用户方案：manager 持 pool，
   agent 生命周期在 pool 管理，manager 不感知具体 agent）
```

## 2. 核心设计：执行权归还 Manager

```
改前：CLI 持有 agent → agent.chat(user_input)（CLI 感知执行者）
改后：CLI 只调 manager → manager.run(context_id, user_input)（执行权归还）

manager.run(context_id, user_input)：
  pool.acquire(context)   # 从 pool 取执行者（无则建，绑定该 context）
  agent.chat(user_input)  # 执行
  pool.release(agent)     # 即用即弃（生命周期在 pool）
  → CLI 完全感知不到 agent 存在！

并行场景（子任务）：pool.acquire(None) 多实例并行（已实现）
主对话场景（同步）：manager.run 同步执行（用户等回复，天然同步）
```

**收益**：
- CLI 不持有 agent（不违反生命周期——agent 比 manager 短命）
- 执行权归还 manager（CLI 只认 manager.run 接口）
- manager 不感知具体 agent 类型（pool 工厂管创建——可插拔）
- agent 生命周期在 pool（即用即弃，无状态）

## 3. ID 规范化

```
context_id = "ctx_" + uuid12      # 数据载体 id（前缀 ctx_）
agent_id   = "agt_" + uuid12      # 执行者 id（前缀 agt_——make_agent 生成）
manager 注册表：contexts[ctx_id] = context（按 context_id 寻址）
agent 有独立 id：Agent.id = agt_xxx（执行者身份——可观测/审计）

命名修正：
  RuntimeBundle: 删 agent_id（重复）→ (manager, context_id, installed)
  manager.get_context(context_id)（入参改名）
  manager.register(context, role) 返回 context_id（语义明确）
  manager.run(context_id, user_input)（执行入口，用 context_id 寻址）
```

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `qi_agent/context/context.py` | id 前缀：`"ctx_" + uuid12` |
| `qi_agent/agents/agent.py` | Agent 加 `id`（`"agt_" + uuid12`，make_agent 生成） |
| `qi_agent/agents/agent_manager.py` | 参数改名（context_id）+ **新增 run(context_id, user_input)**（pool 取执行者→chat→release）+ register 返回 context_id |
| `qi_agent/agents/factory.py` | RuntimeBundle 删 agent_id（→ manager/context_id/installed）+ **get_context() 方法** |
| `qi_agent/agents/pool.py` | acquire 绑定 context 时返回 (agent, agent_id)（agent.id 生成） |
| `qi_agent/cli.py` | 删私有 get_context（→ runtime.get_context()）；**agent.chat → manager.run** |
| `evaluation/runner.py` | 适配（runtime.get_context + manager.run 或保留 make_agent 直调——评测每任务独立） |
| 测试 | id 前缀断言 + manager.run 测试 + CLI mock 适配 |

## 5. 决策点

| # | 决策 | 选项 | 倾向 |
|---|---|---|---|
| D1 | RuntimeBundle 是否删 agent_id | A. 删（context_id 足够） B. 留 | **A（用户指出冗余）** |
| D2 | agent_id 是否本期加 | A. 加（agt_ 前缀，make_agent 生成） B. 不加（v2） | **A（用户要求第 4 点）**——执行者可观测/审计需要 |
| D3 | manager.run 是否同步 | A. 同步（用户等回复） B. 异步（返回 handle） | **A**——主对话天然同步；异步=subagent 后台（已有 pool） |
| D4 | 评测 runner 是否也走 manager.run | A. 走（eval/prod parity） B. 保留 make_agent 直调 | **A**——一致（评测测真实形态） |
| D5 | CLI 是否完全感知不到 agent | A. 是（只调 manager.run） B. 保留 make_agent（CLI 创建） | **A（用户核心诉求）**——执行权归还 |

## 6. 工业级落地考量（P0-9）

- **可靠性**：manager.run 内部 pool.acquire 失败/超时 → 错误回填（agent.chat
  异常不外泄）；agent 即用即弃（无泄漏状态）；崩溃恢复依赖 context 持久化（v2）
- **安全**：执行权收敛 manager（单一入口可审计）；agent_id 用于审计
  （谁执行了什么）；权限链不变（审批/受限子集）
- **记忆**：context_id 是会话身份（持久化键）；agent_id 是执行者身份
  （瞬态，不入持久化）——两类 id 职责清晰
- **可观测**：manager.run 可发事件（agent-run/start、agent-run/end +
  agent_id/context_id）；/status 显示当前执行者（agent_id）
- **取舍声明**：agent_id 本期只做身份生成 + 事件透传，不做持久化
  （执行者瞬态）；manager.run 同步（主对话天然同步，异步=v2 后台模式）

## 7. 验收标准

1. context_id 前缀 ctx_，agent_id 前缀 agt_（可区分）
2. RuntimeBundle 无 agent_id（只有 manager/context_id/installed）+ get_context()
3. CLI 不持有 agent（只调 manager.run）——代码无 agent.chat 直调
4. manager.run(context_id, input) 返回回复（pool 取→chat→release）
5. 评测 runner 走 manager.run（eval/prod parity）
6. 全量回归绿 + ruff 过
