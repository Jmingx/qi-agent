"""控制面并发测试：stop/steer 与 run 交错（2026-08-28 系统排查）。

重点：① stop 在 run 期间调用（实时中断）是否安全
      ② steer 在 run 期间注入（下轮生效）是否安全
      ③ 消息序列在中断后是否完整（无悬空 tool_calls）
"""

import threading
import time
import unittest.mock as mock


from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext


class _ToolLoopClient:
    """多轮工具调用（模拟长任务——每轮调工具）。"""

    def __init__(self, rounds: int = 3):
        self.rounds = rounds
        self.calls = 0

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult, ToolCall

        self.calls += 1
        if self.calls <= self.rounds:
            # 继续调工具（产生 tool_calls + 后续 tool 消息）
            call = ToolCall(id=f"call_{self.calls}", name="get_time",
                            arguments={})
            return ChatResult(
                content=None,
                tool_calls=[call],
                assistant_message={"role": "assistant", "content": None,
                                   "tool_calls": [
                                       {"id": call.id,
                                        "type": "function",
                                        "function": {"name": "get_time",
                                                     "arguments": "{}"}}]},
                usage=None)
        return ChatResult(content="完成", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "完成"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def test_stop_during_run_safe() -> None:
    """run 期间 stop → 实时中断（安全返回 + 消息序列完整）。"""
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    client = _ToolLoopClient(rounds=5)
    mock.patch.object(factory, "LLMClient", lambda key: client).start()

    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    # run 在后台线程（模拟长任务）
    result_box = {}
    t = threading.Thread(
        target=lambda: result_box.update(r=mgr.run(ctx.id, "任务")))
    t.start()
    time.sleep(0.3)  # 等 run 开始（正在工具循环）
    ctx.stop()  # 并发 stop
    t.join(timeout=10)

    r = result_box.get("r", "TIMEOUT")
    print(f"stop 后 run 返回: {r!r}")
    # 中断返回（不是崩溃）
    assert "中断" in r or r == "完成"  # 可能已跑完（stop 时机）

    # 消息序列完整：无悬空 tool_calls（每个 assistant tool_calls 后有 tool）
    msgs = ctx.messages
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            # 下一条必须是 tool（配对）
            nxt = msgs[i + 1] if i + 1 < len(msgs) else None
            assert nxt is not None and nxt.get("role") == "tool", \
                f"悬空 tool_calls at {i}: {m.get('tool_calls')}"
            assert nxt.get("tool_call_id") == m["tool_calls"][0]["id"], \
                f"tool_call_id 不匹配 at {i}"


def test_steer_during_run() -> None:
    """run 期间 steer → 排队（下轮生效，不崩溃）。"""
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient",
                      lambda key: _ToolLoopClient(rounds=2)).start()

    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    result_box = {}
    t = threading.Thread(
        target=lambda: result_box.update(r=mgr.run(ctx.id, "任务")))
    t.start()
    time.sleep(0.2)
    ctx.steer("改变方向")  # 并发 steer
    t.join(timeout=10)

    assert "r" in result_box  # run 正常完成（steer 排队不阻塞）
