"""审批插件测试（2026-09-13 选项语义化改造）：走 InteractionProvider + 选项对象。

改前：弹窗选项是 CLI 惯用词 `["y", "n", "a"]`，语义靠各外壳自行映射。
改后：选项是 `InteractionOption(value=once|session|deny, label=中文文案)`——
决策层给语义，外壳只管渲染；档位裁剪（沙箱档没有 session）在决策层完成。
回答缺失 / 非 tty / 无 provider → fail-closed 拒绝（不变）。
"""

import pytest

from qi_agent.events import EventBus
from qi_agent.interaction import (
    InteractionOption,
    InteractionUnavailableError,
    set_interaction_provider,
)
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.builtin.approval_gate import ApprovalGatePlugin
from qi_agent.tools.decision import (
    SEC_APPROVAL_ESCALATION,
    SEC_APPROVAL_GENERAL,
    SEC_APPROVAL_SANDBOX,
)


class FakeProvider:
    """可编程交互提供者：预设回答序列 + 记录问题/选项/meta。"""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = list(answers or [])
        self.questions: list[str] = []
        self.options_list: list[list[InteractionOption] | None] = []
        self.metas: list[dict | None] = []

    def ask(self, question: str, options=None, timeout: float | None = None,
            *, meta: dict | None = None) -> str:
        self.questions.append(question)
        self.options_list.append(options)
        self.metas.append(meta)
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


def _values(provider: FakeProvider) -> list[str]:
    """最后一次提问的选项值序列（机器语义）。"""
    options = provider.options_list[-1] or []
    return [o.value for o in options]


def _labels(provider: FakeProvider) -> list[str]:
    options = provider.options_list[-1] or []
    return [o.label for o in options]


class FakeShellClient:
    """测试替身：shell 执行命令（配合审批插件链路）。"""

    def __init__(self, command: str) -> None:
        self._command = command

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
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
    from qi_agent.agents.agent import Agent
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


# ── 端到端：决策 → 执行 / 拒绝 ─────────────────────────────────────────────


def test_approval_event_denies(fake_provider) -> None:
    """审批插件拒绝 → 工具不执行，回填 [审批拒绝]。"""
    _set_answers(fake_provider, ["deny"])
    plugin = ApprovalGatePlugin()
    agent = _make_agent("git push origin main", plugin)
    agent.chat("帮我 push")
    assert "审批拒绝" in _tool_output(agent)
    assert "git push" in _tool_output(agent)


def test_approval_event_agrees(fake_provider) -> None:
    """审批插件同意（once）→ 工具执行（approved 注入，命令真实执行）。"""
    _set_answers(fake_provider, ["once"])
    plugin = ApprovalGatePlugin()
    agent = _make_agent("echo approved-ok", plugin)
    agent.chat("跑个 echo")
    assert "审批拒绝" not in _tool_output(agent)


def test_approval_fail_closed() -> None:
    """无审批插件（评测环境）→ 需审批命令拒绝，不执行。"""
    agent = _make_agent("git push origin main", None)
    agent.chat("帮我 push")
    assert "审批拒绝" in _tool_output(agent)


def test_approval_no_interaction_denies() -> None:
    """交互不可用（无 provider = 非 tty 评测/管道）→ 自动拒绝（fail-closed）。"""
    set_interaction_provider(None)
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval("rm /tmp/x") is False


# ── 选项语义（决策层唯一来源）────────────────────────────────────────────


