# AgentMailbox 中央队列演进方案（v3：异步消息代理 + A2A 预留）

> 日期：2026-08-29
> 状态：待评审
> 关联：`2026-08-29-AgentMailbox统一通信方案.md`（v2 邮局模型——已落地，
>       本方案为其 v3 演进）、`27-并发模型演进教训.md`（状态所有权）、
>       并发排查（2026-08-28：跨线程通信是核心问题域）
> 决策记录：用户拍板——① 消息投递与路由解耦（agent 只丢自己邮箱，
>       Dispatcher 独立承接投递/路由）；② 中央队列方案（消灭 N 队列
>       多路复用瓶颈）；③ A2A 预留（target 拓展 ip:端口:agent_id +
>       foreignMailbox 对外通道）
> 修正记录（2026-08-29 用户评审）：④ **AgentManager 不持有 main_mailbox**
>       （越权）——mailbox 统一挂 context（主/子一样）；Manager 只管理
>       context；"main" 魔法字符串替换为 parent_id（父 context id）
>       ⑤ **AgentContext 必备 mailbox**（构造即创建——A2A/team 场景
>       每个 agent 都是通信端点，不是可选）
>       ⑥ **spawn 统一走 register()**（dict + 邮局路由 + 事件上报）
>       ⑦ **失败通知统一**（意外崩溃也投 RESULT 给父）
>       ⑧ **steer 收口 mailbox**（MessageType.STEER 落地——控制指令
>       走邮局统一通道）；**stop 保持 Event 信号**（立即生效——信号
>       语义不是消息流）

## 一、v2 现状与问题（为什么演进）

```
v2 邮局模型（已落地——mailbox.py）：
  AgentMailbox：单队列（send 投递 / drain 消费）
  Dispatcher：路由表（register/unregister + send 查表投递 +
               send_direct 直投）——【同步、非线程】

问题（用户评审指出）：
  ① 逻辑耦合：agent 要发消息 → 自己调 Dispatcher.send（知道路由）；
     agent 要收 → 自己调 mailbox.drain（知道消费时机）
     → 收发逻辑耦合在 agent 循环里（delegate_task 的 watcher）
  ② 多路复用瓶颈：若每 agent 独立 outbox 队列 → Dispatcher 要
     监听 N 个队列（轮询/多阻塞）——长期演进瓶颈（用户预判）
  ③ A2A 无预留：target 只有本地 agent_id——无法寻址远程 agent
```

## 二、目标架构（v3：异步消息代理）

```
┌─────────────────────────────────────────────────────────────┐
│ Agent（只做两件事）                                          │
│   send(msg)  → 丢【自己的 outbox】——完事（不碰路由）         │
│   drain()    → 取【自己的 inbox】（归属者消费）               │
├─────────────────────────────────────────────────────────────┤
│ Dispatcher（独立线程——消息代理/邮局）                        │
│   ① 中央队列（central）：所有 outbox 消息【汇聚】到这里       │
│      → 单线程只等【一个】队列（阻塞 get——零轮询！）          │
│   ② 路由注册：agent 注册（agent_id → inbox）                 │
│   ③ 投递：从中央取 → 查路由 → 投到目标 inbox                 │
│   ④ 远程（A2A）：target 是远程 → foreign_mailbox.send        │
├─────────────────────────────────────────────────────────────┤
│ ForeignMailbox（对外通道——A2A 预留，Phase 2）                │
│   本地消息（target=远程）→ 序列化 → 网络发送                 │
│   远程消息 → 反序列化 → 投到本地 inbox                       │
└─────────────────────────────────────────────────────────────┘

关键设计：
  中央队列 = 把所有 outbox【合成一个】——Dispatcher 阻塞等一个队列
    → 多路复用问题消失（零轮询 + 零忙等 + 天然 FIFO 顺序）
    → 类比：Kafka topic（汇聚）+ 消费者（分发）
  agent 解耦 = 只丢自己 outbox + 取自己 inbox（不碰路由）
  路由集中 = Dispatcher 一处管（未来 A2A 只改路由决策）
```

