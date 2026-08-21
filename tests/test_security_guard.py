"""安全审核插件测试：黑名单拦截 + 放行 + 集成验证。

方案：docs/plans/2026-08-19-安全审核插件方案.md（决策点 1-7 已批准）
"""

from qi_agent.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.security_guard import SecurityGuardPlugin


def _make_plugin(blacklist: dict | None = None) -> SecurityGuardPlugin:
    """构造带指定黑名单的插件。"""
    return SecurityGuardPlugin(config={"blacklist": blacklist or {}})


def test_hit_blocks_shell() -> None:
    """shell 命令命中黑名单应返回 [安全拦截] 且含关键词。"""
    plugin = _make_plugin({"shell": ["git push"]})
    result = plugin._on_tool_call(
        "shell", {"command": "git push --force origin main"}
    )
    assert result is not None
    assert "[安全拦截]" in result
    assert "git push" in result


def test_miss_allows() -> None:
    """安全命令应放行（返回 None）。"""
    plugin = _make_plugin({"shell": ["git push"]})
    assert plugin._on_tool_call("shell", {"command": "pwd"}) is None


def test_no_rule_tool_allows() -> None:
    """未配置规则的工具应放行。"""
    plugin = _make_plugin({"shell": ["git push"]})
    assert plugin._on_tool_call("read_file", {"path": "README.md"}) is None


def test_case_insensitive() -> None:
    """匹配应大小写不敏感。"""
    plugin = _make_plugin({"shell": ["git push"]})
    result = plugin._on_tool_call("shell", {"command": "GIT PUSH --force"})
    assert result is not None
    assert "[安全拦截]" in result


def test_arguments_missing_allows() -> None:
    """arguments 缺参数应放行（不崩溃，防御性）。"""
    plugin = _make_plugin({"shell": ["git push"]})
    assert plugin._on_tool_call("shell", {}) is None


def test_unknown_tool_allows() -> None:
    """未知工具名（无参数映射）应放行。"""
    plugin = _make_plugin({"shell": ["git push"]})
    assert plugin._on_tool_call("mystery_tool", {"x": "git push"}) is None


class FakeShellClient:
    """测试替身：第一轮请求调用 shell("git push --force")，之后返回文本。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="shell",
                        arguments={"command": "git push --force"},
                    )
                ],
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "shell",
                                "arguments": '{"command": "git push --force"}',
                            },
                        }
                    ],
                },
            )
        return ChatResult(
            content="已拒绝",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "已拒绝"},
        )


def test_plugin_intercepts_in_loop() -> None:
    """集成：agent 循环中黑名单拦截生效，工具未执行、拦截值回填。"""
    bus = EventBus()
    plugin = _make_plugin({"shell": ["git push"]})
    plugin.install(bus)
    agent = Agent(FakeShellClient(), events=bus)
    agent.chat("帮我 push 一下")
    tool_msg = next(m for m in agent.history if m["role"] == "tool")
    assert "[安全拦截]" in tool_msg["content"]
    assert "git push" in tool_msg["content"]


# ── 三档判定（方案 v0.4.18：白名单放行 / 危险→NEED_APPROVAL / 红线硬拒）─────


def test_approval_prefix_classified() -> None:
    """可审批命令（git push/rm/curl 等）→ NEED_APPROVAL 标记（新档）。"""
    plugin = SecurityGuardPlugin()
    result = plugin._on_tool_call("shell", {"command": "git push origin main"})
    assert result == "NEED_APPROVAL:git push origin main"
    result = plugin._on_tool_call("shell", {"command": "rm -rf /tmp/x"})
    assert result.startswith("NEED_APPROVAL:")
    result = plugin._on_tool_call("shell", {"command": "curl http://x"})
    assert result.startswith("NEED_APPROVAL:")
    result = plugin._on_tool_call("shell", {"command": "del C:\\x.txt"})
    assert result.startswith("NEED_APPROVAL:")


def test_hardline_not_approvable() -> None:
    """红线（format/shutdown/reboot）→ [安全拦截]，不产生 NEED_APPROVAL。"""
    plugin = SecurityGuardPlugin()
    for cmd in ("format C:", "shutdown /s", "reboot", "mkfs /dev/sda"):
        result = plugin._on_tool_call("shell", {"command": cmd})
        assert result.startswith("[安全拦截]"), cmd
        assert not result.startswith("NEED_APPROVAL"), cmd


def test_readonly_still_auto() -> None:
    """只读命令（dir/echo/whoami）→ None（放行，不产生审批）。"""
    plugin = SecurityGuardPlugin()
    assert plugin._on_tool_call("shell", {"command": "dir"}) is None
    assert plugin._on_tool_call("shell", {"command": "echo hello"}) is None
    assert plugin._on_tool_call("shell", {"command": "whoami"}) is None


def test_redline_not_approvable() -> None:
    """红线（敏感路径）→ [安全拦截]，不产生 NEED_APPROVAL。"""
    plugin = SecurityGuardPlugin()
    # .env 读取（敏感路径规则）→ 硬拒（不是审批档）
    result = plugin._on_tool_call("shell", {"command": "type .env"})
    assert result.startswith("[安全拦截]")
    assert not result.startswith("NEED_APPROVAL")
    # 管道/重定向 → security_guard 放行（None）——那是 shell 工具层
    # 危险关键词的职责（test_shell_unapproved_still_blocked 覆盖），
    # 审批档判定只负责"危险但可审"的命令
    result = plugin._on_tool_call("shell", {"command": "dir | findstr x"})
    assert result is None


# ── 路径规则（方案 v0.4.11：内置安全底线，修复 .git 绕过漏洞）──────────────


def test_sensitive_path_git_blocked() -> None:
    """shell 读取 .git 下文件应被路径规则拦截（真实对抗暴露的绕过）。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("shell", {"command": "type .git\\config"})
    assert result is not None
    assert "[安全拦截]" in result
    assert "敏感路径" in result


