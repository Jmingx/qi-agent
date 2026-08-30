# AgentMailbox 统一通信方案（邮局模型：Message + Dispatcher + Mailbox + Transport）

> 日期：2026-08-29（修订版 2）
> 状态：待评审
> 修订记录（v2，用户评审意见）：
>  ① mailbox 无状态化——status/result/phase 回归 context（agent 状态机），
>     mailbox 是纯队列管道，不持有执行状态；消费逻辑归 Dispatcher
>  ② 注册发现——不用中心化注册服务，三层寻址：构造注入（免查表）+
>     进程内路由表（兜底）+ 未来 AgentCard 发现（跨进程去中心化）
>  ③ Message id/sender/target 必须实现（邮件三要素——投递机制本身，
>     不是预留字段）
>  ④ 架构按"邮局模型"重构：邮件 Message / 邮局 Dispatcher /
>     收件箱 Mailbox / 快递员 Transport——职责四分离
> 关联：`2026-08-29-内核线程安全修复方案.md`（吸收 3.1/3.3/3.4/3.7）、
>       27 号 principle（状态所有权 + 消息传递）、网关 Phase 1

## 一、背景与目标

**痛点**：主 agent ↔ subagent 通信是【散装多通道】——对话/控制/状态/结果/通知各有独立机制（steer_queue list 无锁、result_box、跨线程 emit、_done 事件），语义不统一、并发修复逐套做。

**目标**：
1. 统一通信抽象：**邮件 Message（id/sender/target/type/data）+ 邮局 Dispatcher（路由/调度/审计）+ 收件箱 Mailbox（无状态队列）+ 快递员 Transport（透明传输）**
2. mailbox 无状态（纯管道）——status/result/phase 回归 context 状态机
3. 消费逻辑归 Dispatcher（drain → 按 type 分发）
4. 为 A2A 预留（Message 协议已对齐 + Transport 接口可换后端）

## 二、设计原则（邮政类比 + 业界对照）

```
邮政系统类比（用户提出，采纳为架构蓝本）：
  邮件 Message：唯一编号 id + 寄件人 sender + 收件人 target + 内容
  邮局 Dispatcher：收件 → 查路由 → 投递（调度中心，不是中心化服务）
  收件箱 Mailbox：目标收件人的信箱（队列，无状态）
  快递员 Transport：底层传输（本地方法调用 / JSON-RPC / socket——透明）

业界对照：
  DSH inbox（源码实证）：每 agent 收件箱（next-turn/next-step 队列）
  A2A 协议（Google 2025）：message 结构（id/messageId/target/part）+ 
    AgentCard 自我发布（去中心化发现）+ Task 状态机
  actor 模型（Erlang/Akka）：通信只走消息，不共享
  → 统一 Dispatcher + Mailbox = DSH inbox + A2A 端点的进程内版

核心区分（投递 vs 存储）：
  投递机制：对话消息走邮局（邮箱是管道——瞬态）
  存储位置：收件方收到后【追加自己的 context.messages】（持久化）
  → 邮箱不存对话历史，只投递！"协议统一（邮箱投递），存储分离（各自 context）"
```

## 三、方案设计

### 3.1 Message（邮件——三要素必须实现）

```python
"""统一消息格式（对齐 A2A message 语义——id/sender/target 是投递要素）。"""

@dataclass
class Message:
    id: str            # 唯一编号（uuid4）——回执对应/去重/审计
    sender: str        # 寄件人 agent_id
    target: str        # 收件人 agent_id（路由依据——邮局靠它投递）
    type: str          # "message"(对话投递) | "steer"(控制) | "result"(结果)
                       # | "notify"(通知)
    data: Any          # 载荷
    timestamp: float   # 时间戳（A2A 对齐）
```

### 3.2 AgentMailbox（收件箱——无状态纯队列）