## 三、方案设计

### 3.1 AgentMailbox 拆双队列（outbox/inbox）

```python
class AgentMailbox:
    """每 agent 一个邮箱——outbox 发件 + inbox 收件分离。"""

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id
        self.outbox: queue.Queue[Message] = queue.Queue()  # 发件（自己丢）
        self.inbox: queue.Queue[Message] = queue.Queue()   # 收件（Dispatcher 投）

    def send(self, msg: Message) -> None:
        """agent 调用：丢【自己 outbox】（完事——不碰路由）。"""
        self.outbox.put(msg)

    def drain(self) -> list[Message]:
        """agent 调用：取【自己 inbox】（归属者消费）。"""
        msgs = []
        while True:
            try:
                msgs.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return msgs
```

### 3.2 Dispatcher 独立线程 + 中央队列

```python
class Dispatcher(threading.Thread):
    """消息代理：中央队列 + 路由（独立线程——异步搬运）。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._central: queue.Queue[Message] = queue.Queue()  # 中央队列
        self._routes: dict[str, AgentMailbox] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ── 路由注册 ────────────────────────────────────────────
    def register(self, mailbox: AgentMailbox) -> None:
        with self._lock:
            self._routes[mailbox.owner_id] = mailbox

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._routes.pop(agent_id, None)

    # ── 投递入口（agent/任意线程调——丢中央）──────────────
    def send(self, msg: Message) -> None:
        """投递：丢中央队列（Dispatcher 线程异步搬运——不阻塞）。"""
        self._central.put(msg)

    # ── Dispatcher 线程主循环（搬运）────────────────────────
    def run(self) -> None:
        """搬运循环：阻塞等中央队列 → 查路由 → 投目标 inbox。"""
        while not self._stop.is_set():
            try:
                msg = self._central.get(timeout=0.5)  # 阻塞等（可被 stop 唤醒）
            except queue.Empty:
                continue
            self._deliver(msg)

    def _deliver(self, msg: Message) -> None:
        """路由决策（A2A 预留——本地/远程分支）。"""
        with self._lock:
            target = self._routes.get(msg.target)
        if target is not None:
            target.inbox.put(msg)          # 本地投递（队列）
        elif self._foreign is not None:
            self._foreign.send(msg)        # 远程投递（A2A——Phase 2）
        else:
            # fail-closed：未知 target 不静默丢信（审计/报错）
            self._on_undeliverable(msg)

    def stop(self) -> None:
        """优雅关停（线程退出）。"""
        self._stop.set()
```

```
关键点：
  ① 中央队列 = 唯一等待点（Dispatcher 阻塞 get——零轮询）
  ② agent 解耦：send 丢自己 outbox → 谁搬？——Dispatcher 线程
     从 central 取 → 投目标 inbox（但 outbox→central 谁搬？见 3.3）
  ③ 路由集中 + fail-closed（未知 target 不静默丢）
  ④ daemon 线程 + stop 事件（进程退出/优雅关停）
```

### 3.3 完整消息流（outbox=central 引用——用户拍板）

```
关键设计（用户拍板）：outbox 不是独立队列——【直接引用 central】！
  register 时赋值：mailbox.outbox = self._central
  → send = 丢 outbox = 丢 central（同一队列！）
  → 消灭"独立 outbox + 同步搬 central"的两步

代码形态：
  class AgentMailbox:
      def __init__(self, owner_id):
          self.owner_id = owner_id
          self.outbox = None            # register 时绑定 central
          self.inbox = queue.Queue()    # 收件（Dispatcher 投）

      def send(self, msg):
          if self.outbox is None:
              raise RuntimeError(f"mailbox 未注册: {self.owner_id}")
          self.outbox.put(msg)          # = central.put（同一队列）

      def drain(self):
          # 取自己 inbox（归属者消费）
          ...

  # Dispatcher.register 绑定：
  def register(self, mailbox):
      mailbox.outbox = self._central    # outbox 引用 central
      self._routes[mailbox.owner_id] = mailbox

消息流（最终）：
  agent.send(msg) → outbox.put（= central.put）
  → Dispatcher 线程阻塞 get（central）
  → _deliver（本地/远程）→ 目标 inbox.put
  → agent.drain() 取

为什么好：
  ① 简洁：send 一步入中央（无两步/无中间层）
  ② 语义保留：agent 视角"丢自己的邮箱"（outbox 概念在）
  ③ 多路复用消失：只有一个 central（所有 outbox 指向它）
  ④ register 时机自然：注册前 send 显式报错（fail-fast）
```

