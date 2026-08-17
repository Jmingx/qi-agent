"""流式输出测试：chat_stream 生成器、agent 流式回调、历史完整性、回归保护。"""

from qi_agent.agent import Agent
from qi_agent.llm import ChatResult, ToolCall


class FakeClient:
    """测试替身：支持普通 chat 和流式 chat_stream。"""

    def __init__(self) -> None:
        self.stream_calls: list[dict] = []  # 记录流式调用

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        return ChatResult(
            content="普通回复",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "普通回复"},
        )

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta=None,
    ) -> ChatResult:
        """流式替身：逐块回调 on_delta，返回累积 ChatResult。"""
        self.stream_calls.append({"messages": list(messages), "tools": tools})
        parts = ["你", "好", "！"]
        for piece in parts:
            if on_delta:
                on_delta(piece)
        full = "".join(parts)
        return ChatResult(
            content=full,
            tool_calls=None,
            assistant_message={"role": "assistant", "content": full},
        )


class ToolTurnClient(FakeClient):
    """测试替身：第一轮返回工具调用，第二轮流式回答。"""

    def __init__(self) -> None:
        super().__init__()
        self.turn = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.turn += 1
        if self.turn == 1:
            tool_call_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"},
                    }
                ],
            }
            return ChatResult(
                content=None,
                tool_calls=[ToolCall(id="c1", name="get_time", arguments={})],
                assistant_message=tool_call_msg,
            )
        # 第二轮：模型直接回答（流式分支会调 chat_stream，这里不会走到）
        return ChatResult(
            content="时间是10点",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "时间是10点"},
        )

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta=None,
    ) -> ChatResult:
        """第二轮流式替身：回调增量，返回与普通回答一致的 ChatResult。"""
        self.stream_calls.append({"messages": list(messages), "tools": tools})
        parts = ["时", "间", "是", "10", "点"]
        for piece in parts:
            if on_delta:
                on_delta(piece)
        full = "".join(parts)
        return ChatResult(
            content=full,
            tool_calls=None,
            assistant_message={"role": "assistant", "content": full},
        )


def test_chat_stream_yields_deltas() -> None:
    """chat_stream 应通过 on_delta 回调逐块产出增量，并返回累积完整文本。"""
    client = FakeClient()
    received: list[str] = []

    result = client.chat_stream(
        [{"role": "user", "content": "hi"}], on_delta=lambda d: received.append(d)
    )

    assert received == ["你", "好", "！"]  # 打字机增量
    assert result.content == "你好！"  # 累积完整文本
    assert result.tool_calls is None


def test_agent_stream_callback_receives_deltas() -> None:
    """agent 流式时回调应收到所有增量块。"""
    client = FakeClient()
    agent = Agent(client)

    received: list[str] = []
    reply = agent.chat("你好", stream_callback=lambda delta: received.append(delta))

    assert received == ["你", "好", "！"]
    assert reply == "你好！"  # 返回累积的完整文本


def test_agent_stream_accumulates_full_text() -> None:
    """流式后历史应存完整文本（非片段）。"""
    client = FakeClient()
    agent = Agent(client)

    agent.chat("你好", stream_callback=lambda delta: None)

    assert agent.history[-1]["role"] == "assistant"
    assert agent.history[-1]["content"] == "你好！"  # 完整文本，不是"你"或"你好"


def test_agent_without_callback_unchanged() -> None:
    """不传 stream_callback 时行为与之前一致（回归保护）。"""
    client = FakeClient()
    agent = Agent(client)

    reply = agent.chat("你好")

    assert reply == "普通回复"  # 走普通 chat 分支
    assert agent.history[-1]["content"] == "普通回复"


class StreamToolClient(FakeClient):
    """测试替身：chat_stream 第一轮返回工具调用，第二轮返回文本。"""

    def __init__(self) -> None:
        super().__init__()
        self.turn = 0

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta=None,
    ) -> ChatResult:
        self.stream_calls.append({"messages": list(messages), "tools": tools})
        self.turn += 1
        if self.turn == 1:
            # 第一轮：工具调用（流式 delta 累积出 tool_calls）
            tc = ToolCall(id="c1", name="get_time", arguments={})
            return ChatResult(
                content=None,
                tool_calls=[tc],
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "get_time", "arguments": "{}"},
                        }
                    ],
                },
            )
        # 第二轮：文本回答（打字机）
        parts = ["时", "间", "是", "10", "点"]
        for piece in parts:
            if on_delta:
                on_delta(piece)
        full = "".join(parts)
        return ChatResult(
            content=full,
            tool_calls=None,
            assistant_message={"role": "assistant", "content": full},
        )


def test_stream_tool_turn_detected() -> None:
    """流式模式应能识别工具调用轮次（第一轮流式返回 tool_calls → 执行 → 第二轮流式回答）。"""
    client = StreamToolClient()
    agent = Agent(client)

    received: list[str] = []
    reply = agent.chat("现在几点", stream_callback=lambda delta: received.append(delta))

    assert reply == "时间是10点"
    assert len(client.stream_calls) == 2  # 两轮都走流式（单调用模式）
    # 历史包含工具调用链：system+user+assistant(tool_calls)+tool+assistant
    roles = [m["role"] for m in agent.history]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
