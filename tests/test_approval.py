"""审批插件测试（2026-08-23 交互抽象层改造）：走 InteractionProvider。

改前：monkeypatch builtins.input 模拟用户输入
改后：注入 FakeProvider（ask_user → provider.ask）——与 clarify 同一
交互通道；无 provider / 回答耗尽 = 交互不可用 → fail-closed 拒绝。
"""

import pytest

from qi_agent.events import EventBus
from qi_agent.interaction import (
    InteractionUnavailableError,
    set_interaction_provider,
)
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.builtin.approval_gate import ApprovalGatePlugin
from qi_agent.tools.decision import (
    SEC_APPROVAL_ESCALATION,
    SEC_APPROVAL_SANDBOX,
)


class FakeProvider:
    """可编程交互提供者：预设回答序列 + 记录问题与选项。"""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = list(answers or [])
        self.questions: list[str] = []
        self.choices_list: list[list | None] = []

    def ask(self, question: str, choices: list[str] | None = None,
            timeout: float | None = None) -> str:
        self.questions.append(question)
        self.choices_list.append(choices)
        if not self.answers:
            raise InteractionUnavailableError("回答耗尽（测试断言不应弹窗）")
        return self.answers.pop(0)


@pytest.fixture
def fake_provider(monkeypatch) -> FakeProvider:
    """注入假 provider（approval_gate 走 ask_user → 本 provider）。"""
    provider = FakeProvider()
    set_interaction_provider(provider)
    yield provider
    set_interaction_provider(None)


def _set_answers(provider: FakeProvider, answers: list[str]) -> None:
    provider.answers = list(answers)


class FakeShellClient:
    """测试替身：shell 执行命令（配合审批插件链路）。"""

    def __init__(self, command: str) -> None:
        self._command = command

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        # 模型第一轮：请求 shell 执行命令
        tool_call_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "shell", "arguments": f'{{"command": "{self._command}"}}'}}
            ],
        }
        return ChatResult(
            content=None,
            tool_calls=[ToolCall(id="c1", name="shell", arguments={"command": self._command})],
            assistant_message=tool_call_msg,
        )


def _make_agent(command: str, plugin: ApprovalGatePlugin | None) -> object:
    """构造 agent：判档插件（security_guard）+ 审批插件（或 None = fail-closed）。"""
    from qi_agent.agent import Agent
    from qi_agent.plugins.builtin.security_guard import SecurityGuardPlugin

    bus = EventBus()
    SecurityGuardPlugin().install(bus)
    if plugin is not None:
        plugin.install(bus)
    return Agent(FakeShellClient(command), events=bus)


def _tool_output(agent) -> str:
    """取 agent 历史中最后一条 tool 消息内容。"""
    for m in reversed(agent.history):
        if m["role"] == "tool":
            return str(m.get("content", ""))
    return ""


# ── 事件点 + 插件行为 ─────────────────────────────────────────────────────


def test_approval_event_denies(fake_provider) -> None:
    """审批插件拒绝 → 工具不执行，回填 [审批拒绝]。"""
    _set_answers(fake_provider, ["n"])
    plugin = ApprovalGatePlugin()
    agent = _make_agent("git push origin main", plugin)
    agent.chat("帮我 push")
    assert "审批拒绝" in _tool_output(agent)
    assert "git push" in _tool_output(agent)


def test_approval_event_agrees(fake_provider) -> None:
    """审批插件同意 → 工具执行（approved 注入，命令真实执行）。"""
    _set_answers(fake_provider, ["y"])
    plugin = ApprovalGatePlugin()
    agent = _make_agent("echo approved-ok", plugin)
    agent.chat("跑个 echo")
    assert "审批拒绝" not in _tool_output(agent)


def test_approval_fail_closed() -> None:
    """无审批插件（评测环境）→ 需审批命令拒绝，不执行。"""
    agent = _make_agent("git push origin main", None)
    agent.chat("帮我 push")
    assert "审批拒绝" in _tool_output(agent)  # fail-closed：无监听器 = 拒绝


def test_approval_no_interaction_denies() -> None:
    """交互不可用（无 provider = 非 tty 评测/管道）→ 自动拒绝（fail-closed）。"""
    set_interaction_provider(None)  # 模拟无交互环境
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval("rm /tmp/x") is False


