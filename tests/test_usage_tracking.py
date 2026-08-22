"""API usage 跟踪测试（阶段 A2）：agent 累计 prompt/completion/total。

设计（方案 2026-08-22-上下文管理）：ChatResult.usage 已透传（v0.4.22
资源监控），本阶段补 agent 层累计（self._usage）+ 会话结束汇总打印。
"""

from qi_agent.agent import Agent
from qi_agent.llm import ChatResult


class UsageFakeClient:
    """带 usage 的替身：每轮返回不同 usage（验证累计）。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        usage = {
            "prompt_tokens": 100 * self.calls,
            "completion_tokens": 10 * self.calls,
            "total_tokens": 110 * self.calls,
        }
        return ChatResult(
            content="ok",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "ok"},
            usage=usage,
        )

    def chat_stream(self, messages, tools=None, on_delta=None) -> ChatResult:
        return self.chat(messages, tools)


def _make_agent(client) -> Agent:
    return Agent(
        client=client,
        system_prompt="test",
        max_turns=3,
    )


def test_usage_accumulated() -> None:
    """多次调用累计：2 轮后 prompt/completion/total 各自加总。"""
    agent = _make_agent(UsageFakeClient())
    agent.chat("你好")
    agent.chat("再来")
    usage = agent.get_usage()
    assert usage["prompt_tokens"] == 100 + 200
    assert usage["completion_tokens"] == 10 + 20
    assert usage["total_tokens"] == 110 + 220


def test_usage_initial_zero() -> None:
    """初始为 0（未调用不崩）。"""
    agent = _make_agent(UsageFakeClient())
    usage = agent.get_usage()
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_usage_without_usage_field() -> None:
    """usage 缺失（旧 API/流式缺 usage）→ 不崩、不累计（容错）。"""

    class NoUsageClient:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                content="ok",
                tool_calls=None,
                assistant_message={"role": "assistant", "content": "ok"},
                usage=None,
            )

        def chat_stream(self, messages, tools=None, on_delta=None) -> ChatResult:
            return self.chat(messages, tools)

    agent = _make_agent(NoUsageClient())
    agent.chat("你好")
    assert agent.get_usage()["total_tokens"] == 0


def test_usage_report_string() -> None:
    """汇总报告字符串：人类可读（/stats 或退出打印）。"""
    agent = _make_agent(UsageFakeClient())
    agent.chat("你好")
    report = agent.usage_report()
    assert "tokens" in report
    assert "prompt" in report
    assert "110" in report  # total_tokens 首轮值
