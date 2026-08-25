"""CLI 层 usage 命令测试：输入 usage/资源 应打印插件 report 且不消耗 LLM 调用。

覆盖 cli.py 中 USAGE_COMMANDS 分支（交互调整 2026-08-21）：
- usage / 资源（中文别名）→ 打印所有带 report() 的插件汇总
- 不消耗 LLM 调用（命令分支在 agent.chat 之前）
"""

from unittest import mock

from qi_agent.agents.agent import Agent
from qi_agent.cli import main
from qi_agent.llm import ChatResult
from qi_agent.tools.builtin import get_time, read_file, shell  # noqa: F401  导入即注册内置工具


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

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta=None,
    ) -> ChatResult:
        """流式替身：逐块回调（cli 现在总是流式，必须支持）。"""
        self.chat_count += 1
        return ChatResult(
            content="ok",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "ok"},
        )


class FakeResourcePlugin:
    """假资源监控插件：验证 usage 命令打印 report()。"""

    def report(self) -> str:
        return "  [资源] 累计消耗 1,000 tokens"


def run_cli_with_inputs(inputs: list[str], plugins=None) -> tuple[Agent, FakeClient]:
    """mock 驱动 main()：依次提供用户输入，注入假插件列表。"""
    agent = Agent(FakeClient())
    inputs_iter = iter(inputs)

    with mock.patch("builtins.input", side_effect=lambda prompt="": next(inputs_iter)):
        with mock.patch(
            "qi_agent.cli.build_agent", return_value=type("B", (), {"agent": agent,
                                      "manager": type("M", (), {"get_context":
                                      lambda self, cid: agent.context})(),
                                      "context_id": "ctx1",
                                      "agent_id": "main",
                                      "installed": plugins or []})()
        ):
            main(argv=[])

    return agent, agent.client


def test_usage_command_prints_report(capsys) -> None:
    """输入 usage → 打印插件 report（资源消耗汇总）。"""
    run_cli_with_inputs(["usage", "exit"], plugins=[FakeResourcePlugin()])
    out = capsys.readouterr().out
    assert "累计消耗 1,000 tokens" in out


def test_usage_command_chinese_alias(capsys) -> None:
    """中文别名"资源"同样触发。"""
    run_cli_with_inputs(["资源", "exit"], plugins=[FakeResourcePlugin()])
    out = capsys.readouterr().out
    assert "累计消耗 1,000 tokens" in out


def test_usage_command_no_llm_call() -> None:
    """usage 命令不消耗 LLM 调用（命令分支在 agent.chat 之前）。"""
    _, client = run_cli_with_inputs(["usage", "exit"], plugins=[FakeResourcePlugin()])
    assert client.chat_count == 0


def test_usage_command_no_plugins(capsys) -> None:
    """无插件（无 report）时命令不报错、无输出。"""
    run_cli_with_inputs(["usage", "exit"], plugins=[])
    out = capsys.readouterr().out
    assert "累计消耗" not in out