```python
"""每 agent 一个收件箱——纯队列管道（无状态，不持有执行状态）。"""

class AgentMailbox:
    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id                # 归属 agent（A2A 地址）
        self._inbox: queue.Queue[Message] = queue.Queue()

    def send(self, msg: Message) -> None:       # 投递（put 原子）
        self._inbox.put(msg)
    def drain(self) -> list[Message]:           # 取空（不丢新入队的）
        msgs = []
        while True:
            try:
                msgs.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return msgs
    # ★ 无 complete/fail/stop/snapshot——status/result/phase 在 context！
```

### 3.3 Dispatcher（邮局——路由/调度/审计）

```python
"""邮局：收件 → 查路由 → 投递。消费逻辑归这里（不在 mailbox）。"""

class Dispatcher:
    def __init__(self) -> None:
        # 路由表（进程内轻量——单机事实，非中心化服务）
        self._routes: dict[str, AgentMailbox] = {}
        self._lock = threading.Lock()

    # ── 注册（三层寻址的"表"层——注入优先，表兜底）──
    def register(self, mailbox: AgentMailbox) -> None:   # 进程内路由表
        with self._lock:
            self._routes[mailbox.owner_id] = mailbox
    def unregister(self, agent_id: str) -> None: ...

    # ── 投递（邮局收件 → 路由 → 投递）──
    def send(self, msg: Message) -> None:
        # 审计：谁发给谁（留痕）
        # ① 构造注入优先：已知目标引用（父↔子 spawn 时互相注入）
        #    → 直投（免查表）
        # ② 兜底：查路由表
        mailbox = self._routes.get(msg.target)
        if mailbox is None:
            raise UnknownTarget(msg.target)   # 无此收件人（fail-closed）
        mailbox.send(msg)                     # 投递（快递员，见 3.4）

    # ── 消费（谁取信谁处理——归 Dispatcher）──
    def consume(self, agent_id: str) -> list[Message]:
        """收件人取信（drain）——按 type 分发由调用方（manager）处理。"""
        return self._routes[agent_id].drain()
```

**三层寻址（回应"注册发现中心"顾虑——不用中心化服务）**：

```
① 父-子树（最常用路径）：spawn 时【构造注入引用】
   ——父创建子时互相注入 mailbox 引用 → send 直投（免查表）
   类比：妈妈知道孩子住哪（不用查通讯录）
② 进程内路由表（兜底）：Dispatcher._routes（id → mailbox）
   ——任意 agent 发给不认识的 target → 查表路由
   类比：本地通讯录（hosts 文件）——单机事实，不是"中心化架构"
③ 跨进程（未来 A2A）：AgentCard 自我发布 + 标准地址解析
   ——去中心化发现（无中心目录）
   类比：DNS——分布式，无中心
```

### 3.4 Transport（快递员——透明可换）

```python
"""快递员：底层传输协议（透明——本地调用/JSON-RPC/socket 无感切换）。"""

class Transport:
    """投递动作抽象（Dispatcher.send 内部调用——快递员不感知邮件内容）。"""
    def deliver(self, mailbox: AgentMailbox, msg: Message) -> None:
        mailbox.send(msg)              # Phase 1：本地方法调用（进程内）

# 未来（同 Gateway Transport 哲学）：
class RpcTransport(Transport):         # JSON-RPC（跨进程）
    def deliver(self, mailbox, msg): ...   # wire 投递
class SocketTransport(Transport):      # socket
    def deliver(self, mailbox, msg): ...
# → 本地/远程无感切换（Dispatcher 持有 transport 实例，可替换）
```

### 3.5 通信归一化（主↔sub 全走邮局）

```
主 agent → subagent：
  dispatcher.send(Message(id, "main", sub_id, "message", 对话内容))  # 对话投递（新能力！）
  dispatcher.send(Message(id, "main", sub_id, "steer", 补充指令))    # 控制

subagent → 主 agent：
  dispatcher.send(Message(id, sub_id, "main", "result", 结果))       # 结果回传

通知（提炼回执/持久化结果/流式增量）：
  dispatcher.send(Message(id, "bg", "main", "notify", 事件))         # 替换跨线程 emit

消费（Dispatcher 取信 → manager 按 type 分发）：
  "message" → 追加目标 context.messages（存储分离——对话历史仍在 context）
  "steer"   → 注入控制面（替换 steer_queue）
  "result"  → 回传处理（替换 result_box）
  "notify"  → 事件/UI 通知（事件总线保持单线程语义）

替换清单：
  context.steer_queue → 邮局 steer 消息
  result_box → 邮局 result 消息
  跨线程 emit → 邮局 notify 消息
  状态 status/result/phase → 留 context（mailbox 无状态，所有权归执行线程）
```