### 3.4 A2A 预留（Phase 2——接口先抽象）

```
target 拓展：
  v2/v3 本地：target = "main" / "sub1"（agent_id）
  A2A：target = "ip:port:agent_id"（三元组——远程可寻址）

路由决策（_deliver 扩展）：
  def _deliver(self, msg):
      if 是本地 agent_id:
          routes[target].inbox.put(msg)      # 本地队列
      elif self._foreign:
          self._foreign.send(msg)            # 远程（A2A）
      else:
          fail-closed（未知/无远程通道）

ForeignMailbox（对外通道——Phase 2 实现，本方案只留接口）：
  class ForeignMailbox(Protocol):
      def send(self, msg: Message) -> None: ...    # 本地 → 远程
      def start(self) -> None: ...                  # 监听远程 → 本地 inbox
  → Dispatcher 持有 foreign 引用（None = 纯本地模式）

A2A 完整形态：
  每 agent 地址 = ip:port:agent_id（三元组）
  本地 agent：routes 表（进程内）
  远程 agent：foreign_mailbox（网络）
  → 路由决策一处改（_deliver）——本地/远程无感
```

### 3.5 控制通道收口（steer 走 mailbox + stop 保持 Event——修正 ⑧）

```
现状（两套平行机制）：
  steer：context.steer_queue（queue.Queue）——控制线程 put，子循环 drain
  stop：context._stop_flag（threading.Event）——立即生效信号
  → 与 mailbox 平行——控制通道没走邮局（MessageType.STEER 定义了没用）

收口设计：
  ✅ steer 走 mailbox（MessageType.STEER——消息流）：
      manager.steer(context_id, msg) → dispatcher.send(
          Message(type=STEER, target=子id, data=msg))
      → 子消费：drain_steer 从 mailbox 读（STEER 类型）
      → context.steer_queue 废弃（收敛到 mailbox）
      → A2A 对齐（控制指令 = 消息）+ STEER 类型落地 + 路由复用

  ✅ stop 保持 Event（信号——不是消息流）：
      现状 _stop_flag 立即生效（中断语义）——保持不变
      → 消息化反而慢（下轮消费）——stop 要立即中断
      → 职责分离：消息走邮箱（steer/result/message），信号走 Event（stop）
      → 对齐"消息/状态分离"哲学（消息流 vs 一次性信号）

  消费时序（子循环 pre-step）：
      drain_steer → 从 mailbox 取 STEER 消息（追加 [父补充指令]）
      stop_hook → 查 _stop_flag（立即——不等消息）
      → 两种通道各自消费（消息/信号不混）
```

## 四、决策点

| # | 决策 | 选项 | 倾向 |
|---|---|---|---|
| D1 | Dispatcher 线程模型 | A. 独立 daemon 线程（阻塞等中央） B. 事件触发（异步回调） | **A**——简单 + 阻塞等零轮询；事件触发 = 过度设计（单机） |
| D2 | outbox 与 central 关系 | A. 纯中央（send 直接丢 central） B. **outbox 引用 central（register 赋值）** | **B**（用户拍板）——send 一步入中央 + outbox 概念保留（丢自己邮箱语义）；未注册 send 显式报错（fail-fast） |
| D3 | 中央队列有界性 | A. 无界（控制低频 OK） B. 有界 + 背压 | **A**——单机 agent 低频；有界背压 = 未来高吞吐事件流再做 |
| D4 | 失败处理 | A. fail-closed（未知 target 报错/审计） B. 静默丢弃 | **A**——不静默丢信（对齐 v2 fail-closed） |
| D5 | A2A 预留实现 | A. 接口抽象（_deliver 分支 + foreign 引用） B. 记 TODO 不抽象 | **A**——接口抽象成本低（几行）；实现 Phase 2（场景账：本地暂无远程） |
| D6 | Dispatcher 生命周期 | A. AgentManager 创建/关停（随 manager） B. 全局单例 | **A**——随 manager（对齐 v2 现状：manager 持有 dispatcher） |