def test_approval_session_memory(fake_provider) -> None:
    """a=总是允许 → 同前缀命令第二次不再弹窗（直接同意）。"""
    _set_answers(fake_provider, ["a"])
    plugin = ApprovalGatePlugin()
    # 第一次：用户选 a（总是允许）
    assert plugin._on_tool_approval("rm /tmp/a") is True
    # 第二次：同前缀（rm ...）不再弹窗（回答耗尽 → 不应被调用），直接同意
    _set_answers(fake_provider, [])
    assert plugin._on_tool_approval("rm /tmp/b") is True


# ── shell approved 参数 ───────────────────────────────────────────────────


def test_shell_approved_param() -> None:
    """approved=True → 非白名单命令可执行（审批同意路径）。"""
    from qi_agent.tools.builtin.shell import shell

    result = shell("echo approved-exec", approved=True)
    assert "[安全拦截]" not in result


def test_shell_unapproved_still_blocked() -> None:
    """无 approved → 非白名单命令拒绝（工具层兜底保持）。"""
    from qi_agent.tools.builtin.shell import shell

    result = shell("shutdown /s")
    assert "[安全拦截]" in result


def test_shell_model_cant_bypass() -> None:
    """模型传 approved=True → 参数校验拒绝（schema 不暴露该参数）。"""
    from qi_agent.tools.registry import _TOOL_REGISTRY, validate_arguments

    entry = _TOOL_REGISTRY["shell"]
    error = validate_arguments(entry.schema, {"command": "shutdown /s", "approved": True})
    assert error is not None  # 多余参数被拒
    assert "approved" in error


# ── run_python 沙箱降级审批（v0.4.23） ────────────────────────────────────


def test_run_python_downgrade_prompt(fake_provider) -> None:
    """run_python 降级弹窗：专用文案（含"降级沙箱"）+ 无 a=总是允许。"""
    _set_answers(fake_provider, ["y"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "import 'requests'（沙箱降级）",
        code=SEC_APPROVAL_SANDBOX,
    ) is True
    prompt = fake_provider.questions[-1]
    assert "降级沙箱" in prompt
    assert "总是允许" not in prompt  # 决策点 3：run_python 不提供 a
    assert fake_provider.choices_list[-1] == ["y", "n"]  # 选项无 a


def test_run_python_downgrade_no_always_allow(fake_provider) -> None:
    """run_python 降级：输入 a 视为拒绝且不记忆（a=总是允许 禁用）。"""
    _set_answers(fake_provider, ["a"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "import 'x'（沙箱降级）", code=SEC_APPROVAL_SANDBOX,
    ) is False
    assert plugin._approved_prefixes == []  # 未记忆


def test_run_python_downgrade_fail_closed() -> None:
    """run_python 降级交互不可用（评测/管道）→ 自动拒绝。"""
    set_interaction_provider(None)
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "import 'x'（沙箱降级）", code=SEC_APPROVAL_SANDBOX,
    ) is False


# ── shell 代码执行命令 = 沙箱升级审批（v0.4.23，弹窗透明） ───────────────


def test_sandbox_escalation_prompt(fake_provider) -> None:
    """沙箱升级弹窗：专用文案（⚠️ 完整权限）+ 无 a=总是允许。"""
    _set_answers(fake_provider, ["y"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "python -c 'print(1)'", code=SEC_APPROVAL_ESCALATION,
    ) is True
    prompt = fake_provider.questions[-1]
    assert "完整权限" in prompt
    assert "沙箱" in prompt
    assert "总是允许" not in prompt  # 代码执行档不提供 a
    assert fake_provider.choices_list[-1] == ["y", "n"]


def test_sandbox_escalation_no_always_allow(fake_provider) -> None:
    """沙箱升级输入 a → 拒绝且不记忆（防总允许=变相全局放行代码执行）。"""
    _set_answers(fake_provider, ["a"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "python -c 'x'", code=SEC_APPROVAL_ESCALATION,
    ) is False
    assert plugin._approved_prefixes == []


def test_sandbox_escalation_fail_closed() -> None:
    """沙箱升级交互不可用（评测）→ 自动拒绝。"""
    set_interaction_provider(None)
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "python -c 'x'", code=SEC_APPROVAL_ESCALATION,
    ) is False
