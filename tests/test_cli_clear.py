"""CLI 层 clear 命令测试：验证命令行解析与 Agent 的交互。

覆盖 cli.py 中 CLEAR_COMMANDS 分支：输入 clear 应触发 agent.clear_context()
且不消耗 API 调用；大小写不敏感；不影响后续对话。
"""

from unittest import mock

from qi_agent.agent import Agent
from qi_agent.cli import main
from qi_agent.llm import ChatResult
from qi_agent.tools import get_time, read_file, shell  # noqa: F401  导入即注册内置工具


class FakeClient:
    """测试替身：记录 chat 被调用的次数。"""

    def __init__(self) -> None:
        self.chat_count = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.chat_count += 1
        return ChatResult(
            content="ok",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "ok"},
        )


def run_cli_with_inputs(inputs: list[str]) -> tuple[Agent, FakeClient]:
    """用 mock 驱动 main()：依次提供用户输入，返回被注入的 agent 和 client。

    注意：main() 内部创建 Agent，这里通过 patch Agent 返回预构造实例，
    并捕获 agent.chat 的调用情况。
    """
    agent = Agent(FakeClient())
    inputs_iter = iter(inputs)

    with mock.patch("builtins.input", side_effect=lambda prompt="": next(inputs_iter)):
        with mock.patch("qi_agent.cli.load_api_key", return_value="sk-test"):
            with mock.patch("qi_agent.cli.Agent", return_value=agent):
                with mock.patch("qi_agent.cli.LLMClient"):
                    main(argv=[])  # 注入空 argv，避免误读 pytest 的参数

    return agent, agent.client


def test_clear_command_triggers_clear_context() -> None:
    """输入 clear 应调用 agent.clear_context()。"""
    agent, _ = run_cli_with_inputs(["你好", "clear", "exit"])
    # clear 后历史只剩 system 一条（clear_context 生效的证据）
    assert len(agent.history) == 1


def test_clear_command_does_not_call_api() -> None:
    """输入 clear 不应消耗 API 调用（continue 跳过 chat）。"""
    _, client = run_cli_with_inputs(["你好", "clear", "exit"])
    # 只有"你好"消耗了一次调用，clear 和 exit 都不调 API
    assert client.chat_count == 1


def test_clear_uppercase_insensitive() -> None:
    """clear 命令应大小写不敏感（Clear/CLEAR 都应生效）。"""
    agent, _ = run_cli_with_inputs(["你好", "CLEAR", "exit"])
    assert len(agent.history) == 1


def test_clear_command_with_whitespace() -> None:
    """带空格的 clear（如 ' clear '）应被 strip 后识别。"""
    agent, _ = run_cli_with_inputs(["你好", "  clear  ", "exit"])
    assert len(agent.history) == 1


def test_clear_then_continue_chat() -> None:
    """clear 之后应能继续正常对话（新会话上下文）。"""
    agent, client = run_cli_with_inputs(["第一轮", "clear", "第二轮", "exit"])
    assert client.chat_count == 2  # 两轮真实对话
    # 第二轮后历史: system + user + assistant = 3 条（新会话，不含第一轮）
    assert len(agent.history) == 3
