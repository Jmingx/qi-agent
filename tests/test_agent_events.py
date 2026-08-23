"""Agent 事件点集成测试：事件在循环中被触发的时机与 payload。

方案：docs/plans/2026-08-18-事件化改造方案.md（决策点 1-7 已批准）
"""

import re

from qi_agent.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.builtin.tool_stats import ToolStatsPlugin


class FakeClient:
    """测试替身：返回固定文本回复，不发起网络请求。"""

    def __init__(self, reply: str = "fake reply") -> None:
        self.reply = reply
        self.received: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.received.append(messages)
        return ChatResult(
            content=self.reply,
            tool_calls=None,
            assistant_message={"role": "assistant", "content": self.reply},
        )


class FakeToolClient:
    """测试替身：第一轮返回 get_time 工具调用，之后返回文本。"""

    def __init__(self) -> None:
        self.calls = 0
        self.received: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.calls += 1
        self.received.append(messages)
        if self.calls == 1:
            return ChatResult(
                content=None,
                tool_calls=[ToolCall(id="call_1", name="get_time", arguments={})],
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_time", "arguments": "{}"},
                        }
                    ],
                },
            )
        return ChatResult(
            content="最终答案",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "最终答案"},
        )


class NeverDoneClient:
    """测试替身：永远返回工具调用（触发 max_turns 超限）。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.calls += 1
        return ChatResult(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="get_time", arguments={})],
            assistant_message={
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"},
                    }
                ],
            },
        )


def test_turn_start_emitted() -> None:
    """一轮对话应触发 turn-start，payload 含 user_input。"""
    bus = EventBus()
    received: list[str] = []
    bus.on("agent/turn-start", lambda user_input, **_: received.append(user_input))
    agent = Agent(FakeClient(reply="hi"), events=bus)
    agent.chat("你好")
    assert received == ["你好"]


def test_pre_step_waterfall_injects() -> None:
    """pre-step 改写消息后，模型应收到改写后的历史。"""
    bus = EventBus()
    fake = FakeClient(reply="ok")

    def inject(messages: list[dict], **_) -> list[dict]:
        return [{"role": "system", "content": "注入的上下文"}] + messages

    bus.on("agent/pre-step", inject)
    agent = Agent(fake, events=bus)
    agent.chat("你好")
    assert fake.received[0][0] == {"role": "system", "content": "注入的上下文"}


def test_tool_call_bail_intercepts() -> None:
    """tool-call 返回非 None 应拦截：工具不执行，拦截值作为结果回填。"""
    bus = EventBus()
    bus.on("agent/tool-call", lambda **_: "[审批] 拒绝执行 get_time")
    agent = Agent(FakeToolClient(), events=bus)
    reply = agent.chat("几点了")
    assert reply == "最终答案"
    # 工具结果消息内容应为拦截值，而非 get_time 的时间格式
    tool_msg = next(m for m in agent.history if m["role"] == "tool")
    assert tool_msg["content"] == "[审批] 拒绝执行 get_time"


def test_tool_call_bail_none_executes() -> None:
    """tool-call 全部返回 None 应正常执行工具。"""
    bus = EventBus()
    bus.on("agent/tool-call", lambda **_: None)
    agent = Agent(FakeToolClient(), events=bus)
    agent.chat("几点了")
    tool_msg = next(m for m in agent.history if m["role"] == "tool")
    # get_time 正常执行 → 结果是 YYYY-MM-DD HH:MM:SS 格式
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", tool_msg["content"])


def test_final_answer_emitted() -> None:
    """最终答案应触发 final-answer 事件。"""
    bus = EventBus()
    received: list[str] = []
    bus.on("agent/final-answer", lambda content, **_: received.append(content))
    agent = Agent(FakeClient(reply="答案是 42"), events=bus)
    agent.chat("问题")
    assert received == ["答案是 42"]


def test_turn_end_on_max_turns() -> None:
    """超限结束应触发 turn-end，reason=max_turns。"""
    bus = EventBus()
    reasons: list[str] = []
    bus.on("agent/turn-end", lambda reason, **_: reasons.append(reason))
    agent = Agent(NeverDoneClient(), max_turns=2, events=bus)
    reply = agent.chat("死循环")
    assert reply == "已达最大轮数，任务可能未完成。"
    assert reasons == ["max_turns"]


def test_tool_result_emitted() -> None:
    """工具执行后应触发 tool-result，payload 含 name/output。"""
    bus = EventBus()
    seen: list[dict] = []
    bus.on("agent/tool-result", lambda **data: seen.append(data))
    agent = Agent(FakeToolClient(), events=bus)
    agent.chat("几点了")
    assert len(seen) == 1
    assert seen[0]["name"] == "get_time"
    assert isinstance(seen[0]["duration"], float)


def test_tool_stats_plugin() -> None:
    """统计插件：一轮含工具调用的对话后 calls/failures 正确。"""
    bus = EventBus()
    plugin = ToolStatsPlugin()
    plugin.install(bus)
    agent = Agent(FakeToolClient(), events=bus)
    agent.chat("几点了")
    report = plugin.report()
    assert "get_time" in report
    assert "1 次" in report  # 调用 1 次、成功（get_time 正常执行）
