"""安全审核插件测试：黑名单拦截 + 放行 + 集成验证。

方案：docs/plans/2026-08-19-安全审核插件方案.md（决策点 1-7 已批准）
"""

from qi_agent.agents.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.builtin.security_guard import SecurityGuardPlugin
from qi_agent.tools.decision import (
    SEC_APPROVAL_ESCALATION,
    SEC_APPROVAL_SANDBOX,
    ToolAction,
)

# 触发全量工具注册（v0.4.26 声明式判档）：security_guard 的工具级审批
# 查 registry（ToolEntry.approval）——工具必须先注册，判档才拿到声明。
# 用真实注册测真实行为（file_delete 的模板、shell 的条件函数都在工具文件里）。
# F401: 故意副作用导入（注册工具），名字本身不被引用
import qi_agent.tools  # noqa: F401


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
    assert result.action == ToolAction.BLOCK
    assert "git push" in result.reason


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
    assert result.action == ToolAction.BLOCK


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
    assert result.action == ToolAction.NEED_APPROVAL
    assert result.command == "git push origin main"
    result = plugin._on_tool_call("shell", {"command": "rm -rf /tmp/x"})
    assert result.action == ToolAction.NEED_APPROVAL
    result = plugin._on_tool_call("shell", {"command": "curl http://x"})
    assert result.action == ToolAction.NEED_APPROVAL
    result = plugin._on_tool_call("shell", {"command": "del C:\\x.txt"})
    assert result.action == ToolAction.NEED_APPROVAL


def test_hardline_not_approvable() -> None:
    """红线（format/shutdown/reboot）→ [安全拦截]，不产生 NEED_APPROVAL。"""
    plugin = SecurityGuardPlugin()
    for cmd in ("format C:", "shutdown /s", "reboot", "mkfs /dev/sda"):
        result = plugin._on_tool_call("shell", {"command": cmd})
        assert result.action == ToolAction.BLOCK, cmd
        assert result.action != ToolAction.NEED_APPROVAL, cmd


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
    assert result.action == ToolAction.BLOCK
    assert result.action != ToolAction.NEED_APPROVAL
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
    assert result.action == ToolAction.BLOCK
    assert "敏感路径" in result.reason


def test_sensitive_path_env_blocked() -> None:
    """shell 读取 .env 应被拦截。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("shell", {"command": "type .env"})
    assert result is not None
    assert result.action == ToolAction.BLOCK


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
    assert result.action == ToolAction.BLOCK


def test_blacklist_and_path_both_work() -> None:
    """黑名单与路径规则独立生效：黑名单命中返回黑名单原因。"""
    plugin = _make_plugin({"shell": ["git push"]})
    # 黑名单命中（不依赖路径规则）
    blacklist_hit = plugin._on_tool_call("shell", {"command": "git push origin"})
    assert blacklist_hit is not None
    assert "危险关键词" in blacklist_hit.reason
    # 路径规则命中（不依赖黑名单）
    path_hit = plugin._on_tool_call("shell", {"command": "type .env"})
    assert path_hit is not None
    assert "敏感路径" in path_hit.reason


# ── run_python 沙箱降级判据（v0.4.23，方案 2026-08-21） ───────────────────


def test_run_python_downgrade_needs_approval() -> None:
    """run_python 代码 import 受限白名单外模块 → NEED_APPROVAL 降级档。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("run_python", {"code": "import requests\nprint(1)"})
    assert result.action == ToolAction.NEED_APPROVAL
    assert result.code == SEC_APPROVAL_SANDBOX
    assert "requests" in result.reason


# ── 工具级审批声明（v0.4.26 声明式判档：ToolEntry.approval）───────────────


def test_tool_approval_declared_file_delete() -> None:
    """file_delete 工具注册带审批模板 → 判档命中：NEED_APPROVAL:删除文件 <path>。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("file_delete", {"path": r"C:\tmp\x.txt"})
    assert result.action == ToolAction.NEED_APPROVAL
    assert result.command == "删除文件 C:\\tmp\\x.txt"


def test_tool_approval_template_missing_param() -> None:
    """模板命中但参数缺失 → 回退模板本身（不崩、仍判审批）。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("file_delete", {})
    assert result.action == ToolAction.NEED_APPROVAL
    assert "删除文件 {path}" in result.command