def test_general_options_are_semantic(fake_provider) -> None:
    """常规档：once / session / deny 三选项 + 中文文案（前端零硬编码）。"""
    _set_answers(fake_provider, ["once"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval("git push origin main") is True
    assert _values(fake_provider) == ["once", "session", "deny"]
    assert "允许" in _labels(fake_provider)[0]
    assert "本会话" in _labels(fake_provider)[1]
    assert "拒绝" in _labels(fake_provider)[2]


def test_session_choice_remembers_prefix(fake_provider) -> None:
    """session=本会话总是允许 → 记住命令前缀，同前缀第二次不再弹窗。"""
    _set_answers(fake_provider, ["session"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval("rm /tmp/a") is True
    _set_answers(fake_provider, [])  # 回答耗尽 → 若再弹窗即失败
    assert plugin._on_tool_approval("rm /tmp/b") is True


def test_unknown_choice_denies(fake_provider) -> None:
    """非选项值（模型/前端乱传）→ 拒绝，且不记忆前缀。"""
    _set_answers(fake_provider, ["maybe"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval("rm /tmp/a") is False
    assert plugin._approved_prefixes == []


def test_meta_carries_session_and_code(fake_provider) -> None:
    """meta 带会话身份与档位（Web 弹框渲染依据；会话身份取 bus.context_id）。"""
    _set_answers(fake_provider, ["deny"])
    plugin = ApprovalGatePlugin()
    bus = EventBus(context_id="ctx_test_1")
    plugin.install(bus)
    plugin._on_tool_approval("git push", name="shell", code=SEC_APPROVAL_GENERAL,
                             arguments={"command": "git push"})
    meta = fake_provider.metas[-1] or {}
    assert meta.get("session") == "ctx_test_1"
    assert meta.get("tool") == "shell"
    assert meta.get("code") == SEC_APPROVAL_GENERAL
    assert meta.get("command") == "git push"


def test_meta_carries_tool_call_id(fake_provider) -> None:
    """meta 带 tool_call_id：前端把审批绑到"触发它的那一行工具"（M2-a 内联记录）。"""
    _set_answers(fake_provider, ["deny"])
    plugin = ApprovalGatePlugin()
    bus = EventBus(context_id="ctx_test_1")
    plugin.install(bus)
    plugin._on_tool_approval("git push", name="shell", code=SEC_APPROVAL_GENERAL,
                             arguments={"command": "git push"}, tool_call_id="call_9",
                             turn=3)
    meta = fake_provider.metas[-1] or {}
    assert meta.get("tool_call_id") == "call_9"
    assert meta.get("turn") == 3


# ── shell approved 参数（不变）─────────────────────────────────────────────


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


# ── run_python 沙箱降级审批（v0.4.23，选项改造后）──────────────────────────


def test_run_python_downgrade_prompt(fake_provider) -> None:
    """run_python 降级弹窗：专用文案 + 选项只有 once/deny（无 session=总允许）。"""
    _set_answers(fake_provider, ["once"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "import 'requests'（沙箱降级）",
        code=SEC_APPROVAL_SANDBOX,
    ) is True
    assert "降级沙箱" in fake_provider.questions[-1]
    assert _values(fake_provider) == ["once", "deny"]


def test_run_python_downgrade_rejects_session_value(fake_provider) -> None:
    """沙箱档收到 session（前端被篡改/模型乱传）→ 拒绝且不记忆。"""
    _set_answers(fake_provider, ["session"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "import 'x'（沙箱降级）", code=SEC_APPROVAL_SANDBOX,
    ) is False
    assert plugin._approved_prefixes == []


def test_run_python_downgrade_fail_closed() -> None:
    """run_python 降级交互不可用（评测/管道）→ 自动拒绝。"""
    set_interaction_provider(None)
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "import 'x'（沙箱降级）", code=SEC_APPROVAL_SANDBOX,
    ) is False


# ── shell 代码执行命令 = 沙箱升级审批（v0.4.23，弹窗透明）──────────────────


def test_sandbox_escalation_prompt(fake_provider) -> None:
    """沙箱升级弹窗：专用文案（⚠️ 完整权限）+ 无 session 选项。"""
    _set_answers(fake_provider, ["once"])
    plugin = ApprovalGatePlugin()
    assert plugin._on_tool_approval(
        "python -c 'print(1)'", code=SEC_APPROVAL_ESCALATION,
    ) is True
    prompt = fake_provider.questions[-1]
    assert "完整权限" in prompt
    assert "沙箱" in prompt
    assert _values(fake_provider) == ["once", "deny"]


def test_sandbox_escalation_rejects_session_value(fake_provider) -> None:
    """沙箱升级档收到 session → 拒绝且不记忆（防变相全局放行代码执行）。"""
    _set_answers(fake_provider, ["session"])
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
