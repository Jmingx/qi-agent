"""Agent 核心单元测试：验证消息历史管理与多轮对话逻辑。

使用 FakeClient 代替真实 LLM 客户端，不发起网络请求。
"""

from qi_agent.agent import Agent
from qi_agent.llm import ChatResult


class FakeClient:
    """测试替身：不发真实请求，只记录收到的消息并返回固定回复。"""

    def __init__(self, reply: str = "fake reply") -> None:
        self.reply = reply
        self.received: list[list[dict]] = []  # 记录每次收到的消息列表

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.received.append(messages)
        return ChatResult(
            content=self.reply,
            tool_calls=None,
            assistant_message={"role": "assistant", "content": self.reply},
        )


def make_agent(client: FakeClient | None = None) -> tuple[Agent, FakeClient]:
    """构造被测 Agent 及其 FakeClient，方便断言。"""
    fake = client or FakeClient()
    agent = Agent(fake)
    return agent, fake


def test_chat_returns_reply() -> None:
    """chat() 应返回模型的回复文本。"""
    agent, fake = make_agent(FakeClient(reply="你好！"))
    assert agent.chat("在吗") == "你好！"


def test_history_starts_with_system() -> None:
    """历史应以 system prompt 开头。"""
    agent, _ = make_agent()
    assert agent.history[0]["role"] == "system"
    assert "助手" in agent.history[0]["content"]


def test_user_message_appended() -> None:
    """用户消息应被追加进历史（位于 system 之后）。"""
    agent, _ = make_agent()
    agent.chat("你好")
    assert agent.history[1] == {"role": "user", "content": "你好"}


def test_assistant_reply_appended() -> None:
    """模型回复应被追加进历史。"""
    agent, fake = make_agent(FakeClient(reply="我是回复"))
    agent.chat("你好")
    assert agent.history[-1] == {"role": "assistant", "content": "我是回复"}


def test_two_turns_context() -> None:
    """两轮对话后，第二次请求应包含全部 5 条消息（system+user+assistant+user+assistant）。

    这是多轮上下文的核心验证：客户端必须把第一轮的历史也带上。
    """
    agent, fake = make_agent()
    agent.chat("我叫小明")
    agent.chat("我叫什么名字？")

    assert len(fake.received) == 2
    second_request = fake.received[1]
    # system + 第一轮 user/assistant + 第二轮 user
    assert len(second_request) == 5
    assert second_request[0]["role"] == "system"
    assert second_request[1] == {"role": "user", "content": "我叫小明"}
    assert second_request[2]["role"] == "assistant"
    assert second_request[3] == {"role": "user", "content": "我叫什么名字？"}


def test_custom_system_prompt() -> None:
    """应支持自定义 system prompt。"""
    client = FakeClient()
    agent = Agent(client, system_prompt="你是中文助手。")
    assert agent.history[0]["content"] == "你是中文助手。"