def test_tool_approval_not_declared_allows() -> None:
    """未声明 approval（默认 None）的工具 → 放行（不误伤）。"""
    plugin = _make_plugin()
    assert plugin._on_tool_call("get_time", {}) is None


def test_tool_approval_declared_contract(monkeypatch) -> None:
    """声明式契约：注册带 approval 的新工具 → 插件自动判档（零插件改动）。

    模拟\"新增需审批工具\"：register() 声明 approval，插件无需任何修改。
    """
    from qi_agent.tools.registry import _TOOL_REGISTRY, register

    def hypo_handler(target: str) -> str:
        return f"done {target}"

    register(name="hypo_tool", handler=hypo_handler, approval="假想操作 {target}")
    try:
        plugin = _make_plugin()
        result = plugin._on_tool_call("hypo_tool", {"target": "abc"})
        assert result.action == ToolAction.NEED_APPROVAL
        assert result.command == "假想操作 abc"
    finally:
        _TOOL_REGISTRY.pop("hypo_tool", None)


def test_tool_approval_callable_condition() -> None:
    """callable 条件审批：返回描述 → 审批；返回 None → 放行（write_file 覆盖档）。"""
    import tempfile
    import os

    plugin = _make_plugin()
    with tempfile.TemporaryDirectory() as tmp:
        existing = os.path.join(tmp, "exists.txt")
        open(existing, "w").write("data")
        # 覆盖已存在文件 → 审批
        r1 = plugin._on_tool_call("write_file", {"path": existing, "content": "x"})
        assert r1.action == ToolAction.NEED_APPROVAL
        assert f"覆盖写入 {existing}" in r1.command
        # 新文件（项目内？tmp 在项目外）→ 越界审批；用项目内路径测放行
        r2 = plugin._on_tool_call("write_file", {"path": "new_file.txt", "content": "x"})
        assert r2 is None


def test_run_python_no_downgrade_allowed() -> None:
    """白名单内模块（math）或普通代码 → 放行（None）。"""
    plugin = _make_plugin()
    assert plugin._on_tool_call("run_python", {"code": "print(1 + 1)"}) is None
    assert plugin._on_tool_call("run_python", {"code": "import math\nprint(1)"}) is None


def test_run_python_downgrade_from_import() -> None:
    """from X import Y 同样触发降级判据。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("run_python", {"code": "from pandas import read_csv"})
    assert result.action == ToolAction.NEED_APPROVAL
    assert result.code == SEC_APPROVAL_SANDBOX
    assert "pandas" in result.reason


# ── shell 代码执行类命令 = 沙箱升级档（v0.4.23 弹窗透明；2026-08-23 决策码）──


def test_code_exec_command_sandbox_escalation() -> None:
    """python/py/node 等代码执行类命令 → ESCALATION 档（独立 action）。"""
    plugin = _make_plugin()
    for cmd in ("python -c 'print(1)'", "py -c 'print(1)'", "node -e 'x'",
                "pip install requests", "npm install"):
        result = plugin._on_tool_call("shell", {"command": cmd})
        assert result.action == ToolAction.ESCALATION, f"{cmd} 应判 ESCALATION"
        assert result.code == SEC_APPROVAL_ESCALATION, f"{cmd} → {result.code}"
        assert result.command == cmd


def test_code_exec_prefix_unaffected() -> None:
    """非代码执行命令保持普通审批档（ESCALATION 判据不误伤）。"""
    plugin = _make_plugin()
    result = plugin._on_tool_call("shell", {"command": "rm /tmp/x"})
    assert result.action == ToolAction.NEED_APPROVAL  # 普通档（非 ESCALATION）
    assert result.code != SEC_APPROVAL_ESCALATION


def test_code_exec_whitelist_unchanged() -> None:
    """只读白名单命令（pwd 等）不受影响。"""
    plugin = _make_plugin()
    assert plugin._on_tool_call("shell", {"command": "pwd"}) is None
