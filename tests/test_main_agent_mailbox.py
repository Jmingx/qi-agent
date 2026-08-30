"""主 agent 消费邮箱消息测试（2026-08-30 修复：主 agent mailbox 不再只收不读）。

验证：
  ① 用户 steer 主 agent → 下轮 chat 时注入 [引导]（引导生效）
  ② 其他 agent 对话投递 → 下轮 chat 追加 [消息投递]
"""

import time
import unittest.mock as mock

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext


class _FastClient:
    def __init__(self):
        self.seen = []

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult
        self.seen.append([m for m in messages])
        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


class _FakeAgent:
    def __init__(self, client, context):
        from qi_agent.agents.agent import Agent
        # 直接传 context 给 Agent 构造（不事后覆盖——events/messages 一致）
        self._agent = Agent(client, system_prompt="", max_turns=3,
                            context=context)
        self.client = client
        self.context = context

    def chat(self, user_input, stream_callback=None):
        return self._agent.chat(user_input, stream_callback)


def _patch_make_agent(mgr, client):
    """mock make_agent：返回绑定假 client 的 Agent（不碰真实 LLM/工具）。

    pool.acquire 里是 `from qi_agent.agents.factory import make_agent`
    （延迟 import）——patch factory 模块的属性（pool 运行时会查）。
    """
    import qi_agent.agents.factory as factory_mod

    def fake_make_agent(context, type="standard"):
        return _FakeAgent(client, context)

    return mock.patch.object(factory_mod, "make_agent", fake_make_agent)


def test_main_agent_consumes_steer() -> None:
    """steer 主 agent → 下轮 chat 注入 [引导]（引导生效）。"""
    mgr = AgentManager()
    ctx = AgentContext(context_id="ctx_main")
    mgr.register(ctx, role="main")
    client = _FastClient()

    # 用户 steer 主 agent（模拟：主 agent 未运行时排队——下轮生效）
    assert mgr.steer(ctx.id, "重点查性能", sender_id="unknown")
    # 等投递完成
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if ctx.mailbox.inbox.qsize() >= 1:
            break
        time.sleep(0.01)

    # 主 agent 跑一轮（chat 开头消费邮箱）
    with _patch_make_agent(mgr, client):
        mgr.run(ctx.id, "你好", stream_callback=lambda s: None)
    # 验证 LLM 看到的 messages 含 [引导]
    assert any("[引导]" in str(m) for m in client.seen[-1]), \
        f"引导未注入: {client.seen[-1][:3]}"


def test_main_agent_consumes_message() -> None:
    """其他 agent 对话投递 → 下轮 chat 追加 [消息投递]。"""
    mgr = AgentManager()
    ctx = AgentContext(context_id="ctx_main2")
    mgr.register(ctx, role="main")
    client = _FastClient()

    # 其他 agent 投递对话
    mgr.send_message(ctx.id, "协作消息", sender_id="agt_team")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if ctx.mailbox.inbox.qsize() >= 1:
            break
        time.sleep(0.01)

    with _patch_make_agent(mgr, client):
        mgr.run(ctx.id, "继续", stream_callback=lambda s: None)
    assert any("[消息投递]" in str(m) for m in client.seen[-1]), \
        f"对话投递未注入: {client.seen[-1][:3]}"


def test_message_and_steer_merged_single_user() -> None:
    """message + steer 同时存在 → 合并成【一条 user】——防连续 user 报错。

    2026-08-30：相邻追加多条 user 可能触发 LLM 400（role 必须交替）。
    context.consume_mailbox 合并：一条 user（| 分隔）。
    """

    mgr = AgentManager()
    ctx = AgentContext(context_id="ctx_merge")
    mgr.register(ctx, role="main")

    # 同时投递：对话 + steer
    mgr.send_message(ctx.id, "协作消息", sender_id="agt_team")
    mgr.steer(ctx.id, "改方向", sender_id="unknown")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if ctx.mailbox.inbox.qsize() >= 2:
            break
        time.sleep(0.01)

    # 直接调 consume_mailbox（数据层能力——不经过 Agent）
    msgs = ctx.consume_mailbox([])
    assert len(msgs) == 1, f"应合并成一条 user: {msgs}"
    assert msgs[0]["role"] == "user"
    assert "[消息投递] 协作消息" in msgs[0]["content"]
    assert "[引导] 改方向" in msgs[0]["content"]
    # 已消费（二次消费空）
    assert ctx.consume_mailbox([]) == []
