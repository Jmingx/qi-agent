"""Agent Loop 测试：工具调用循环的核心逻辑。

用脚本化 FakeClient 模拟 LLM 的各种响应序列，
验证 agent 能正确执行工具、回填结果、直到给出最终答案。
"""

from qi_agent.agent import Agent
from qi_agent.llm import ChatResult, ToolCall


class ScriptedClient:
    """可编程测试替身：按预设脚本依次返回 ChatResult。"""

    def __init__(self, script: list[ChatResult]) -> None:
        self.script = script
        self.calls: list[dict] = []  # 记录每次收到的请求

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.script:
            raise AssertionError("脚本已耗尽：LLM 被调用的次数超出预期")
        return self.script.pop(0)


def make_result(content: str | None, tool_calls: list[ToolCall] | None = None) -> ChatResult:
    """构造一个 ChatResult。"""
    assistant_msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        import json

        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
    return ChatResult(content=content, tool_calls=tool_calls, assistant_message=assistant_msg)


def test_no_tool_single_turn() -> None:
    """模型直接回复：chat() 返回文本，历史追加 assistant。"""
    client = ScriptedClient([make_result("你好！")])
    agent = Agent(client)

    reply = agent.chat("在吗")

    assert reply == "你好！"
    assert len(agent.history) == 3  # system + user + assistant
    assert agent.history[-1]["role"] == "assistant"


def test_one_tool_call() -> None:
    """模型调 1 个工具后回复：工具被执行、结果回填、最终答案返回。"""
    # 脚本：第一轮要调工具，第二轮给最终答案
    client = ScriptedClient([
        make_result(None, [ToolCall(id="call_1", name="get_time", arguments={})]),
        make_result("现在是 2026-08-14 21:30:00"),
    ])
    agent = Agent(client)

    reply = agent.chat("现在几点了？")

    assert reply == "现在是 2026-08-14 21:30:00"
    # 历史应包含 tool 角色的回填消息
    roles = [m["role"] for m in agent.history]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]


def test_tool_result_in_history() -> None:
    """工具结果应带正确的 tool_call_id 和内容。"""
    client = ScriptedClient([
        make_result(None, [ToolCall(id="call_9", name="get_time", arguments={})]),
        make_result("done"),
    ])
    agent = Agent(client)

    agent.chat("时间")

    tool_msg = agent.history[3]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_9"
    assert isinstance(tool_msg["content"], str) and len(tool_msg["content"]) > 0


