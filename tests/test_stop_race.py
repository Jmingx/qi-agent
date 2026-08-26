"""stop 后旧 agent 线程回填污染测试（竞态验证 + 修复验证）。

场景：run#1 被 stop（后台线程还活着）→ run#2 新 agent 接管同一 context
     → 旧线程 LLM 返回后回填 → 会污染 context 吗？
"""

import threading
import time


class _RaceClient:
    """模拟竞态：第一次调用慢（stop 后返回），第二次快。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            time.sleep(0.8)  # 慢（stop 后返回——旧线程）
        from qi_agent.llm import ChatResult

        return ChatResult(content=f"回复{self.calls}", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": f"回复{self.calls}"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def test_stale_agent_does_not_pollute() -> None:
    """stop 后旧 agent 线程的回填不得污染下一轮 context。

    验证：run#1 stop 后，旧线程即使 LLM 返回也不写 assistant 消息；
    run#2 正常执行，context 只有 run#2 的消息。
    """
    import unittest.mock as mock

    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext

    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    client = _RaceClient()
    mock.patch.object(factory, "LLMClient", lambda key: client).start()

    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    # run#1：后台线程慢 LLM（0.8s），0.2s 后 stop
    def _do_stop():
        time.sleep(0.2)
        mgr.stop(ctx.id)

    threading.Thread(target=_do_stop, daemon=True).start()
    reply1 = mgr.run(ctx.id, "第一句")
    assert reply1 == "已按指令中断当前任务。"

    # 等旧线程 LLM 返回（0.8s——它应该发现自己被 stop，不写 assistant 消息）
    time.sleep(1.0)

    # run#2：正常执行（快）
    reply2 = mgr.run(ctx.id, "第二句")
    assert reply2 == "回复2"

    # 验证 context：只有 run#2 的完整消息（user 第二句 + assistant 回复2）
    # 不允许出现"回复1"（旧线程的 assistant 消息——污染）
    contents = [m.get("content") for m in ctx.messages]
    assert "回复1" not in contents, f"旧线程污染了 context: {contents}"
    assert "第二句" in contents
    assert "回复2" in contents


def test_stale_direct_chat_does_not_pollute() -> None:
    """直接验证：stop 后调用 agent.chat，LLM 返回后不回填 assistant。

    确定性测试（不依赖时序）：先 stop，再调 chat——LLM 返回后
    应发现 stop 并返回中断，不写 assistant 消息。
    """
    from qi_agent.agents.agent import Agent
    from qi_agent.context.context import AgentContext

    client = _RaceClient()
    ctx = AgentContext()
    agent = Agent(client, context=ctx)

    ctx.stop()  # 先 stop（模拟旧 run 被中断）
    reply = agent.chat("被中断的请求")
    assert reply == "已按指令中断当前任务。"

    # 用户消息已写（用户确实说了），但 assistant 回复不能写
    contents = [m.get("content") for m in ctx.messages]
    assert "被中断的请求" in contents  # 用户消息保留
    assert "回复1" not in contents  # assistant 不回填（污染防护）