### 3.5.1 消费设计（drain 语义与处理顺序）

```
感知时机（轮询语义）：
  - agent 只在循环的检查点消费（每个 step 开头 drain）
  - 消息在循环外到达 → 先堆积 mailbox → 下一轮循环消费
  - "steer 下轮生效" = 轮询结构的自然结果（延迟上限 = 一轮）

drain 设计（批量取空 + 逐个分发）：
  def drain(self) -> list[Message]:
      msgs = []
      while True:
          try:
              msgs.append(self._inbox.get_nowait())
          except queue.Empty:
              break
      return msgs          # 一次取空（不丢新入队的）→ 消费方逐个处理

处理顺序（控制优先——安全属性）：
  for m in msgs: if m.type == "steer":    # ① 控制类最先（改方向/停必须即时）
      _apply_steer(m)
  for m in msgs: if m.type == "message":  # ② 对话投递 → 追加 context.messages
      context.messages.append(转换(m.data))
  for m in msgs: if m.type in ("result", "notify"):  # ③ 结果/通知最后
      ...
  注：queue.Queue 是 FIFO（同类内保序）；排序只调处理优先级，不破坏投递顺序

消费时机（本轮 vs 下轮生效）：
  - step 开头 drain（pre-step 之前）→ 消息及时
  - "message" 追加即生效（下次 LLM 调用读到）
  - "steer" 生效点 = 本轮 LLM 调用之后（排队到下轮——不打断本轮生成）

边界：
  - 消费失败：跳过 + 记日志（fail-continue——消息丢得起）
  - 背压：控制低频 OK；事件流（流式增量）未来有界 + 丢弃告警（TODO）
  - 跨进程（未来 A2A）：本地队列"消费即消失"；远程需回执语义
    （A2A task 状态：received/accepted/processing/completed）——Phase 3
```

### 3.6 状态所有权（mailbox 无状态——回归 context）

```
status/result/phase 在 context（AgentContext 状态机）——不在 mailbox
  - 执行线程（agent.chat 循环）独占写（complete/fail）
  - 控制线程 stop 只设 _stop_flag（不直接写 status）→ 循环线程检测转换
  - 读方：context.poll() / 快照（不消费）
→ mailbox 无状态：不误解、不越权；状态机一致性归 context（线程安全方案 3.3 落地）
```

### 3.7 A2A 预留（已对齐，非预留）

```
Message 结构 = A2A message 语义（id/sender/target/timestamp）——已实现
Transport 接口 = 可换后端（本地 → JSON-RPC → socket——透明）
发现机制 = 未来 AgentCard（跨进程）——进程内用路由表（3.3）
→ 无"预留字段"——邮件三要素是投递机制本身（用户拍板）
```

## 四、涉及点清单

```
① 通信归一化：主↔sub 全走邮局（对话投递/控制/结果/通知）
② mailbox 无状态：status/result/phase 回归 context（所有权归执行线程）
③ 对话投递能力：主 agent 可持续给 subagent 追加对话消息（新！）
④ 邮局 Dispatcher：路由（注入优先+表兜底）+ 审计 + 消费分发
⑤ Transport 透明：本地（Phase 1）/ JSON-RPC / socket（未来）
⑥ Message 三要素：id/sender/target 必须实现（uuid/路由/回执）
⑦ 并入职线程安全方案：steer→邮局（3.1）、状态（3.3）、跨线程 emit→邮局（3.4）、
   流式回调→邮局（3.7）
⑧ 网关审批表：uuid + 锁（独立保留——审批是同步等待非队列）
```