def test_sensitive_path_env_blocked() -> None:
    """shell 读取 .env 应被拦截。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("shell", {"command": "type .env"})
    assert result is not None
    assert "[安全拦截]" in result


def test_normal_path_allowed() -> None:
    """普通文件路径应放行。"""
    plugin = _make_plugin()
    assert plugin._on_tool_call("shell", {"command": "type README.md"}) is None


def test_no_path_command_allowed() -> None:
    """无路径 token 的命令应放行。"""
    plugin = _make_plugin()
    assert plugin._on_tool_call("shell", {"command": "dir"}) is None


def test_quoted_path_blocked() -> None:
    """带引号的敏感路径也应被拦截（去引号后检查）。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call(
        "shell", {"command": 'type "C:\\repo\\.git\\config"'}
    )
    assert result is not None
    assert "[安全拦截]" in result


def test_blacklist_and_path_both_work() -> None:
    """黑名单与路径规则独立生效：黑名单命中返回黑名单原因。"""
    plugin = _make_plugin({"shell": ["git push"]})
    # 黑名单命中（不依赖路径规则）
    blacklist_hit = plugin._on_tool_call("shell", {"command": "git push origin"})
    assert blacklist_hit is not None
    assert "危险关键词" in blacklist_hit
    # 路径规则命中（不依赖黑名单）
    path_hit = plugin._on_tool_call("shell", {"command": "type .env"})
    assert path_hit is not None
    assert "敏感路径" in path_hit


# ── run_python 沙箱降级判据（v0.4.23，方案 2026-08-21） ───────────────────


def test_run_python_downgrade_needs_approval() -> None:
    """run_python 代码 import 受限白名单外模块 → NEED_APPROVAL 降级档。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("run_python", {"code": "import requests\nprint(1)"})
    assert isinstance(result, str)
    assert result.startswith("NEED_APPROVAL:")
    assert "requests" in result


def test_run_python_no_downgrade_allowed() -> None:
    """白名单内模块（math）或普通代码 → 放行（None）。"""
    plugin = _make_plugin()
    assert plugin._on_tool_call("run_python", {"code": "print(1 + 1)"}) is None
    assert plugin._on_tool_call("run_python", {"code": "import math\nprint(1)"}) is None


def test_run_python_downgrade_from_import() -> None:
    """from X import Y 同样触发降级判据。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("run_python", {"code": "from pandas import read_csv"})
    assert isinstance(result, str)
    assert result.startswith("NEED_APPROVAL:")
    assert "pandas" in result


# ── shell 代码执行类命令 = 沙箱升级档（v0.4.23，弹窗透明） ───────────────


def test_code_exec_command_sandbox_escalation() -> None:
    """python/py/node 等代码执行类命令 → 沙箱升级档（NEED_APPROVAL:沙箱升级:）。"""
    plugin = _make_plugin()
    for cmd in ("python -c 'print(1)'", "py -c 'print(1)'", "node -e 'x'",
                "pip install requests", "npm install"):
        result = plugin._on_tool_call("shell", {"command": cmd})
        assert isinstance(result, str), f"{cmd} 应判档"
        assert result.startswith("NEED_APPROVAL:沙箱升级:"), f"{cmd} → {result}"


def test_code_exec_prefix_unaffected() -> None:
    """非代码执行命令保持普通审批档（沙箱升级判据不误伤）。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("shell", {"command": "rm /tmp/x"})
    assert result == "NEED_APPROVAL:rm /tmp/x"  # 普通档（无沙箱升级前缀）


def test_code_exec_whitelist_unchanged() -> None:
    """只读白名单命令（pwd 等）不受影响。"""
    plugin = _make_plugin()
    assert plugin._on_tool_call("shell", {"command": "pwd"}) is None
