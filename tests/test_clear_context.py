"""Agent clear_context 回归测试：验证清理上下文的行为。"""

from qi_agent.agents.agent import Agent
from qi_agent.llm import ChatResult


class FakeClient:
    """测试替身：返回固定回复。"""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        return ChatResult(
            content=self.reply,
            tool_calls=None,
            assistant_message={"role": "assistant", "content": self.reply},
        )


def test_clear_context_resets_history() -> None:
    """clear_context 后历史应只剩 system prompt 一条。"""
    agent = Agent(FakeClient())
    agent.chat("你好")
    assert len(agent.history) == 3  # system + user + assistant

    agent.context.reset_session()

    assert len(agent.history) == 1
    assert agent.history[0]["role"] == "system"


def test_clear_context_preserves_custom_prompt() -> None:
    """clear_context 后应保留自定义 system_prompt（回归：修复覆盖bug）。"""
    agent = Agent(FakeClient(), system_prompt="你是专业的编程助手。")
    agent.chat("你好")

    agent.context.reset_session()

    assert agent.history[0]["content"] == "你是专业的编程助手。"


def test_clear_context_then_continue_chat() -> None:
    """clear 之后应能继续正常对话。"""
    agent = Agent(FakeClient())
    agent.chat("第一轮")
    agent.context.reset_session()

    reply = agent.chat("第二轮")

    assert reply == "ok"
    assert len(agent.history) == 3  # 新会话：system + user + assistant
