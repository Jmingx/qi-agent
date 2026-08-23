"""CLI 阶段 C 收尾命令测试：/context（占用构成）+ /compact（手动压缩）。

方案：docs/plans/2026-08-23-阶段C收尾方案.md（C4 手动命令）
覆盖：
- /context → 打印占用构成（估算分段 + 真实 usage 累计），不消耗 LLM
- /compact → 强制同步压缩（消息数下降 + 摘要展示）
- /compact 无插件/无历史 → 优雅提示
"""

from unittest import mock

from qi_agent.agents.agent import Agent
from qi_agent.cli import main
from qi_agent.llm import ChatResult
from qi_agent.plugins.builtin.context_manager import ContextManagerPlugin


class FakeClient:
    """测试替身：chat 返回固定结果（含 usage 供累计）。"""

    def __init__(self) -> None:
        self.chat_count = 0

    def _result(self) -> ChatResult:
        self.chat_count += 1
        return ChatResult(
            content="ok", tool_calls=None,
            assistant_message={"role": "assistant", "content": "ok"},
            usage={"prompt_tokens": 100, "completion_tokens": 20,
                   "total_tokens": 120},
        )

    def chat(self, messages, tools=None) -> ChatResult:
        return self._result()

    def chat_stream(self, messages, tools=None, on_delta=None) -> ChatResult:
        return self._result()


def run_cli_with_inputs(inputs: list[str], plugins=None,
                        history: list[dict] | None = None) -> Agent:
    """mock 驱动 main()：注入假插件列表 + 预设历史。"""
    agent = Agent(FakeClient())
    if history:
        agent.messages = history
    inputs_iter = iter(inputs)
    with mock.patch("builtins.input",
                    side_effect=lambda prompt="": next(inputs_iter)):
        with mock.patch(
            "qi_agent.cli.build_runtime", return_value=type("B", (), {
                "manager": type("M", (), {
                    "get_context": lambda self, cid: agent.context,
                    "run": lambda self, cid, text, stream_callback=None:
                        agent.chat(text),
                    "poll": lambda self, cid: None,
                    "stop": lambda self, cid: True,
                })(),
                "context_id": "ctx1",
                "installed": plugins or [],
                "get_context": lambda self: agent.context,
            })()
        ):
            main(argv=[])
    return agent


def _history(n: int = 12) -> list[dict]:
    """构造 n 条非 system 消息（交替 user/assistant）。"""
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"历史消息{i}"})
    return msgs


def _plugin(summarizer=None) -> ContextManagerPlugin:
    """构造 context_manager 插件（mock 摘要器）。"""
    return ContextManagerPlugin(
        {}, summarizer=summarizer or (lambda msgs: "关键事实：测试摘要"))


def test_context_command_prints_breakdown(capsys) -> None:
    """/context → 打印占用构成（system/tools/历史 估算）+ 真实 usage。"""
    run_cli_with_inputs(["context", "exit"])
    out = capsys.readouterr().out
    assert "[上下文] 总" in out
    assert "占用窗口" in out
    assert "[用量] 累计" in out


def test_context_command_chinese_alias(capsys) -> None:
    """中文别名"上下文"同样触发。"""
    run_cli_with_inputs(["上下文", "exit"])
    out = capsys.readouterr().out
    assert "[上下文] 总" in out


def test_context_command_no_llm_call() -> None:
    """/context 不消耗 LLM 调用（命令分支在 agent.chat 之前）。"""
    run_cli_with_inputs(["context", "exit"])
    # FakeClient 无直接引用——通过 agent 检查
    agent = run_cli_with_inputs(["context", "exit"])
    assert agent.client.chat_count == 0


def test_compact_command_compresses(capsys) -> None:
    """/compact → 强制压缩：消息数下降 + 摘要展示。"""
    history = _history(12)
    agent = run_cli_with_inputs(
        ["compact", "exit"],
        plugins=[_plugin()],
        history=history,
    )
    out = capsys.readouterr().out
    assert "[compact] 压缩完成" in out
    assert "→" in out
    assert "关键事实" in out  # 摘要展示
    assert len(agent.history) < len(history)  # 消息数下降


def test_compact_command_no_plugin(capsys) -> None:
    """无 context_manager 插件 → 优雅提示。"""
    run_cli_with_inputs(["compact", "exit"], plugins=[])
    out = capsys.readouterr().out
    assert "上下文管理插件未启用" in out


def test_compact_command_no_history(capsys) -> None:
    """仅 system 消息（无可压缩）→ 提示无可压缩历史。"""
    run_cli_with_inputs(
        ["compact", "exit"],
        plugins=[_plugin()],
        history=[{"role": "system", "content": "sys"}],
    )
    out = capsys.readouterr().out
    assert "无可压缩历史" in out


def test_compact_command_no_llm_call() -> None:
    """/compact 不消耗主对话 LLM 调用（压缩走独立摘要器）。"""
    agent = run_cli_with_inputs(
        ["compact", "exit"],
        plugins=[_plugin()],
        history=_history(12),
    )
    assert agent.client.chat_count == 0
