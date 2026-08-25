# dsh-agent-teams 多 agent 插件剖析（业界实现参考）

日期：2026-08-24
来源：GitHub NanmiCoder/dsh-agent-teams（源码逐文件研读，v0.1.x，~3900 行 TS）
配套：`16-subagent（agent-as-tool+半双工协议）.md` 是 qi-agent 自己的 subagent 设计，本文是业界最火的多 agent 团队插件实现剖析，作对照参考。

## 一、插件定位（一句话）

DSH（DeepSeek Harness）生态最火的多 agent 插件：**把当前会话变成"队长"**，用 10 个 `agent_teams_*` 工具 + 一段注入 system prompt 的"队长协议" + 事件驱动调度器 + 持久化邮箱 + Web 活动面板，拉起一支多 agent 团队。**不需要额外 Workflow 引擎**——一切基于 DSH 的 Cordis 插件机制。

安装：`dsh plugin --profile web add @nanmicoder/dsh-agent-teams`

## 二、整体架构

```
┌─ 队长（当前会话 agent）────────────────────────────────┐
│ system prompt 注入"队长协议"（7 步：建队→拉人→拆任务→    │
│ 盯进度→汇总→删除）                                     │
│ 10 个工具：create / add_member / remove_member /       │
│   create_task / reassign_task / claim_task /           │
│   update_task / send_message / status / delete         │
└────────────────────────────────────────────────────────┘
        │ 创建/唤醒（ctx.subagents.followup）
        ▼
┌─ 成员（continuable 子代理，每人一个持久会话）────────────┐
│ 共享队长工具集，但 6 个队长专属工具被隐藏（MEMBER_DENIED_  │
│ TOOLS：create/add_member/remove_member/reassign_task/   │
│ create_task/delete）——成员不能建队拉人删队，只能干活      │
│ 空闲时零资源占用；被唤醒才跑一轮完整 turn                │
└────────────────────────────────────────────────────────┘
        │ 读写（磁盘真相）
        ▼
┌─ 共享状态：<workspace>/.agent-teams/<teamId>/ ─────────┐
│ team.json（成员/任务/DAG）+ inbox/<agentKey>.jsonl（邮箱）│
└────────────────────────────────────────────────────────┘
        │ 轮询
        ▼
Web 活动面板（/plugins/dsh-agent-teams/state 路由，快照=磁盘+实时活动）
```

## 三、五个核心机制

### 1. 激活面：协议即 prompt（index.ts）

插件启动 `ctx.systemPrompt.section()` 注入"队长协议"（7 条）：
1. agent_teams_create 建队（你成为队长，一次只能带一队）
2. agent_teams_add_member 拉成员（默认继承队长 LLM 路由，用户明确要求异构才传 provider/model）
3. agent_teams_create_task 拆任务并声明依赖（未满足依赖的任务不可领取）
4. 通过 status 监控 + send_message 指导，**不要重复干成员的活**
5. 转派前先 agent_teams_reassign_task（撤销旧 attempt）
6. 任务更新必须带当前 attempt_id；轮询 status 直到所有任务终态
7. 汇总结果给用户，然后 agent_teams_delete

另有两个确定性激活面：`/agent-teams` slash 命令（Web GUI 菜单）+ 手势边界（agent/pre-step 监听用户消息前导 token，**仅 source.kind === 'user' 防伪造**——注入文本不能冒充用户手势）。

**要点：模型靠 prompt 学会"当队长"，agent 核心零侵入。**

### 2. 成员：可持续续聊的子代理（members.ts）—— 最关键的实现决策

```typescript
ctx.subagents.startContinuable({ ... })   // 创建成员（spawn/fork 两种 provider）
ctx.subagents.followup(captain, memberId, [{type:'text', text}])  // 唤醒跑一轮
ctx.subagents.interrupt(memberId, ...)    // 中断
```

- 成员**没有独立进程**——是宿主进程里的 AgentHandle，靠 **session 持久化**（DSH SQLite 会话）跨轮次、跨重启存活
- **空闲时零成本**（不占进程、不调 LLM），被 followup 唤醒才跑一个 turn
- 所以它"池化"的是**会话句柄**，不是**进程**——省掉了 pool 的全部管理复杂度（生命周期/回收/崩溃隔离）
- 成员最终消息不可程序化读取 → 成员把报告**写进队长邮箱 + 任务记录**（output 字段），队长通过 status 读
- 孤儿清理：写 team.json 失败但子代理已 live → retire（从子代理列表消失、不可续跑）+ interrupt

### 3. 邮箱：成员直达消息，无队长中转（state.ts）

`inbox/<agentKey>.jsonl`（每 agent 一个 JSONL，镜像 Claude Code AgentTeams 邮箱布局）。
`send_message` 的 `from` 只能是调用者自己（防伪造）；投递状态机：
**未读 → 认领（claimMailboxDelivery，防 fallback 与直达竞争）→ 确认（acknowledge）/ 释放回滚（release）**。
成员离线时消息持久化，回来时调度器先投邮箱（fallbackMailboxPrompt）再派新任务。

### 4. 调度器：事件驱动，不是轮询（scheduler.ts）

与 Claude Code 的关键差异（源码注释明说）：Claude Code teammates 每轮后**轮询**共享任务列表；DSH 的可续聊 agent 有显式 idle/running 边界，所以调度器在**每个 idle 事件和任务图变更时尝试一次原子领取**——不维持轮询回合（省 token）。

