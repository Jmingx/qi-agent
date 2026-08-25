# Subagent 技术原理（agent-as-tool + 半双工协议）

日期：2026-08-23（实际跨天 2026-08-24 凌晨）
方案：docs/plans/2026-08-23-subagent方案.md

## 为什么做 subagent

单 agent 的四堵墙：
- **上下文墙**：一个会话上下文有限（128K），塞不下 20 个文件的分析
- **认知墙**：主 agent 一边想"怎么改代码"一边查"依赖文档"，注意力分裂
- **串行墙**：父任务里"先调研 A、再调研 B、然后综合"只能一步步来
- **污染墙**：子任务的中间对话（试错/垃圾信息）会污染父对话历史

subagent 拆法：父任务 = 拆解 → 委派 → 收集 → 综合；子任务 = 独立上下文跑，
只把【结构化总结】带回父对话。

## 核心设计决策

### 1. agent-as-tool（工具形态）

delegate_task 注册为普通工具，主 agent 自己决定"这个任务该外包"。
编排双入口：工具调用（agent 自动）+ CLI /delegate（用户手动）——同一实现。

### 2. 半双工协议（vs 全双工）

为什么不全双工（父问子答实时对话）：父 agent 正阻塞在 delegate_task
工具调用里，子问父答 → **死锁**。业界主流（Hermes steer / DSH direction）
都是单向。

半双工 = 父单向控 + 子单向回报：
- 父 → 子：steer（注入补充指令，子下轮生效）/ poll（探活）/ stop（强制终止）
- 子 → 父：result（最终结果）/ partial（need_more_info 回报）
- 协商落地 = partial 回报 + 重新 spawn，不是实时对话

实现：子 agent 的 pre-step 瀑布钩子（priority=100）每轮 drain_steer + 查 stop。

### 3. 受限子集（工具级隔离，双层防绕过）

全局注册表单例 + 白名单参数：
- 层 1（模型可见）：get_tool_schemas(allowlist) 只给白名单内 schema
  ——LLM 只知道这些工具存在，其他【看都看不到】
- 层 2（执行硬校验）：executor 执行前查白名单 → 白名单外直接拒绝
  ——模型即使幻觉请求白名单外工具，执行层拦截
- 对齐 approved 内部参数的双层设计（schema 不可见 + 执行端校验）

### 4. 权限请求（用户审批前置，先问再给）

主 agent 拉起 subagent 前（还在主 agent 流程）：
1. 预测：按 goal+context 判断白名单外权限
2. 弹框：approval_gate 逐个请求用户确认
3. 注入：用户批准的工具/路径注入子 agent 授权清单

权限四层：
- 层 0 默认只读子集（无需审批）
- 层 1 白名单路径写（delegate_task 参数声明）
- 层 2 白名单外权限（弹框审批，先问再给）
- 层 3 永远不给（shell 代码执行/rm/delegate_task 递归/超上限）

### 5. 递归禁止（三层防线）

- 防线 1（结构禁止）：子 agent 工具集里没有 delegate_task → 物理上无法再 spawn
- 防线 2（深度限制）：将来开放 orchestrator 时 max_spawn_depth 封顶
- 防线 3（预算）：spawn 带 token/时间/步数预算，超预算强制终止

### 6. 结构化返回（P0 用户要求）

result 是 JSON：{summary, artifacts, status, error, question, usage}。
子 agent 的 system prompt 强制要求只输出此 JSON，父 agent 直接解析。

## 踩过的坑

1. **delegate_task 漏注册**（builtin/__init__.py 没 import）→ 工具静默不存在，
   评测 d1/d2 首跑 0/2（主 agent 自己用 list_dir/read_file 干活）
   → 工具注册触发链路（tools/__init__ → builtin/__init__ → 各模块 register）
   漏一个 import = 静默不注册，评测是唯一兜底
2. **循环导入**：delegate_task.py 模块级 `from qi_agent.agent import Agent`
   → tools/__init__ → builtin/__init__ → delegate_task → agent（又 import
   tools/__init__）→ ImportError。修：Agent 在 _run_subagent 内延迟 import。
3. **delegate_task 无条件审批** → 评测 fail-closed 全拒。修：条件审批——
   纯只读委派放行（安全），带 write_paths 才弹窗（用户背书）。
4. **生产系统提示词太简陋**（"你是一个有用的助手"）→ 模型不知道有
   subagent 能力。修：agent_factory 用 PROD_SYSTEM_PROMPT（工具使用策略）。

## 与 Hermes / DSH 对照

| 维度 | Hermes | DSH | qi-agent |
|---|---|---|---|
| 形态 | delegate_task 工具 | detached agent | delegate_task 工具 |
| 上下文 | goal+context（自包含） | goal+context | goal+context（B 方案） |
| 控制面 | steer/stop/list | direction 单向 | steer/stop/poll（半双工） |
| 返回 | final summary | structured result | 结构化 JSON |
| 工具 | subset of parent's | 受限 | 受限子集（双层） |
| 递归 | max_spawn_depth | - | 结构禁止（工具集无 delegate_task） |
| 持久 | 非持久（进程内） | - | 非持久（v1 同步） |

## 问答精华（2026-08-24 研读讨论沉淀）

### 7. 两条调用路线（同步 vs manager 后台）

delegate_task 有两条路，共用 `_run_subagent`（这就是 `_ContextAdapter` 存在的意义）：

```
路线 A（同步工具，v1 生产在用）：delegate_task 工具 → _ContextAdapter
  → _run_subagent → 结构化返回。像打电话：拨通 → 听完 → 挂断，
  中途无法干预（父阻塞在工具调用里，没有控制面）。

路线 B（manager 后台，机制就绪 + UT 验证，调用者 v2 接线）：
  SubagentManager.spawn → 后台线程跑 → 父继续干活 → poll/steer/stop。
  像邮局：寄出（spawn）拿单号 → 查快递（poll）→ 补短信（steer）
  → 叫停（stop）→ 收回复（result）。

现状：A 生产在用；B 的机制完整（spawn/steer/poll/stop + 状态机）
  但无生产调用者——CLI /subagent 控制命令、delegate_task background=true、
  subagent_control 工具都是 v2 的事（YAGNI：不为不存在的调用者提前接线）。
```

### 8. SubagentContext 与 agent session 职责分离（v0.4.27 改名动机）

```
SubagentContext = 子任务【运行环境】（瞬态，任务结束即消失）：
  任务定义（goal/context/max_turns）+ 状态机（status/result/error）
  + 控制面（steer_queue/_stop_flag）+ 线程信号（_done）+ 独立事件总线

未来 agent session = 对话【存档】（持久，可恢复/搜索）：
  消息历史落盘 + 恢复 + 搜索（TODO：独立 session 系统）

边界原则（写进 subagent.py 头注释）：
  Context 不持有对话历史（messages 在子 Agent 实例上）
  Context 不负责持久化（那是 session 系统的事）
  两者是组合关系（子任务装配时各挂各的），不是继承/合并

命名直觉：
  Context = 任务跑在什么"现场"（瞬态，跑完即散）
  Session = 对话存在什么"存档"（持久，可恢复可搜索）
```

### 9. 统一 AgentContext 方向（合并讨论结论——待评审实施）

研读时发现：主 agent（Agent 类内部）与 SubagentContext 有 80% 重叠
（事件总线/状态机/控制面），本质都是"一个 agent 运行的**环境**"。
