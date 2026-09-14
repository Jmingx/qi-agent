"""Web 交互实现测试（方案 2026-09-13 §3.5）：WebInteraction → 网关审批桥。

职责边界：
- 本实现只做"把提问转成网关审批请求 + 把答案转回决策层"，
  **不承担放行决策**——超时/缺会话/网关异常一律抛
  InteractionUnavailableError，由 approval_gate / clarify 的既有
  fail-closed 语义收口。
- 会话身份由调用方经 `meta["session"]` 显式传入（插件读 bus.context_id），
  provider 自己不猜上下文。
"""

import pytest

from qi_agent.interaction import (
    InteractionOption,
    InteractionUnavailableError,
    normalize_options,
)
from qi_agent.web.interaction import WebInteraction


class FakeGateway:
    """记录调用参数的网关替身。"""

    def __init__(self, choice: str | None = "once", raises: Exception | None = None) -> None:
        self._choice = choice
        self._raises = raises
        self.calls: list[dict] = []

    def request_approval(self, session_id, question, options=None, *,
                         meta=None, timeout=None):
        self.calls.append({"session_id": session_id, "question": question,
                           "options": options, "meta": meta, "timeout": timeout})
        if self._raises is not None:
            raise self._raises
        return self._choice


OPTIONS = [
    InteractionOption(value="once", label="允许一次", tone="primary"),
    InteractionOption(value="deny", label="拒绝", tone="danger"),
]


def test_ask_routes_to_gateway_with_session() -> None:
    """问题 + 选项 + 会话身份 → 网关审批请求；返回选项值。"""
    gateway = FakeGateway(choice="once")
    provider = WebInteraction(gateway)
    answer = provider.ask(
        "执行命令 'git push'？", OPTIONS,
        meta={"session": "ctx_1", "tool": "shell", "code": "SEC_APPROVAL_GENERAL"},
    )
    assert answer == "once"
    call = gateway.calls[-1]
    assert call["session_id"] == "ctx_1"
    assert call["question"] == "执行命令 'git push'？"
    assert call["timeout"] == 60.0  # 默认等待与网关 APPROVAL_TIMEOUT 对齐
    assert [o["value"] for o in call["options"]] == ["once", "deny"]
    assert call["options"][0]["label"] == "允许一次"
    assert call["meta"]["tool"] == "shell"


def test_ask_accepts_plain_string_options() -> None:
    """clarify 传字符串列表也走同一通道（零改动收益）。"""
    gateway = FakeGateway(choice="B")
    provider = WebInteraction(gateway)
    answer = provider.ask("选哪个？", ["A", "B"], meta={"session": "ctx_1"})
    assert answer == "B"
    assert [o["value"] for o in gateway.calls[-1]["options"]] == ["A", "B"]


def test_ask_without_session_raises() -> None:
    """无会话上下文（meta 缺失）→ 交互不可用（fail-closed，不打网关）。"""
    gateway = FakeGateway()
    provider = WebInteraction(gateway)
    with pytest.raises(InteractionUnavailableError):
        provider.ask("执行？", OPTIONS)
    assert gateway.calls == []


def test_ask_timeout_raises() -> None:
    """超时（网关返回 None）→ 交互不可用 → 决策层视为拒绝。"""
    gateway = FakeGateway(choice=None)
    provider = WebInteraction(gateway)
    with pytest.raises(InteractionUnavailableError):
        provider.ask("执行？", OPTIONS, meta={"session": "ctx_1"})


def test_ask_gateway_failure_raises() -> None:
    """网关异常（断连/通知失败）→ 交互不可用（不放行）。"""
    gateway = FakeGateway(raises=RuntimeError("通知失败"))
    provider = WebInteraction(gateway)
    with pytest.raises(RuntimeError):
        provider.ask("执行？", OPTIONS, meta={"session": "ctx_1"})


def test_ask_respects_custom_timeout() -> None:
    """调用方指定超时（如沙箱档逐次确认更短）→ 透传网关。"""
    gateway = FakeGateway(choice="once")
    provider = WebInteraction(gateway)
    provider.ask("执行？", OPTIONS, timeout=5.0, meta={"session": "ctx_1"})
    assert gateway.calls[-1]["timeout"] == 5.0


def test_options_normalization_used() -> None:
    """选项规范化在 provider 内统一执行（None → 空列表）。"""
    assert normalize_options(None) == []
    gateway = FakeGateway(choice="ok")
    provider = WebInteraction(gateway)
    provider.ask("开放提问？", None, meta={"session": "ctx_1"})
    assert gateway.calls[-1]["options"] == []