- 入口：`ctx.on('agent/status')` 监听所有 agent 状态 → 成员变 idle → kickMember
- 原子性两把锁：**团队锁**（withTeamLock，防并发派同一任务）+ **成员队列**（serializeMember，每成员 Promise 链）
- 领取逻辑：邮箱优先 → 依赖满足的 pending 任务（先自己名下的，再共享池）
- **attempt_id 机制（防迟到覆盖）**：每次派活 beginTaskAttempt 生成唯一 attemptId，成员更新任务必须带当前值；转派/接管先 invalidateTaskAttempt 撤销旧 attempt、等原成员安静（waitForMemberIdle），防迟到结果写回新 attempt。派发失败回滚：只回滚自己的那次分发（attemptId 比对防并发覆盖），任务回 pending + assignee 还原
- **停驻与冷恢复**：驻留成员带开放 attempt 空闲 = 停驻（parkedAttempts 内存 map，等队长指令不自动重派）；**冷重启后内存 map 为空 → 磁盘遗留开放任务自动 recoverOwned 生成新 attempt**

### 5. 状态模型（types.ts）

```
TeamState  { name, id(队名 sanitize 成目录 id), description, captainSessionId,
             createdAt, members[], tasks[], taskSeq }
TeamTask   { id(t1,t2...), subject, description, status, assignee(成员名或captain),
             dependencies[], output, attempt(单调代数), attemptId, handoffId,
             reassigning, createdAt, updatedAt }
           status 状态机：pending → claimed → in_progress → completed/failed/cancelled
TeamMember { id(子代理 session id), name, role, provider, model, reasoningEffort,
             joinedAt, status(idle/working/removed) }
TeamMessage{ id, from, to, content, ts, deliveryClaimedAt, deliveredAt, readAt }
```

文件持久化 + 原子写（replaceFileAtomicOrDirect）+ 进程内串行（多进程同时改同一团队不保证一致——诚实声明的边界）。

### 6. 事件层 + UI 层

- **事件层**：7 类 `agent-teams/*` 事件 append 到**队长的 Session**（成员执行的操作也记队长会话——单一口径监控面）；Web 端靠 Conversation Node 机制从 session log 确定性折叠树状视图
- **UI 层**：Web 路由 `/plugins/dsh-agent-teams/state`（浏览器轮询快照：磁盘 team.json + 实时子代理活动合并）+ `/plugins/dsh-agent-teams/assets`（**白名单**防路径穿越，只放行固定 png）。前端 React：分段进度条、可折叠成员树、可交互任务 DAG。镜像 Claude Code desktop teamWatcher 模式——**模型跳过工具仪式（没调 update_task）也能如实展示磁盘状态**

## 四、设计亮点（值得圈起来）

1. **事件驱动调度 vs 轮询**：idle/running 边界触发，不烧 token
2. **attempt_id 原子性**：转派撤销旧 attempt + 迟到结果无法覆盖 + 回滚只回滚自己
3. **邮箱投递状态机**：claim/acknowledge/release 回滚，成员离线不丢消息
4. **协议即 prompt**：队长行为完全由注入的 system prompt 定义
5. **权限隔离**：成员共享工具集但看不到队长专属工具（对应 qi-agent 受限子集双层设计）
6. **磁盘真相 + 实时活动合并**：模型不守协议也如实展示
7. **孤儿清理**：状态写失败 → retire + interrupt，不留僵尸子代理
8. **一队长一队**：findTeamByParticipant 检查，建队即上锁

## 五、与 qi-agent 的对照（衔接 16 号文档）

| 维度 | qi-agent（16 号设计） | dsh-agent-teams |
|---|---|---|
| 形态 | delegate_task 工具，**一次性**子代理（同步等结果） | 10 个工具 + 持久化**团队**（成员可续聊） |
| 成员生命周期 | 非持久（v1 同步，spawn 即跑完即弃） | **continuable 持久会话**，空闲零成本，唤醒才跑 |
| 控制面 | 半双工（steer/stop/poll） | followup 唤醒 + interrupt + 邮箱直达 |
| 上下文 | goal+context 自包含打包 | 队长持协议 + 任务 DAG 共享；成员上下文隔离（各自持久会话） |
| 调度 | 无（父 agent 直接驱动） | 事件驱动共享调度器（idle 边界触发） |
| 防竞态 | 无对应 | attempt_id（转派撤销/迟到覆盖防护） |
| 权限 | 受限子集双层 + 权限请求四层 | MEMBER_DENIED_TOOLS 隐藏队长工具 |
| 递归 | 结构禁止（工具集无 delegate_task） | memberMaxDepth 配置封顶 |

**核心启示**：
1. qi-agent 的"pool 化"将来若做，形态应是**持久会话池**（可续聊会话句柄）而非常驻进程池——进程复用性能账已算过（<1% 收益 + 破坏隔离）
2. **前置条件：session 持久化**（qi-agent 目前子代理非持久）——pool 化排期在会话存储之后
3. attempt_id 防覆盖 + 事件驱动调度是通用答案，写 qi-agent 多 agent 方案时直接抄
4. 队长协议进 prompt 的做法 qi-agent 已有同款（PROD_SYSTEM_PROMPT 工具使用策略）——将来扩"团队协议"段落即可

## 六、诚实局限（源码注释自述）

- 状态文件持久化 + **进程内串行**：多进程同时改同一团队不保证一致（单机单进程够用）
- 活动面板如实展示持久化状态，但**模型偶尔完成工作却没按协议更新任务状态**（靠面板兜底展示，不保证任务记录准确）
- 成员默认零交互（快照队长 LLM 路由），异构分工需用户显式要求——避免逐个弹窗打扰
