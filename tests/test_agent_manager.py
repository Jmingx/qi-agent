"""AgentManager 统一控制台测试（方案 2026-08-24-AgentManager统一控制台）。

验证：register（主/子 agent 注册）+ spawn/steer/stop/poll 接口兼容。
"""

import time

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext, ContextStatus


def test_register_main_agent() -> None:
    """主 agent 注册：返回 id + 可在控制台查询。"""
    mgr = AgentManager()
    ctx = AgentContext(persist=True)
    agent_id = mgr.register(ctx, role="main")
    assert agent_id == ctx.id
    assert mgr.poll(agent_id) == ContextStatus.IDLE  # 主 agent 新建未开始


def test_register_multiple() -> None:
    """多 agent 注册互不干扰。"""
    mgr = AgentManager()
    c1 = AgentContext()
    c2 = AgentContext()
    id1 = mgr.register(c1, role="main")
    id2 = mgr.register(c2, role="subagent")
    assert id1 != id2
    assert mgr.poll(id1) is not None
    assert mgr.poll(id2) is not None


def test_poll_unknown_id_returns_none() -> None:
    """poll 未知 id → None。"""
    mgr = AgentManager()
    assert mgr.poll("nope") is None


def test_steer_unknown_id_returns_false() -> None:
    """steer 未知 id → False。"""
    mgr = AgentManager()
    assert mgr.steer("nope", "msg") is False


def test_stop_unknown_id_returns_false() -> None:
    """stop 未知 id → False。"""
    mgr = AgentManager()
    assert mgr.stop("nope") is False


def test_steer_main_agent() -> None:
    """steer 主 agent：指令走邮局（下轮生效——等异步投递）。"""
    import time

    mgr = AgentManager()
    ctx = AgentContext()
    agent_id = mgr.register(ctx, role="main")
    assert mgr.steer(agent_id, "改方向")
    # v3：steer 走 mailbox（Dispatcher 异步搬运）——等待投递
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if ctx.drain_steer() == ["改方向"]:
            break
        time.sleep(0.01)
    assert ctx.drain_steer() == []  # 已消费（二次 drain 空）


def test_steer_sender_is_current_context() -> None:
    """steer 的 sender = 当前 context_id（调用者身份——2026-08-29 修正：
    不是 parent（创建关系≠控制发起者），谁调填谁的 context id）。"""
    import time

    mgr = AgentManager()
    parent = AgentContext(context_id="ctx_parent")
    mgr.register(parent, role="main")
    sub = mgr.spawn("目标", parent_id="ctx_parent")

    # 主 context（当前）steer 子——sender 填当前 context_id
    assert mgr.steer(sub.id, "改方向", sender_id="ctx_parent")
    # 等消息到达（不 drain_steer——直接读 inbox 验证 sender）
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        msgs = sub.mailbox.drain()
        if msgs:
            break
        time.sleep(0.01)
    assert len(msgs) == 1
    assert msgs[0].sender == "ctx_parent"  # 当前 context_id（调用者身份）
    assert msgs[0].type == "steer" and msgs[0].data == "改方向"

    # 默认 sender = unknown（外部/未知发起——保守不猜）
    assert mgr.steer(sub.id, "再来")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        msgs = sub.mailbox.drain()
        if msgs:
            break
        time.sleep(0.01)
    assert len(msgs) == 1
    assert msgs[0].sender == "unknown"
    assert msgs[0].data == "再来"


def test_stop_main_agent() -> None:
    """stop 主 agent：flag 置位 + 状态 STOPPED。"""
    mgr = AgentManager()
    ctx = AgentContext()
    agent_id = mgr.register(ctx, role="main")
    ctx.begin_chat()  # 主 agent 运行中
    assert mgr.stop(agent_id)
    assert ctx.should_stop()
    assert ctx.status == ContextStatus.STOPPED


def test_unregister() -> None:
    """unregister 后 poll 返回 None。"""
    mgr = AgentManager()
    ctx = AgentContext()
    agent_id = mgr.register(ctx, role="main")
    mgr.unregister(agent_id)
    assert mgr.poll(agent_id) is None


def test_get_context() -> None:
    """get_context：按 id 取数据载体（CLI 数据访问的唯一入口）。"""
    mgr = AgentManager()
    ctx = AgentContext()
    agent_id = mgr.register(ctx, role="main")
    got = mgr.get_context(agent_id)
    assert got is ctx
    assert mgr.get_context("nope") is None


def test_run_executes_via_pool() -> None:
    """manager.run：执行权归还 Manager——pool 取执行者 → chat → release。

    CLI 不持有 agent（执行者在 pool 内即用即弃）。
    """
    from qi_agent.llm import ChatResult

    class Fake:
        def chat(self, messages, tools=None):
            return ChatResult(content="ok", tool_calls=[],
                              assistant_message={"role": "assistant",
                                                 "content": "ok"},
                              usage=None)

    import unittest.mock as mock
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")
    with mock.patch.object(factory, "LLMClient", lambda key: Fake()):
        reply = mgr.run(ctx.id, "你好")
    assert reply == "ok"
    assert mgr.pool.active_count == 0  # 执行后额度回收（即用即弃）


class _SlowClient:
    """慢客户端：spawn 后台任务用（等待完成）。"""

    def __init__(self, delay: float = 0.05):
        self.delay = delay

    def chat(self, messages, tools=None):
        time.sleep(self.delay)
        return type("R", (), {"content": "ok", "tool_calls": [],
                              "assistant_message": {"role": "assistant",
                                                    "content": "ok"},
                              "usage": None})()


def test_spawn_still_works() -> None:
    """spawn 子任务（接口兼容）：后台跑 + 结果写回。"""
    mgr = AgentManager()
    ctx = mgr.spawn(goal="g", context="c",
                    client_factory=lambda: _SlowClient(0.01))
    assert mgr.poll(ctx.id) == ContextStatus.RUNNING  # spawn 即运行
    ctx.wait(timeout=5)
    assert ctx.status == ContextStatus.COMPLETED