## 五、工业级落地考量（P0-9）

```
可靠性：
  - 中央队列阻塞 get（零轮询 + 消息顺序保证 FIFO）
  - 失败 fail-closed（未知 target 审计不静默丢——对齐 v2）
  - Dispatcher 崩溃 → 消息滞留中央队列（重启可恢复——但队列是内存态，
    进程退出丢消息可接受：对话消息在 context 持久化，邮箱只传瞬态信号）
  - 优雅关停（stop 事件——daemon 线程 + 超时退出）
  - 无界队列内存风险：控制低频（每 agent 每轮几条）——单机几十 agent
    无瓶颈；未来高吞吐加有界 + 背压（D3 记 TODO）

安全：
  - 路由 fail-closed（未知 target 不投错——防消息串线）
  - A2A 未来：消息鉴权（谁可给谁发）+ 传输加密（Phase 2 设计）
  - 审计（Dispatcher 可记录投递轨迹——A2A 审计基础）

记忆：
  - 对话投递仍走邮箱但存储分离（context.messages 持久化不变）
  - 提炼/持久化通知走邮箱（notify）——快照化不变

可观测：
  - 中央队列深度指标（背压可见——堆积预警）
  - 投递审计日志（谁发给谁——A2A 审计基础）
  - Dispatcher 线程状态（存活/积压——监控）
```

## 六、验收标准

```
1. agent.send 丢自己邮箱（不碰路由）→ Dispatcher 异步投递到目标 inbox
2. Dispatcher 单线程阻塞等中央队列（零轮询——N 队列多路复用消失）
3. 并发测试：多 agent 并发 send 100 条 → 目标 inbox 全收到（零丢失）
4. 路由 fail-closed：未知 target → 审计不静默丢
5. 优雅关停：Dispatcher.stop() → 线程退出（不悬挂）
6. A2A 预留：_deliver 有远程分支 + foreign 引用（None = 纯本地）
7. 全量回归 600+ 绿 + ruff 过
```

## 七、范围与阶段

```
Phase 1（本方案）：
  - AgentMailbox 双队列语义（send 丢 outbox/central，drain 取 inbox）
  - Dispatcher 独立线程 + 中央队列（阻塞 get 搬运）
  - 路由 fail-closed + 优雅关停
  - 现有调用点适配（agent_manager/subagent/delegate_task）
  - 测试：并发零丢失 / 路由 / 关停 / A2A 预留

Phase 2（A2A——独立 TODO，场景账：本地单机暂无远程）：
  - target 三元组（ip:port:agent_id）
  - ForeignMailbox（网络传输 + 鉴权 + 序列化）
  - 路由决策远程分支实现

与 v2 关系：v2 的 Dispatcher.send/send_direct 语义升级为
  "丢中央 + 异步搬运"（send_direct 可保留为本地快速路径——构造注入）
```

## 八、一句话总结

```
v3 = 中央队列 + 独立 Dispatcher 线程 + 双邮箱（outbox/inbox 语义）
  → agent 只丢自己邮箱 + 取自己邮箱（解耦）
  → Dispatcher 阻塞等一个中央队列（零轮询——多路复用问题消失）
  → 路由集中一处（A2A 只改 _deliver——加远程分支 + foreign）
  → 对齐 Kafka topic / A2A 端点 / 消息代理模式
```
