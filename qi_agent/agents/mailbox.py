"""AgentMailbox 统一通信（v3 中央队列演进——方案 2026-08-29-中央队列演进）。

对齐业界（Kafka topic / A2A 端点 / 消息代理模式）：
  agent 只做两件事：send（丢自己邮箱——outbox 引用 central）+
                    drain（取自己 inbox）
  Dispatcher = 独立 daemon 线程（消息代理）：中央队列阻塞 get →
               查路由 → 投目标 inbox（零轮询 + FIFO 顺序）
  A2A 预留：_deliver 本地/远程分支 + foreign 引用（Phase 2 实现）

角色：
  Producer（投递者）→ 任意线程调 mailbox.send（= central.put）
  Dispatcher（搬运者）→ 独立线程：central.get → 路由 → inbox.put
  Consumer（消费者）→ mailbox 归属 agent：drain() 取自己 inbox

关键设计（用户拍板 2026-08-29）：
  outbox 不是独立队列——【直接引用 central】（register 时赋值）
  → send 一步入中央（无两步/无中间层）+ 语义保留（丢自己邮箱）
  → 只有一个中央队列（所有 outbox 指向它）——多路复用问题消失
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from qi_agent.util import generate_id
from qi_agent.logging_setup import get_message_logger


class MessageType(str, Enum):
    """消息类型（A2A 对齐——type 字段枚举化，防拼错字符串静默失效）。

    MESSAGE: 对话投递（主→sub 追加对话消息，存储分离——收方追加
             自己的 context.messages）
    STEER:   控制指令（补充指令，收方下轮生效）
    RESULT:  结果回传（sub→主，任务完成结果）
    NOTIFY:  通知（事件回执/流式增量——跨线程通知走邮箱）
    """

    MESSAGE = "message"
    STEER = "steer"
    RESULT = "result"
    NOTIFY = "notify"
    NACK = "nack"  # 投递失败回执（2026-08-29：未知 target → 回执给发送方）


@dataclass
class Message:
    """统一消息格式（对齐 A2A message 语义——id/sender/target 是投递要素）。

    Attributes:
        id: 唯一编号（generate_id("msg")——统一格式 类型_时间戳_uuid）
        sender: 寄件人 agent_id
        target: 收件人 agent_id（路由依据——本地 id 或未来 A2A 三元组）
        type: 消息类型（MessageType 枚举——message/steer/result/notify）
        data: 载荷（对话内容/指令/结果/事件）
        timestamp: 时间戳（A2A 对齐）
    """

    sender: str
    target: str
    type: MessageType
    data: Any
    id: str = field(default_factory=lambda: generate_id("msg"))
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        """类型校验（str-Enum 有隐式 fallback——拼错字符串不报错，
        这里显式强制：type 必须是 MessageType 成员）。"""
        if not isinstance(self.type, MessageType):
            raise ValueError(
                f"非法消息类型: {self.type!r}（应为 MessageType 成员: "
                f"{[t.value for t in MessageType]}）")


class AgentMailbox:
    """每 agent 一个邮箱——outbox(→central 引用) + inbox 分离。

    send：丢【自己 outbox】（= central.put——register 绑定后一步入中央）
    drain：取【自己 inbox】（归属者消费——Dispatcher 投来的）
    """

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id  # 归属 agent（A2A 地址）
        self.outbox: queue.Queue | None = None  # register 时绑定 central
        self.inbox: queue.Queue[Message] = queue.Queue()
        self._drain_lock = threading.Lock()  # drain_by_type 原子（与 put 竞争）

    def send(self, msg: Message) -> None:
        """投递（agent 调用——丢自己 outbox = 中央队列）。

        Raises:
            RuntimeError: 未注册（outbox 未绑定 central）——fail-fast
        """
        if self.outbox is None:
            raise RuntimeError(
                f"mailbox 未注册（outbox 未绑定 central）: {self.owner_id}")
        self.outbox.put(msg)
        # 日志（message.log——投递入口审计；owner=谁发的邮箱；
        # 2026-08-30 补 data——完整内容打印，方便定位）
        get_message_logger().info(
            "send owner=%s sender=%s target=%s type=%s id=%s data=%s",
            self.owner_id, msg.sender, msg.target, msg.type.value, msg.id,
            msg.data)

    def drain(self) -> list[Message]:
        """取空 inbox（不丢新入队的——get_nowait 循环到 empty）。

        Returns:
            全部待处理消息（消费即消失——本地队列语义）
        """
        msgs: list[Message] = []
        while True:
            try:
                msgs.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return msgs

    def drain_by_type(self, type_: MessageType) -> list[Message]:
        """只取指定类型的消息（2026-08-30 修复：drain_steer/drain_messages
        不能共用 drain()——它会连带清掉其他类型的消息，互相清空）。

        锁内遍历（与 Dispatcher put 竞争原子）：非目标类型重新入队
        （队尾——顺序近似保持；消息少频率低，正确性优先）。

        Returns:
            该类型的消息（其他类型留 inbox——等对应消费者取）
        """
        picked: list[Message] = []
        rest: list[Message] = []
        with self._drain_lock:
            while True:
                try:
                    msg = self.inbox.get_nowait()
                except queue.Empty:
                    break
                (picked if msg.type == type_ else rest).append(msg)
            for m in rest:  # 非目标类型放回（不消费）
                self.inbox.put(m)
        return picked


class ForeignMailbox(Protocol):
    """A2A 对外通道（Phase 2 实现——本方案只留接口）。

    职责：本地消息（target=远程）→ 序列化 → 网络发送；
          远程消息 → 反序列化 → 投到本地 inbox。
    """

    def send(self, msg: Message) -> None: ...
    def start(self) -> None: ...


class Dispatcher(threading.Thread):
    """消息代理（邮局）：中央队列 + 路由——独立 daemon 线程。

    v3 演进（方案 2026-08-29）：从"同步查表投递"升级为
    "异步搬运"——所有消息汇聚中央队列，单线程阻塞 get（零轮询）。

    职责：
      ① 路由注册（register：agent_id → mailbox + 绑定 outbox=central）
      ② 中央队列（send 丢进来——Dispatcher 阻塞 get）
      ③ 投递（_deliver：本地 routes / 远程 foreign / fail-closed）
      ④ 优雅关停（stop 事件）
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="mailbox-dispatcher")
        self._central: queue.Queue[Message] = queue.Queue()  # 中央队列
        self._routes: dict[str, AgentMailbox] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._foreign: ForeignMailbox | None = None  # A2A 预留（Phase 2）
        # 不可投递消息审计（fail-closed 留痕——可观测）
        self.undeliverable: list[Message] = []

    # ── 生命周期 ─────────────────────────────────────────────────────
    def start(self) -> None:
        """启动 Dispatcher 线程（幂等——防重复 start）。"""
        if not self.is_alive():
            super().start()

    def stop(self, timeout: float = 2.0) -> None:
        """优雅关停（stop 事件唤醒 → 线程退出）。

        Args:
            timeout: 等待线程退出的超时（秒）——防悬挂
        """
        self._stop.set()
        self.join(timeout=timeout)

    # ── 路由注册 ─────────────────────────────────────────────────────
    def register(self, mailbox: AgentMailbox) -> None:
        """注册收件箱（agent_id → mailbox + 绑定 outbox=central）。"""
        with self._lock:
            mailbox.outbox = self._central  # outbox 引用 central（用户拍板）
            self._routes[mailbox.owner_id] = mailbox

    def unregister(self, agent_id: str) -> None:
        """注销（任务结束清理——outbox 解绑防误发）。"""
        with self._lock:
            mailbox = self._routes.pop(agent_id, None)
            if mailbox is not None:
                mailbox.outbox = None

    def get_mailbox(self, agent_id: str) -> AgentMailbox | None:
        """按 id 取收件箱（查询——不投递）。"""
        with self._lock:
            return self._routes.get(agent_id)

    def set_foreign(self, foreign: ForeignMailbox | None) -> None:
        """设置 A2A 对外通道（Phase 2——None = 纯本地模式）。"""
        self._foreign = foreign

    # ── 投递入口（任意线程调——丢中央，不阻塞）───────────────────────
    def send(self, msg: Message) -> None:
        """投递：丢中央队列（Dispatcher 线程异步搬运——不阻塞）。

        注意：直接调 Dispatcher.send 是"任意投递"（查 target 路由）；
              agent 一般用自己 mailbox.send（= 丢同一中央）。
        """
        self._central.put(msg)

    def send_direct(self, mailbox: AgentMailbox, msg: Message) -> None:
        """构造注入直投（同步——免中央/免查表，父↔子已知引用）。

        与 send（异步中央）不同：直投【立即】进目标 inbox——
        持有引用时最常用路径（spawn 时父↔子已互知）。
        """
        mailbox.inbox.put(msg)

    # ── Dispatcher 线程主循环（搬运）──────────────────────────────────
    def run(self) -> None:
        """搬运循环：阻塞等中央队列 → 查路由 → 投目标 inbox。

        中央队列 = 唯一等待点（零轮询——get 阻塞，有消息才醒）。
        """
        while not self._stop.is_set():
            try:
                msg = self._central.get(timeout=0.5)  # 可被 stop 唤醒
            except queue.Empty:
                continue
            self._deliver(msg)

    def _deliver(self, msg: Message) -> None:
        """路由决策（A2A 预留——本地/远程分支）。

        本地：查 routes → 目标 inbox.put
        远程：foreign.send（Phase 2）
        未知：fail-closed——回执 NACK 给发送方（不静默丢信）

        NACK 防递归：回执本身不再生成回执（target=sender 若也不存在
        → 直接 fail-closed 审计，不无限回执）。
        """
        if msg.type == MessageType.NACK:
            # 回执不再回执（防递归）——未知则审计
            with self._lock:
                target = self._routes.get(msg.target)
            if target is not None:
                target.inbox.put(msg)
                get_message_logger().info(
                    "nack-deliver sender=%s target=%s id=%s data=%s",
                    msg.sender, msg.target, msg.id, msg.data)
            else:
                with self._lock:
                    self.undeliverable.append(msg)
                get_message_logger().warning(
                    "nack-orphan sender=%s target=%s id=%s data=%s",
                    msg.sender, msg.target, msg.id, msg.data)
            return
        with self._lock:
            target = self._routes.get(msg.target)
        if target is not None:
            target.inbox.put(msg)  # 本地投递（队列）
            get_message_logger().info(
                "deliver sender=%s target=%s type=%s id=%s data=%s",
                msg.sender, msg.target, msg.type.value, msg.id, msg.data)
            return
        if self._foreign is not None:
            self._foreign.send(msg)  # 远程投递（A2A——Phase 2）
            return
        # fail-closed：未知 target——回执 NACK 给发送方（退信语义）
        with self._lock:
            self.undeliverable.append(msg)  # 审计留痕（原始消息）
        get_message_logger().warning(
            "undeliverable sender=%s target=%s type=%s id=%s data=%s",
            msg.sender, msg.target, msg.type.value, msg.id, msg.data)
        nack = Message(
            sender="dispatcher",
            target=msg.sender,  # 回执投回发送方
            type=MessageType.NACK,
            data={
                "original_id": msg.id,       # 哪条消息投递失败
                "original_type": msg.type.value,
                "original_target": msg.target,
                "reason": "unknown_target",
            },
        )
        # 回执也走中央队列（异步投递——_deliver 递归处理 NACK 防环）
        self._central.put(nack)
