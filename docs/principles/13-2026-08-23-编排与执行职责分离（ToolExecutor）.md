# 13-2026-08-23-编排与执行职责分离（ToolExecutor）

> 项目技术原理归档（P0-1）。面向 agent 开发小白读者。
> 关联方案：`docs/plans/2026-08-23-ToolExecutor执行闭环方案.md`

## 一句话

**agent 核心循环只做"编排"（发事件、收结果、管历史），工具调用的"执行"（审批、并发、结果封装）全部收进独立的 ToolExecutor——一个模块完整交付工具能力。**

## 为什么要分（问题）

改造前的 `agent.py` 一个 `step()` 循环里混了 4 层职责：

```python
for call in result.tool_calls:
    decision = self.events.bail("agent/tool-call", ...)   # ① 事件点（编排）
    # ② 审批分发：按 decision.action 分支（BLOCK/WARN/NEED_APPROVAL...）
    # ③ 并发执行：ThreadPoolExecutor 线程池跑工具
    # ④ 结果处理：组装 [安全拦截]/[审批拒绝]/警告后缀 → 回填
```

问题清单：

1. **职责不分**：编排（"下一步做什么"）与执行（"具体怎么做"）糊在一个方法里
2. **不可单测**：测审批分支必须走完整 agent 循环（造 FakeClient 脚本、跑多轮）
3. **并发策略耦合**：想改成顺序执行/异步队列，要改 agent.py 核心
4. **文件膨胀**：执行策略（~100 行）占 agent.py 三分之一

**Java 类比**（用户背景）：现在的 agent.py 像 Controller 里既做路由、又写 Service 业务、又拼 SQL——改一个业务规则动 Controller。正确分层：Controller 只做路由转发，Service 承载业务。

## 业界对照

| 项目 | 工具执行的组织 |
|---|---|
| **Hermes** | Agent 循环发事件（on_tool_call），工具执行走工具管理模块，agent 只收集 ToolResult |
| **DSH** | 工具执行完全在 tool 层（execution engine），agent 循环只拿 ToolResult 对象 |
| **Claude Code** | 工具执行在 tool use manager，agent 只决定"下一个动作" |

**共识**：事件点（agent 发）→ 执行（tool 层闭环）→ 结果（agent 收）。编排与执行是两层，不是一回事。

## 怎么分（核心设计）

```
agent.py（编排层，只剩 3 件事）：
  1. 发事件点：agent/tool-call（bail 短路决策——插件判档"要不要做"）
  2. 调执行器：tool_executor.execute(calls, decisions) → 收结果
  3. 回填历史：tool 消息追加进 messages

tools/executor.py（执行闭环，3 个阶段）：
  阶段1 审批分发：NEED_APPROVAL/ESCALATION → 发 agent/tool-approval bail → 同意才放行
  阶段2 并发执行：线程池跑工具（只 execute_tool，事件回主线程）
  阶段3 结果封装：BLOCK→[安全拦截] / 审批拒绝→[审批拒绝] / WARN→执行+警告后缀
                  → 发 agent/tool-result 事件 → 返回 (output, duration)
```

### 事件语义二分（本方案最核心的概念）

事件不是"都从 agent 发"——按语义分两类：

| 事件 | 语义 | 归属 |
|---|---|---|
| `agent/tool-call` | **编排**："要不要做"（短路决策） | agent 发 |
| `agent/tool-approval` | **编排**："用户同不同意"（审批决策） | executor 发起（决策延续） |
| `tool/start` | **执行生命周期**："开始做了"（观测） | executor 发 |
| `agent/tool-result` | **执行生命周期**："做得怎么样"（观测/记录） | executor 发 |
| `agent/step-end` | **编排**："这一步结束"（策略链挂载） | agent 发 |

**判别方法**：这个事件是"做决定"还是"报进度"？
- 做决定（bail/决策）→ 编排层（agent 或决策延续）
- 报进度（emit/观测）→ 执行层（executor）

注意：`agent/tool-result` 事件名带 `agent/` 前缀但由 executor 发——**事件名是协议契约（插件订阅点），发出位置是实现细节**。改名会破坏插件订阅（debug_logger 等），保持名字不变、只移位置，插件零改动。

### 注入方式

```python
# Agent 构造：执行器可注入（默认共享同一事件总线）
class Agent:
    def __init__(self, client, ..., events=None, tool_executor=None):
        self.events = events or EventBus()
        self.tool_executor = tool_executor or ToolExecutor(self.events)
```

- 默认：executor 和 agent 用**同一个事件总线**——插件订阅点完全不变
- 测试：可注入假 executor（替换执行策略）或假总线（隔离插件）

## 改造收益

```
✅ agent 瘦身：292 行 → 220 行（执行策略全部迁出）
✅ tool 闭环：审批 → 执行 → 结果，一个模块交付完整工具能力
✅ 独立单测：executor 直接测（审批路由/并发/失败聚合/事件），不经过 agent 循环
✅ 并发策略可换：顺序/并行/异步队列，只改 executor，agent 零改动
✅ 事件语义清晰：编排 vs 执行生命周期，一眼可判归属
```

## 踩过的坑

1. **测试 patch 位置**：`test_parallel_actually_concurrent` 曾 patch
   `qi_agent.agent.execute_tool`——执行下沉后路径变成
   `qi_agent.tools.executor.execute_tool`，patch 位置必须跟着移动。
   教训：**重构移动函数后，全量搜索所有引用它的路径**（尤其测试的 mock.patch）。
2. **验收脚本用了错误断言命令**：想验证 `[安全拦截]`（红线）却用了
   `rm -rf C:/`——它是**审批档**（命中 `"rm "` 前缀），无审批插件时
   fail-closed 拒绝显示 `[审批拒绝]`。行为正确，是测试预期错了。
   教训：**验收前先查清楚判档规则**（HARDLINE vs APPROVAL），选对触发命令。
3. **assistant_message 完整性**：FakeClient 构造 ChatResult 时没传
   `assistant_message`（默认空 dict）——agent 原样追加进历史时产生
   `{}` 消息，后续消息处理崩（KeyError: 'role'）。真实 LLM 客户端会构造
   完整 assistant 消息，**测试替身必须同样完整**（协议要求）。
