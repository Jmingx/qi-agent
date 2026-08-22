"""调试日志插件测试（2026-08-22 插件化）：事件驱动日志输出 + Agent 集成。

单元测试直调插件事件处理方法（_on_pre_llm 等）验证日志内容；
集成测试验证 Agent + 插件装配后的完整链路打印。
"""

from qi_agent.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.debug_logger import DebugLoggerPlugin


class FakeClient:
    """测试替身：按脚本返回 ChatResult，记录请求。"""

    def __init__(self, script: list[ChatResult] | None = None) -> None:
        self.script = script or [
            ChatResult(content="你好！", tool_calls=None,
                       assistant_message={"role": "assistant", "content": "你好！"})
        ]
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.calls.append({"messages": messages, "tools": tools})
        return self.script.pop(0)


def _make_plugin() -> DebugLoggerPlugin:
    return DebugLoggerPlugin()


# ── 事件方法单元测试 ─────────────────────────────────────────────────────


def test_pre_llm_logs_request(capsys) -> None:
    """pre-llm 事件 → [REQ] 日志含消息内容和工具定义。"""
    _make_plugin()._on_pre_llm(
        [{"role": "user", "content": "现在几点"}],
        [{"type": "function", "function": {"name": "get_time"}}],
    )
    out = capsys.readouterr().out
    assert "[REQ]" in out
    assert "现在几点" in out
    assert "get_time" in out


def test_pre_llm_logs_context(capsys) -> None:
    """pre-llm 事件 → [CTX] 上下文占用日志。"""
    _make_plugin()._on_pre_llm(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        [],
    )
    out = capsys.readouterr().out
    assert "[CTX]" in out
    assert "tokens" in out


def test_post_llm_logs_response_content(capsys) -> None:
    """post-llm 事件 → [RESP] 含模型文本响应。"""
    _make_plugin()._on_post_llm(
        ChatResult(content="答案是42", tool_calls=None,
                   assistant_message={"role": "assistant", "content": "答案是42"})
    )
    out = capsys.readouterr().out
    assert "[RESP]" in out
    assert "答案是42" in out


def test_post_llm_logs_tool_calls(capsys) -> None:
    """post-llm 事件 → [RESP] 含工具调用信息。"""
    tc = ToolCall(id="c1", name="get_time", arguments={})
    _make_plugin()._on_post_llm(
        ChatResult(content=None, tool_calls=[tc],
                   assistant_message={"role": "assistant", "content": None})
    )
    out = capsys.readouterr().out
    assert "[RESP]" in out
    assert "get_time" in out


def test_tool_result_logs_args_and_output(capsys) -> None:
    """tool-result 事件 → [TOOL] 含入参和结果。"""
    _make_plugin()._on_tool_result("read_file", {"path": "/tmp/a.txt"}, "文件内容")
    out = capsys.readouterr().out
    assert "[TOOL]" in out
    assert "read_file" in out
    assert "文件内容" in out


def test_turn_start_logs_user_input(capsys) -> None:
    """turn-start 事件 → [USER] 日志。"""
    _make_plugin()._on_turn_start("你好")
    out = capsys.readouterr().out
    assert "[USER]" in out
    assert "你好" in out


def test_final_answer_logs(capsys) -> None:
    """final-answer 事件 → [ANSWER] 日志。"""
    _make_plugin()._on_final_answer("最终答案")
    out = capsys.readouterr().out
    assert "[ANSWER]" in out
    assert "最终答案" in out


# ── Agent 集成 ───────────────────────────────────────────────────────────


def test_agent_with_plugin_logs_chain(capsys) -> None:
    """Agent + 插件：完整链路被记录（user→ctx→req→resp→answer）。"""
    client = FakeClient()
    agent = Agent(client, events=EventBus())
    DebugLoggerPlugin().install(agent.events)

    reply = agent.chat("你好")

    assert reply == "你好！"
    out = capsys.readouterr().out
    assert "[USER]" in out
    assert "[CTX]" in out
    assert "[REQ]" in out
    assert "[RESP]" in out
    assert "[ANSWER]" in out


def test_agent_without_plugin_no_output(capsys) -> None:
    """未装配插件 → 无任何日志输出（正常会话安静）。"""
    client = FakeClient()
    agent = Agent(client)  # 无插件

    reply = agent.chat("你好")

    assert reply == "你好！"
    out = capsys.readouterr().out
    assert out == ""  # 回归保护：无日志


def test_agent_with_plugin_tool_chain(capsys) -> None:
    """带插件的工具调用链：user→req→resp(tool)→tool→answer。"""
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
    client = FakeClient(script=[
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="get_time", arguments={})],
                   assistant_message=tool_call_msg),
        ChatResult(content="时间是10点", tool_calls=None,
                   assistant_message={"role": "assistant", "content": "时间是10点"}),
    ])
    agent = Agent(client, events=EventBus())
    DebugLoggerPlugin().install(agent.events)

    reply = agent.chat("现在几点")

    assert reply == "时间是10点"
    out = capsys.readouterr().out
    assert "[TOOL]" in out          # 工具执行被记录
    assert "get_time" in out
    assert "[ANSWER]" in out