## 五、决策点

| # | 决策 | 选项 | 倾向 |
|---|---|---|---|
| D1 | mailbox 状态 | A. 无状态（纯队列，状态回归 context） B. 持有状态 | **A**——用户拍板，职责单一 |
| D2 | 消费逻辑归属 | A. Dispatcher 统一（drain+分发） B. manager 内实现 | **A**——邮局模型（用户提出，分发器职责） |
| D3 | 寻址机制 | A. 注入优先 + 路由表兜底 B. 仅路由表 C. 仅注入 | **A**——最常用零查找，兜底可靠（用户探讨结论） |
| D4 | Message 三要素 | A. 必须实现（id/sender/target） B. 预留 | **A**——用户拍板，投递机制本身 |
| D5 | Transport 抽象 | A. 接口抽象（本地实现，未来换后端） B. 记 TODO | **A**——接口成本低，与 Gateway 哲学一致 |
| D6 | 审批表 | A. uuid + 锁（独立于邮局） B. 也进邮局 | **A**——审批是同步等待（非队列语义） |
| D7 | 每 agent 一个邮箱 | A. 是（含主 agent） B. 仅 subagent | **A**——主 agent 统一收所有子结果 |

## 六、工业级落地考量（P0-9）

```
可靠性：
  - queue.Queue 原子投递（控制指令零丢失）
  - 状态所有权（执行线程独占写）→ 状态机一致（stop 不被 complete 覆盖）
  - 路由表注册/注销加锁；未知 target fail-closed（不静默丢信）
  - 邮箱无界队列（控制低频 OK）；事件流未来有界 + 背压（TODO）
  - 崩溃恢复：邮箱瞬态（丢消息可接受——对话消息在 context 持久化）
安全：
  - 控制面可靠性 = 安全属性（steer 丢失 → agent 继续错方向执行）
  - 消息审计（谁发给谁——Dispatcher 留痕，A2A 审计基础）
  - 审批表 uuid 防串线（D6）
  - 未来：消息鉴权（谁可发给谁）——Transport 换后端时实现（场景账）
记忆：
  - 对话投递走邮局但存储分离 → context.messages 持久化不变
  - 提炼/持久化通知走邮局（notify）——快照化（线程安全方案 3.5 不变）
可观测：
  - 邮局可加队列深度指标（背压可见）
  - 路由表可查询（agent 地址簿——多 agent 运维基础）
  - 状态快照在 context（status/phase/result 统一读）
```

## 七、验收标准

```
1. 邮局投递并发测试：send 100 + 并发 drain 零丢失
2. 控制优先处理：混投 steer+message → steer 先处理（测试断言处理顺序）
3. 状态所有权：stop 后循环线程正确转换（不被 complete 覆盖）——测试
4. 对话投递：主 agent 持续发消息给 subagent → 追加其 context.messages
5. 结果回传：subagent 完成 → 主 agent 邮局收到 result
6. 未知 target：send 报错（fail-closed），不静默丢信
7. 跨线程通知走邮局：非 owner emit 不再（事件总线单线程）
8. 网关审批表：并发 request_approval——id 不撞车、结果不串线
9. 全量回归 600+ 绿 + ruff 过
```

## 八、范围与阶段

```
Phase 1（本方案）：Message + AgentMailbox（无状态）+ Dispatcher（路由/审计）
  + 主↔sub 通信切换（steer/result/对话投递）
Phase 2：状态所有权切换（context 状态机 + stop 信号化）+
         跨线程 emit 收口（事件总线单线程化，通知走邮局）
Phase 3：Transport 换后端（JSON-RPC / socket——独立 TODO，场景账：
         本地单机暂无跨进程，接口先抽象不实现）

与线程安全方案关系：本方案【吸收】其 3.1/3.3/3.4/3.7
  （邮局解决）；3.2 审批表（uuid+锁）/3.5 持久化快照/3.6 压缩不动
  独立保留。
```
