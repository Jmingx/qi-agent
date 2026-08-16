"""调试日志器测试：验证 DebugLogger 输出、Agent 集成、--debug 开关行为。"""

from qi_agent.agent import Agent
from qi_agent.debugger import DebugLogger
from qi_agent.llm import ChatResult, ToolCall


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


def test_logger_disabled_no_output(capsys) -> None:
    """enabled=False 时不应有任何输出。"""
    logger = DebugLogger(enabled=False)
    logger.log_user_input("你好")
    logger.log_request([{"role": "user", "content": "你好"}], None)
    logger.log_response(ChatResult(content="ok", tool_calls=None,
                                   assistant_message={"role": "assistant", "content": "ok"}))
    logger.log_tool_call("get_time", {}, "2026-08-14")
    captured = capsys.readouterr()
    assert captured.out == ""


def test_log_request_contains_messages(capsys) -> None:
    """log_request 输出应包含消息内容和工具定义。"""
    logger = DebugLogger()
    messages = [{"role": "user", "content": "现在几点"}]
    tools = [{"type": "function", "function": {"name": "get_time"}}]
    logger.log_request(messages, tools)
    out = capsys.readouterr().out
    assert "[REQ]" in out
    assert "现在几点" in out
    assert "get_time" in out


def test_log_response_content(capsys) -> None:
    """log_response 输出应包含模型文本响应。"""
    logger = DebugLogger()
    logger.log_response(ChatResult(content="答案是42", tool_calls=None,
                                   assistant_message={"role": "assistant", "content": "答案是42"}))
    out = capsys.readouterr().out
    assert "[RESP]" in out
    assert "答案是42" in out


def test_log_response_tool_calls(capsys) -> None:
    """log_response 输出应包含工具调用信息。"""
    logger = DebugLogger()
    tc = ToolCall(id="c1", name="get_time", arguments={})
    logger.log_response(ChatResult(content=None, tool_calls=[tc],
                                   assistant_message={"role": "assistant", "content": None}))
    out = capsys.readouterr().out
    assert "[RESP]" in out
    assert "get_time" in out


def test_log_tool_call_contains_args_and_result(capsys) -> None:
    """log_tool_call 输出应包含入参和结果。"""
    logger = DebugLogger()
    logger.log_tool_call("read_file", {"path": "/tmp/a.txt"}, "文件内容")
    out = capsys.readouterr().out
    assert "[TOOL]" in out
    assert "read_file" in out
    assert "文件内容" in out


def test_agent_with_logger_logs_chain(capsys) -> None:
    """Agent 带 logger 时，完整链路被记录（user→req→resp→answer）。"""
    client = FakeClient()
    logger = DebugLogger()
    agent = Agent(client, logger=logger)

    reply = agent.chat("你好")

    assert reply == "你好！"
    out = capsys.readouterr().out
    assert "[USER]" in out
    assert "[REQ]" in out
    assert "[RESP]" in out
    assert "[ANSWER]" in out


def test_agent_without_logger_unchanged(capsys) -> None:
    """不带 logger 时行为与原来一致（无任何日志输出）。"""
    client = FakeClient()
    agent = Agent(client)  # logger=None（默认）

    reply = agent.chat("你好")

    assert reply == "你好！"
    out = capsys.readouterr().out
    assert out == ""  # 回归保护：无日志


def test_agent_with_logger_tool_chain(capsys) -> None:
    """带 logger 的工具调用链：user→req→resp(tool)→tool→answer。"""
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
    logger = DebugLogger()
    agent = Agent(client, logger=logger)

    reply = agent.chat("现在几点")

    assert reply == "时间是10点"
    out = capsys.readouterr().out
    assert "[TOOL]" in out          # 工具执行被记录
    assert "get_time" in out
    assert "[ANSWER]" in out
