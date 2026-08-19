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
