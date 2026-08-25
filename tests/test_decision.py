"""工具决策码测试（方案 2026-08-23）：枚举/错误码/WARN 执行路径。

验证：
- ToolAction 枚举值 + 可扩展性
- ToolDecision 构造与字段
- 错误码常量语义
- agent 集成：WARN 档 = 执行 + 结果附警告后缀；ESCALATION 走审批
"""

from qi_agent.agents.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.tools.decision import (
    SEC_APPROVAL_ESCALATION,
    SEC_APPROVAL_GENERAL,
    SEC_APPROVAL_SANDBOX,
    SEC_BLOCK_BLACKLIST,
    SEC_BLOCK_REDLINE,
    SEC_BLOCK_SENSITIVE,
    ToolAction,
    ToolDecision,
)


# ── 枚举与错误码 ─────────────────────────────────────────────────────────


def test_tool_action_values() -> None:
    """枚举值语义（字符串值——日志/序列化友好）。"""
    assert ToolAction.ALLOW.value == "allow"
    assert ToolAction.BLOCK.value == "block"
    assert ToolAction.NEED_APPROVAL.value == "need_approval"
    assert ToolAction.WARN.value == "warn"
    assert ToolAction.ESCALATION.value == "escalation"


def test_decision_construct() -> None:
    """ToolDecision 构造 + 字段默认值。"""
    d = ToolDecision(ToolAction.BLOCK, reason="黑名单命中", code=SEC_BLOCK_BLACKLIST)
    assert d.action == ToolAction.BLOCK
    assert d.reason == "黑名单命中"
    assert d.code == SEC_BLOCK_BLACKLIST
    assert d.command == ""  # 默认空
    assert d.extra == {}  # 默认空


def test_error_codes_distinct() -> None:
    """错误码互不重复（语义唯一）。"""
    codes = [
        SEC_BLOCK_BLACKLIST, SEC_BLOCK_REDLINE, SEC_BLOCK_SENSITIVE,
        SEC_APPROVAL_GENERAL, SEC_APPROVAL_SANDBOX, SEC_APPROVAL_ESCALATION,
    ]
    assert len(codes) == len(set(codes))
    # 语义分组：BLOCK 系 vs APPROVAL 系
    assert all(c.startswith("SEC_BLOCK") for c in codes[:3])
    assert all(c.startswith("SEC_APPROVAL") for c in codes[3:])


# ── agent 集成：WARN 档执行路径 ──────────────────────────────────────────


class _WarnClient:
    """第一轮触发 WARN 决策（模拟判档插件返回 WARN），之后返回文本。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                content=None,
                tool_calls=[ToolCall(id="c1", name="get_time", arguments={})],
                assistant_message={"role": "assistant", "content": None,
                                   "tool_calls": []},
            )
        return ChatResult(content="完成", tool_calls=None,
                          assistant_message={"role": "assistant", "content": "完成"})


def test_warn_action_executes_with_suffix() -> None:
    """WARN 档：工具执行 + 结果回填附警告后缀。"""
    from qi_agent.plugins.builtin.security_guard import SecurityGuardPlugin

    class WarnPlugin(SecurityGuardPlugin):
        def _on_tool_call(self, name, arguments, **_):
            if name == "get_time":
                return ToolDecision(ToolAction.WARN, reason="测试警告",
                                    code="SEC_WARN_EXEC")
            return None

    agent = Agent(_WarnClient(), events=EventBus())
    WarnPlugin().install(agent.events)
    reply = agent.chat("测一下")
    assert reply == "完成"
    tool_msg = next(m for m in agent.history if m["role"] == "tool")
    assert "[警告] 测试警告" in tool_msg["content"]  # 警告后缀
    assert "2026" in tool_msg["content"]  # 工具真实执行（get_time 结果）


def test_escalation_requires_approval() -> None:
    """ESCALATION 档：走审批（无审批插件 → fail-closed 拒绝）。"""
    from qi_agent.plugins.builtin.security_guard import SecurityGuardPlugin

    class EscClient:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                content=None,
                tool_calls=[ToolCall(id="c1", name="shell",
                                     arguments={"command": "python -c x"})],
                assistant_message={"role": "assistant", "content": None,
                                   "tool_calls": []},
            )

    agent = Agent(EscClient(), events=EventBus())
    SecurityGuardPlugin().install(agent.events)  # 判档：python → ESCALATION
    # 不装 approval_gate（模拟评测 fail-closed）——ESCALATION 无监听器 → 拒绝
    agent.chat("执行 python")
    tool_msg = next(m for m in agent.history if m["role"] == "tool")
    assert "[审批拒绝]" in tool_msg["content"]
