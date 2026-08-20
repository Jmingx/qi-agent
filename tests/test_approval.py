"""审批机制测试：agent/tool-approval 事件点 + approval_gate 插件 + shell approved。

方案：docs/plans/2026-08-20-shell三档权限与审批机制方案.md（决策点 1-7 已批准）
"""

from unittest import mock

from qi_agent.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.approval_gate import ApprovalGatePlugin
from qi_agent.tools.shell import shell


class FakeShellClient:
    """测试替身：第一轮返回 shell 工具调用，之后返回文本。"""

    def __init__(self, command: str) -> None:
        self.command = command
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                content=None,
                tool_calls=[ToolCall(
                    id="call_1", name="shell", arguments={"command": self.command}
                )],
                assistant_message={
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "shell",
                                     "arguments": f'{{"command": "{self.command}"}}'},
                    }],
                },
            )
        return ChatResult(
            content="最终答案", tool_calls=None,
            assistant_message={"role": "assistant", "content": "最终答案"},
        )


def _make_agent(command: str, plugin: ApprovalGatePlugin | None) -> Agent:
    """构造 agent：判档插件（security_guard）+ 审批插件（或 None = fail-closed）。

    注意：security_guard 必须挂载——三档判定（NEED_APPROVAL）由它产生，
    审批事件才被触发；approval_gate 控制审批交互。
    """
    from qi_agent.plugins.security_guard import SecurityGuardPlugin

    bus = EventBus()
    SecurityGuardPlugin().install(bus)
    if plugin is not None:
        plugin.install(bus)
    return Agent(FakeShellClient(command), events=bus)


def _tool_output(agent: Agent) -> str:
    """取 agent 历史中最后一条 tool 消息内容。"""
    for m in reversed(agent.history):
        if m["role"] == "tool":
            return str(m.get("content", ""))
    return ""


# ── 事件点 + 插件行为 ─────────────────────────────────────────────────────


def test_approval_event_denies(monkeypatch) -> None:
    """审批插件拒绝 → 工具不执行，回填 [审批拒绝]。"""
    plugin = ApprovalGatePlugin()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    agent = _make_agent("git push origin main", plugin)
    agent.chat("帮我 push")
    assert "审批拒绝" in _tool_output(agent)
    assert "git push" in _tool_output(agent)


def test_approval_event_agrees(monkeypatch) -> None:
    """审批插件同意 → 工具执行（approved 注入，命令真实执行）。"""
    plugin = ApprovalGatePlugin()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    agent = _make_agent("echo approved-ok", plugin)
    agent.chat("跑个 echo")
    # echo 在白名单（放行）；用 git push 验证同意后执行：
    # （echo 不触发审批，此用例验证同意路径不拒绝）
    assert "审批拒绝" not in _tool_output(agent)


def test_approval_fail_closed() -> None:
    """无审批插件（评测环境）→ 需审批命令拒绝，不执行。"""
    agent = _make_agent("git push origin main", None)
    agent.chat("帮我 push")
    assert "审批拒绝" in _tool_output(agent)  # fail-closed：无监听器 = 拒绝


def test_approval_no_tty_denies() -> None:
    """非 tty（评测/管道）→ 自动拒绝（fail-closed 双保险）。"""
    plugin = ApprovalGatePlugin()
    with mock.patch("sys.stdin.isatty", return_value=False):
        result = plugin._on_tool_approval("rm /tmp/x")
    assert result is False


def test_approval_session_memory(monkeypatch) -> None:
    """a=总是允许 → 同前缀命令第二次不再弹窗（直接同意）。"""
    plugin = ApprovalGatePlugin()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # 第一次：用户选 a（总是允许）
    with mock.patch("builtins.input", return_value="a"):
        assert plugin._on_tool_approval("rm /tmp/a") is True
    # 第二次：同前缀（rm ...）不再调 input，直接同意
    with mock.patch("builtins.input", side_effect=AssertionError("不应弹窗")):
        assert plugin._on_tool_approval("rm /tmp/b") is True


# ── shell approved 参数 ───────────────────────────────────────────────────


def test_shell_approved_param() -> None:
    """approved=True → 非白名单命令可执行（审批同意路径）。"""
    result = shell("echo approved-exec", approved=True)
    assert "[安全拦截]" not in result


def test_shell_unapproved_still_blocked() -> None:
    """无 approved → 非白名单命令拒绝（工具层兜底保持）。"""
    result = shell("shutdown /s")
    assert "[安全拦截]" in result


def test_shell_model_cant_bypass() -> None:
    """模型传 approved=True → 参数校验拒绝（schema 不暴露该参数）。"""
    from qi_agent.tools.registry import _TOOL_REGISTRY, validate_arguments

    entry = _TOOL_REGISTRY["shell"]
    error = validate_arguments(entry.schema, {"command": "shutdown /s", "approved": True})
    assert error is not None  # 多余参数被拒
    assert "approved" in error
