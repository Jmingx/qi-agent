"""Phase 1 测试：Agent.tools 白名单（受限子集双层防绕过）。

方案：docs/plans/2026-08-23-subagent方案.md 第 3 节
层 1（模型可见）：get_tool_schemas(allowlist) → 只给白名单内工具的 schema
层 2（执行硬校验）：executor 执行前查白名单 → 白名单外直接拒绝
"""


from qi_agent.agents.agent import Agent
from qi_agent.tools.registry import get_tool_schemas
from qi_agent.tools.executor import ToolExecutor
from qi_agent.llm import ToolCall


class _FakeClient:
    """假 LLM 客户端：记录收到的 tools，按需返回工具调用或最终回答。"""

    def __init__(self, tool_calls: list[ToolCall] | None = None) -> None:
        self.tool_calls = tool_calls or []
        self.seen_tools: list[list[dict]] = []
        self.messages: list[dict] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> object:
        self.seen_tools.append(tools or [])
        self.messages = messages
        if self.tool_calls:
            calls = self.tool_calls
            self.tool_calls = []
            return _Result(tool_calls=calls, content="")
        return _Result(tool_calls=[], content="最终回答")


class _Result:
    def __init__(self, tool_calls=None, content: str = "") -> None:
        self.tool_calls = tool_calls or []
        self.content = content
        self.usage = None
        self.assistant_message = {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments}}
                for c in (tool_calls or [])
            ] if tool_calls else None,
        }


def test_get_tool_schemas_allowlist_filters() -> None:
    """get_tool_schemas(allowlist) 只返回白名单内工具（模型可见层）。"""
    schemas = get_tool_schemas(["read_file", "get_time"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file", "get_time"}
    assert "shell" not in names  # 白名单外工具对模型不可见


def test_get_tool_schemas_none_returns_all() -> None:
    """allowlist=None（默认）返回全部工具——现有行为不变。"""
    schemas = get_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "read_file" in names and "shell" in names  # 全量


def test_agent_tools_allowlist_limits_schema() -> None:
    """Agent(tools=白名单) → 发送给 LLM 的 tools 只含白名单（层 1）。"""
    client = _FakeClient()
    agent = Agent(client, tools=["get_time"])
    agent.chat("现在几点")
    # 最后发送的 tools 只含 get_time
    assert client.seen_tools[-1]  # 非空
    names = {s["function"]["name"] for s in client.seen_tools[-1]}
    assert names == {"get_time"}


def test_agent_tools_none_all_schemas() -> None:
    """Agent 默认（tools=None）→ 全量工具（向后兼容）。"""
    client = _FakeClient()
    agent = Agent(client)
    agent.chat("hi")
    names = {s["function"]["name"] for s in client.seen_tools[-1]}
    assert "shell" in names and "read_file" in names


def test_executor_rejects_outside_allowlist() -> None:
    """executor 执行白名单外工具 → 拒绝（层 2 硬校验，防绕过）。"""
    executor = ToolExecutor()
    call = ToolCall(id="c1", name="shell", arguments={"command": "rm -rf /"})
    # 模拟 agent/tool-call 判档：放行（None）
    results = executor.execute(
        [call], {"c1": None}, turn=1, step=0, allowlist=["get_time"]
    )
    output, _ = results["c1"]
    assert "受限子集" in output and "shell" in output  # 白名单外拒绝 + 说明工具名
    assert "rm" not in output  # 实际没有执行


def test_executor_allows_inside_allowlist() -> None:
    """executor 执行白名单内工具 → 正常执行。"""
    executor = ToolExecutor()
    call = ToolCall(id="c1", name="get_time", arguments={})
    results = executor.execute(
        [call], {"c1": None}, turn=1, step=0, allowlist=["get_time"]
    )
    output, _ = results["c1"]
    assert output  # 有输出（时间字符串）


def test_executor_default_allows_all() -> None:
    """executor 默认（allowlist=None）→ 全部工具（向后兼容）。"""
    executor = ToolExecutor()
    call = ToolCall(id="c1", name="get_time", arguments={})
    results = executor.execute([call], {"c1": None}, turn=1, step=0)
    output, _ = results["c1"]
    assert output
