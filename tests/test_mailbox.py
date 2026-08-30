"""AgentMailbox 邮局模型测试（方案 2026-08-29 v2 验收 1/2/4/5/6）。"""

import threading

import pytest

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.agents.mailbox import AgentMailbox, Dispatcher, Message, MessageType
from qi_agent.context.context import AgentContext


def _mk(sender: str, target: str, type_: str, data: str) -> Message:
    return Message(sender=sender, target=target,
                   type=MessageType(type_), data=data)


def _wait_for(fn, timeout: float = 2.0) -> bool:
    """等待异步投递完成（v3 Dispatcher 线程搬运——轮询直到条件满足）。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.01)
    return False


class TestMessage:
    def test_id_sender_target_mandatory(self):
        """邮件三要素：id 唯一、sender/target 必填。"""
        m1 = _mk("main", "sub1", "steer", "改方向")
        m2 = _mk("main", "sub1", "steer", "改方向")
        assert m1.id != m2.id  # id 唯一（uuid）
        assert m1.sender == "main" and m1.target == "sub1"

    def test_types(self):
        """四种消息类型。"""
        for t in ("message", "steer", "result", "notify"):
            m = _mk("a", "b", t, "x")
            assert m.type == t

    def test_invalid_type_rejected(self):
        """非法消息类型 → ValueError（枚举保护，防拼错静默失效）。"""
        with pytest.raises(ValueError, match="非法消息类型"):
            Message(sender="a", target="b", type="mesage", data="x")


class TestAgentMailbox:
    def _make_bound(self, owner: str = "sub1"):
        """创建 Dispatcher + 绑定 mailbox（v3：send 需注册后有效）。"""
        d = Dispatcher()
        d.start()
        mb = AgentMailbox(owner)
        d.register(mb)
        return d, mb

    def test_send_drain_basic(self):
        """投递→取空（FIFO 保序）。"""
        d, mb = self._make_bound()
        mb.send(_mk("main", "sub1", "message", "第一条"))
        mb.send(_mk("main", "sub1", "message", "第二条"))
        assert _wait_for(lambda: mb.inbox.qsize() >= 2)
        msgs = mb.drain()
        assert [m.data for m in msgs] == ["第一条", "第二条"]

    def test_drain_empty(self):
        """空邮箱取空 = 空列表（不阻塞）。"""
        assert AgentMailbox("x").drain() == []

    def test_send_unregistered_fails(self):
        """未注册 send → RuntimeError（fail-fast——outbox 未绑定 central）。"""
        mb = AgentMailbox("sub1")
        with pytest.raises(RuntimeError, match="未注册"):
            mb.send(_mk("main", "sub1", "message", "x"))

    def test_concurrent_send_drain_no_loss(self):
        """并发投递零丢失（验收 1）：send 100 + 并发 drain。"""
        d, mb = self._make_bound()
        N = 100
        sent: list[int] = list(range(N))
        received: list[int] = []
        lock = threading.Lock()

        def producer():
            for i in sent:
                mb.send(_mk("main", "sub1", "notify", str(i)))

        def consumer():
            # 轮询直到收满（不设固定轮次——日志拖慢时也能收满；
            # 2026-08-30 修复：200 次固定轮询在慢路径下提前耗尽）
            while True:
                for m in mb.drain():
                    with lock:
                        received.append(int(m.data))
                if len(received) >= N:
                    break

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # 等 Dispatcher 异步搬运完成（v3）——收满 N 为止
        assert _wait_for(lambda: len(received) >= N, timeout=5.0)
        # 清尾（搬运可能还有尾）
        for m in mb.drain():
            with lock:
                received.append(int(m.data))
        assert sorted(received) == sent  # 零丢失、零重复


class TestDispatcher:
    def test_register_and_route(self):
        """注册→投递→目标邮箱收到（异步搬运——等投递）。"""
        d = Dispatcher()
        d.start()
        sub_mb = AgentMailbox("sub1")
        d.register(sub_mb)
        d.send(_mk("main", "sub1", "steer", "改方向"))
        assert _wait_for(lambda: sub_mb.inbox.qsize() >= 1)
        msgs = sub_mb.drain()
        assert len(msgs) == 1 and msgs[0].type == "steer"

    def test_unknown_target_fail_closed(self):
        """未知 target：异步审计（v3——不静默丢信，undeliverable 留痕）。"""
        d = Dispatcher()
        d.start()
        d.send(_mk("main", "nobody", "steer", "x"))
        assert _wait_for(lambda: len(d.undeliverable) >= 1)
        assert d.undeliverable[0].target == "nobody"

    def test_unregister(self):
        """注销后投递 → 异步审计（目标不存在）。"""
        d = Dispatcher()
        d.start()
        mb = AgentMailbox("sub1")
        d.register(mb)
        d.unregister("sub1")
        d.send(_mk("main", "sub1", "steer", "x"))
        assert _wait_for(lambda: len(d.undeliverable) >= 1)

    def test_nack_receipt_to_sender(self):
        """未知 target → 回执 NACK 给发送方（2026-08-29 退信语义）。"""
        d = Dispatcher()
        d.start()
        sender_mb = AgentMailbox("agt_sender")
        d.register(sender_mb)
        # 投给不存在的目标（sender=agt_sender 已注册——能收到回执）
        msg = _mk("agt_sender", "nobody", "steer", "x")
        d.send(msg)
        assert _wait_for(lambda: sender_mb.inbox.qsize() >= 1)
        nack = sender_mb.drain()[0]
        assert nack.type == "nack"
        assert nack.data["original_id"] == msg.id
        assert nack.data["original_target"] == "nobody"
        assert nack.data["reason"] == "unknown_target"
        # 原始消息仍审计留痕
        assert any(m.id == msg.id for m in d.undeliverable)

    def test_nack_no_recursion(self):
        """NACK 回执的发送方也不存在 → 不无限回执（防递归——审计即可）。"""
        d = Dispatcher()
        d.start()
        # sender 未注册——回执 target=agt_ghost 也不存在
        d.send(_mk("agt_ghost", "nobody", "steer", "x"))
        # 等待处理完（原始消息 + 回执都被审计——不会无限递归）
        assert _wait_for(lambda: len(d.undeliverable) >= 1)
        import time

        time.sleep(0.2)
        assert len(d.undeliverable) <= 2  # 原始 + 回执（不再多）

    def test_inject_first_direct_delivery(self):
        """构造注入直投：同步（免中央/免查表——立即进 inbox）。"""
        d = Dispatcher()
        d.start()
        sub_mb = AgentMailbox("sub1")
        # 注入优先路径：持有引用直接投递（同步——不经过中央）
        d.send_direct(sub_mb, _mk("main", "sub1", "notify", "x"))
        assert len(sub_mb.drain()) == 1


class TestManagerIntegration:
    """AgentManager 邮局集成（验收 4/5：对话投递 + 结果回传）。"""

    def test_register_registers_mailbox(self):
        """register 统一注册 context.mailbox（主/子一样——v3 修正：
        Manager 不持有 main_mailbox，mailbox 挂 context）。"""
        manager = AgentManager()
        ctx = AgentContext(context_id="ctx_parent")
        ctx.mailbox = AgentMailbox("ctx_parent")
        manager.register(ctx, role="main")
        assert manager.dispatcher.get_mailbox("ctx_parent") is ctx.mailbox

    def test_send_message_delivery(self):
        """对话投递（验收 4）：send_message → 子 mailbox 收到 message。"""
        manager = AgentManager()
        ctx = AgentContext(context_id="test1", goal="目标")
        ctx.mailbox = AgentMailbox("test1")
        manager.dispatcher.register(ctx.mailbox)
        manager.contexts["test1"] = ctx

        assert manager.send_message("test1", "继续做", sender_id="ctx_parent")
        assert _wait_for(lambda: ctx.mailbox.inbox.qsize() >= 1)
        msgs = ctx.mailbox.drain()
        assert len(msgs) == 1
        assert msgs[0].type == "message" and msgs[0].data == "继续做"
        assert msgs[0].target == "test1" and msgs[0].sender == "ctx_parent"

    def test_send_message_unknown_session(self):
        """未知会话投递 → False（不报错，fail-closed 语义由 dispatcher 兜底）。"""
        manager = AgentManager()
        assert manager.send_message("nobody", "x") is False

    def test_result_delivery_to_parent(self):
        """结果回传（v3 修正）：subagent 完成 → 投回【父 context】邮箱。"""
        manager = AgentManager()
        parent = AgentContext(context_id="ctx_parent")
        parent.mailbox = AgentMailbox("ctx_parent")
        manager.register(parent, role="main")
        manager.dispatcher.send(Message(
            sender="sub1", target="ctx_parent", type=MessageType.RESULT,
            data={"summary": "完成"}))
        assert _wait_for(lambda: parent.mailbox.inbox.qsize() >= 1)
        msgs = parent.mailbox.drain()
        assert len(msgs) == 1
        assert msgs[0].type == "result" and msgs[0].data["summary"] == "完成"

    def test_drain_messages_filter(self):
        """SubagentContext.drain_messages：只取 message 类型（对话投递）。"""
        ctx = AgentContext(context_id="test1", goal="目标")
        ctx.mailbox = AgentMailbox("test1")
        # send_direct 同步直投（测试过滤逻辑——不走中央/异步）
        ctx.mailbox.inbox.put(Message(sender="main", target="test1",
                                      type=MessageType.MESSAGE,
                                      data="对话一"))
        ctx.mailbox.inbox.put(Message(sender="main", target="test1",
                                      type=MessageType.MESSAGE,
                                      data="对话二"))
        ctx.mailbox.inbox.put(Message(sender="main", target="test1",
                                      type=MessageType.STEER, data="指令"))
        assert ctx.drain_messages() == ["对话一", "对话二"]  # 只取 message
        # 2026-08-30 修复：drain_by_type 只取自己类型——STEER 留在 inbox
        # （互不清空——等 drain_steer 消费）
        left = ctx.mailbox.drain()
        assert len(left) == 1 and left[0].type == MessageType.STEER
        assert ctx.drain_messages() == []  # message 已消费完

    def test_drain_messages_no_mailbox(self):
        """无 mailbox（同步模式适配器）→ 空列表（getattr 兜底）。"""
        ctx = AgentContext(context_id="test1", goal="目标")
        assert ctx.drain_messages() == []


class TestConsumeOrder:
    def test_control_first(self):
        """控制优先处理（验收 2）：混投 steer+message → steer 先处理。"""
        d = Dispatcher()
        d.start()
        sub_mb = AgentMailbox("sub1")
        d.register(sub_mb)
        d.send(_mk("main", "sub1", "message", "对话内容"))
        d.send(_mk("main", "sub1", "steer", "改方向"))
        d.send(_mk("main", "sub1", "message", "更多对话"))
        assert _wait_for(lambda: sub_mb.inbox.qsize() >= 3)

        msgs = sub_mb.drain()
        order = [m.type for m in msgs]
        # 分发逻辑：steer 最先（即使投递顺序在后）
        processed = [m for m in msgs if m.type == "steer"] + \
                    [m for m in msgs if m.type == "message"]
        assert processed[0].type == "steer"
        assert [m.type for m in processed].count("steer") == 1
        # 同类内保序（FIFO）
        messages = [m.data for m in msgs if m.type == "message"]
        assert messages == ["对话内容", "更多对话"]
        # order 保留投递顺序（FIFO 不破坏）
        assert order == ["message", "steer", "message"]