def test_two_tools_in_one_call() -> None:
    """模型一次调 2 个工具：两个结果都回填、顺序正确。"""
    client = ScriptedClient([
        make_result(None, [
            ToolCall(id="c1", name="get_time", arguments={}),
            ToolCall(id="c2", name="read_file", arguments={"path": "docs/python-basics/README.md"}),
        ]),
        make_result("都执行完了"),
    ])
    agent = Agent(client)

    reply = agent.chat("两个工具")

    assert reply == "都执行完了"
    tool_msgs = [m for m in agent.history if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert tool_msgs[1]["tool_call_id"] == "c2"
    assert "2026" in tool_msgs[0]["content"] or "20" in tool_msgs[0]["content"]


def test_loop_continues_until_answer() -> None:
    """连续多次工具调用：循环持续到模型给文本才停。"""
    # 脚本：调工具 → 再调工具 → 给答案（3 轮）
    client = ScriptedClient([
        make_result(None, [ToolCall(id="a", name="get_time", arguments={})]),
        make_result(None, [ToolCall(id="b", name="get_time", arguments={})]),
        make_result("最终答案"),
    ])
    agent = Agent(client)

    reply = agent.chat("多轮")

    assert reply == "最终答案"
    assert len(client.calls) == 3  # LLM 被调用了 3 次


def test_max_turns_exceeded() -> None:
    """模型一直调工具：超限应返回提示。"""
    # 脚本：max_turns=2，但模型 3 次都要调工具（第 3 次超出）
    client = ScriptedClient([
        make_result(None, [ToolCall(id="1", name="get_time", arguments={})]),
        make_result(None, [ToolCall(id="2", name="get_time", arguments={})]),
        make_result(None, [ToolCall(id="3", name="get_time", arguments={})]),
    ])
    agent = Agent(client, max_turns=2)

    reply = agent.chat("一直调")

    assert "最大轮数" in reply
    assert len(client.calls) == 2  # 只允许 2 轮就停止


def test_unknown_tool_does_not_crash() -> None:
    """模型请求不存在的工具：不崩溃，错误信息回填后继续。"""
    client = ScriptedClient([
        make_result(None, [ToolCall(id="x", name="no_such_tool", arguments={})]),
        make_result("我失败了，但没崩溃"),
    ])
    agent = Agent(client)

    reply = agent.chat("调用不存在的工具")

    assert reply == "我失败了，但没崩溃"
    # 错误信息作为 tool 结果回填
    tool_msg = [m for m in agent.history if m["role"] == "tool"][-1]
    assert "未知工具" in tool_msg["content"]


# ── 并行工具调用（方案 2026-08-22，DeepSeek 并行返回实测通过）─────────────


def test_parallel_tool_calls_all_executed() -> None:
    """模型一次返回 2 个 tool_calls → 两个都执行、结果按原顺序回填。"""
    client = ScriptedClient([
        make_result(None, tool_calls=[
            ToolCall(id="call_1", name="read_file",
                     arguments={"path": "pyproject.toml"}),
            ToolCall(id="call_2", name="shell", arguments={"command": "pwd"}),
        ]),
        make_result("两个都执行了"),
    ])
    agent = Agent(client)
    reply = agent.chat("帮我读项目配置并看当前目录")
    assert reply == "两个都执行了"
    tool_msgs = [m for m in agent.history if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    # 回填顺序 = call 原顺序（call_1 在前，模型按 index 对应）
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert tool_msgs[1]["tool_call_id"] == "call_2"
    assert "qi-agent" in tool_msgs[0]["content"]  # read_file 真执行了
    assert tool_msgs[1]["content"] != "[安全拦截]"  # pwd 白名单放行


def test_parallel_tool_result_events_in_order() -> None:
    """tool-result 事件按 call 原顺序 emit（监听器状态配对依赖顺序）。"""
    from qi_agent.events import EventBus

    emitted: list[str] = []
    bus = EventBus()
    bus.on("agent/tool-result", lambda **kw: emitted.append(kw["name"]))
    client = ScriptedClient([
        make_result(None, tool_calls=[
            ToolCall(id="c1", name="shell", arguments={"command": "pwd"}),
            ToolCall(id="c2", name="shell", arguments={"command": "dir"}),
        ]),
        make_result("完成"),
    ])
    agent = Agent(client, events=bus)
    agent.chat("看下目录")
    assert emitted == ["shell", "shell"]  # 两个事件按序（同名工具也不乱）


def test_parallel_blocked_call_still_emits() -> None:
    """并行中拦截的 call 也广播 tool-result（与串行现状语义一致）。"""
    from qi_agent.events import EventBus

    emitted: list[str] = []
    bus = EventBus()
    bus.on("agent/tool-result", lambda **kw: emitted.append(kw["output"][:6]))
    # 脚本：第一轮 2 个 tool_calls（第二个是未知工具 → 工具层拦截）
    client = ScriptedClient([
        make_result(None, tool_calls=[
            ToolCall(id="c1", name="shell", arguments={"command": "pwd"}),
            ToolCall(id="c2", name="no_such_tool", arguments={}),
        ]),
        make_result("完成"),
    ])
    agent = Agent(client, events=bus)
    agent.chat("执行")
    assert emitted[0].startswith("C:") or emitted[0].startswith("/")  # pwd 输出
    assert emitted[1].startswith("[工具")  # 未知工具错误也广播


def test_parallel_actually_concurrent() -> None:
    """并行证据：第一个工具执行等第二个也进入才完成（串行下必超时失败）。"""
    import threading
    from unittest import mock

    first_in = threading.Event()
    both_in = threading.Event()

    def fake_execute(name: str, arguments: dict, internal=None) -> str:
        # 先进入的调用：等第二个也进入（并行才可能；串行时等 2s 超时）
        if not first_in.is_set():
            first_in.set()
            ok = both_in.wait(timeout=2)
            return f"{name}-ok-{ok}"
        both_in.set()  # 第二个进入：通知第一个
        return f"{name}-ok"

    # execute_tool 已随执行闭环下沉到 executor（方案 2026-08-23）——
    # patch 位置从 qi_agent.agent 移到 qi_agent.tools.executor
    with mock.patch("qi_agent.tools.executor.execute_tool", side_effect=fake_execute):
        client = ScriptedClient([
            make_result(None, tool_calls=[
                ToolCall(id="c1", name="shell", arguments={"command": "pwd"}),
                ToolCall(id="c2", name="shell", arguments={"command": "dir"}),
            ]),
            make_result("完成"),
        ])
        agent = Agent(client)
        agent.chat("看目录")
    tool_msgs = [m for m in agent.history if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    # 第一个调用等到了第二个 → ok-True（并行实锤；串行实现会 ok-False）
    assert "ok-True" in tool_msgs[0]["content"] or "ok-True" in tool_msgs[1]["content"]
