"""竞态修复测试：chat 完成后 result_box 写入竞态（'value' KeyError）。

场景：agent.chat 内部 complete_chat（set _done）→ 主线程被唤醒 →
      worker 还没把返回值写入 result_box → 旧代码 KeyError 'value'。
修复：worker finally 设独立完成事件，主线程等它（防竞态）。
"""

import time


from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext


class _SlowCompleteClient:
    """chat 内 complete_chat 后延迟返回（制造竞态窗口）。

    模拟真实场景：_run_tool_loop 返回 → complete_chat（set _done）
    → 主线程被唤醒 → 但 worker 还要走完 return + result_box 赋值
    → 旧代码在窗口内读 result_box["value"] → KeyError。
    """

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        # 模拟 LLM 调用耗时（真实场景此期间主线程在等 _done）
        time.sleep(0.1)
        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def test_run_returns_value_no_race(tmp_path) -> None:
    """run 返回 chat 结果（无 'value' KeyError）。"""
    import unittest.mock as mock

    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient",
                      lambda key: _SlowCompleteClient()).start()
    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    reply = mgr.run(ctx.id, "你好")
    assert reply == "ok"  # 不抛 KeyError


def test_run_with_real_race(tmp_path) -> None:
    """真实竞态：complete_chat 后延迟（制造 worker 未写完窗口）。"""
    import unittest.mock as mock

    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient",
                      lambda key: _SlowCompleteClient()).start()
    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    # 多次 run 稳定复现（防偶发）
    for _ in range(5):
        reply = mgr.run(ctx.id, f"问{_}")
        assert reply == "ok"
