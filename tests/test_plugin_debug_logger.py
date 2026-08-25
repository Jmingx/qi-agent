"""debug_logger 插件测试（2026-08-22 插件化：事件驱动日志）。

验证：插件监听 5 个事件 → 对应日志段打印（复用 DebugLogger 格式化）；
agent 零 logger 依赖（Agent 构造无 logger 参数）。
"""

from qi_agent.agents.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult
from qi_agent.plugins.builtin.debug_logger import DebugLoggerPlugin


class _FakeClient:
    def chat(self, messages, tools=None) -> ChatResult:
        return ChatResult(
            content="回答完毕",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "回答完毕"},
        )

    def chat_stream(self, messages, tools=None, on_delta=None) -> ChatResult:
        return self.chat(messages, tools)


def test_plugin_listens_events(capsys) -> None:
    """插件装配后：一轮对话打印 USER + CTX + REQ + RESP + ANSWER。"""
    agent = Agent(client=_FakeClient(), events=EventBus())
    plugin = DebugLoggerPlugin()
    plugin.install(agent.events)
    agent.chat("你好")
    out = capsys.readouterr().out
    assert "[USER]" in out
    assert "[CTX]" in out
    assert "[REQ]" in out
    assert "[RESP]" in out
    assert "[ANSWER]" in out


def test_no_plugin_no_logs(capsys) -> None:
    """未装配插件 → 零日志输出（正常会话安静）。"""
    agent = Agent(client=_FakeClient(), events=EventBus())
    agent.chat("你好")
    out = capsys.readouterr().out
    assert "[USER]" not in out
    assert "[ANSWER]" not in out


def test_tool_result_logged(capsys) -> None:
    """工具调用 → [TOOL] 日志段（事件驱动）。"""

    class ToolClient:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                content=None,
                tool_calls=[
                    type("TC", (), {
                        "id": "call_1",
                        "name": "get_time",
                        "arguments": {},
                    })()
                ],
                assistant_message={"role": "assistant", "content": None,
                                   "tool_calls": []},
            )

    agent = Agent(client=ToolClient(), events=EventBus())
    plugin = DebugLoggerPlugin()
    plugin.install(agent.events)
    try:
        agent.chat("几点")
    except Exception:
        pass  # 工具执行真实 get_time 或失败——只看 [TOOL] 日志
    out = capsys.readouterr().out
    assert "[TOOL]" in out
