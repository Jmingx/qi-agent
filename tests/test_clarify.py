"""clarify 澄清提问工具测试：向用户提问（交互层委托 + fail-safe）。

设计（方案 2026-08-22-工具三件套）：clarify 只做 schema + 分发——
handler 调用 interaction.ask_user()，交互形态由注册的 provider 决定
（CLI 终端/未来 Web UI）；未注册/非 tty → fail-safe 错误不挂死。
"""

from qi_agent.interaction import (
    InteractionUnavailableError,
    set_interaction_provider,
)
from qi_agent.tools.builtin.clarify import clarify
from qi_agent.tools.registry import execute_tool, get_tool

import pytest


@pytest.fixture(autouse=True)
def _clean_provider():
    """每个测试后清除 provider（防测试间污染：注册了不清理会泄漏到
    后续 fail-safe 测试——ask_user 会拿到上一个测试的 FakeProvider）。"""
    yield
    set_interaction_provider(None)


def test_clarify_open_question(monkeypatch) -> None:
    """开放式提问：返回用户回答。"""
    class FakeProvider:
        def ask(self, question, choices=None, timeout=60.0):
            return "我的回答"

    set_interaction_provider(FakeProvider())
    assert clarify("你怎么想？") == "我的回答"


def test_clarify_choices_passed_through(monkeypatch) -> None:
    """选项原样传给 provider（provider 决定渲染方式）。"""
    received = {}

    class FakeProvider:
        def ask(self, question, choices=None, timeout=60.0):
            received["question"] = question
            received["choices"] = choices
            return "B"

    set_interaction_provider(FakeProvider())
    result = clarify("选哪个", choices=["A", "B", "C"])
    assert result == "B"
    assert received["question"] == "选哪个"
    assert received["choices"] == ["A", "B", "C"]


def test_clarify_no_provider_failsafe() -> None:
    """未注册 provider → fail-safe 错误（不抛异常、不挂死）。"""
    result = clarify("你在吗")
    assert "交互不可用" in result


def test_clarify_provider_raises_failsafe(monkeypatch) -> None:
    """provider 抛交互不可用 → 同样 fail-safe（非 tty 场景）。"""
    class BrokenProvider:
        def ask(self, question, choices=None, timeout=60.0):
            raise InteractionUnavailableError("stdin 非终端")

    set_interaction_provider(BrokenProvider())
    result = clarify("你在吗")
    assert "交互不可用" in result


def test_clarify_registered_and_schema() -> None:
    """工具已注册 + schema 参数正确（question 必填、choices 可选数组）。"""
    entry = get_tool("clarify")
    assert entry is not None
    props = entry.schema["function"]["parameters"]["properties"]
    assert "question" in props
    assert "choices" in props
    assert entry.schema["function"]["parameters"]["required"] == ["question"]


def test_clarify_via_execute_tool(monkeypatch) -> None:
    """execute_tool 路径：注册 provider → 回答回填。"""
    class FakeProvider:
        def ask(self, question, choices=None, timeout=60.0):
            return "via execute"

    set_interaction_provider(FakeProvider())
    assert execute_tool("clarify", {"question": "测试?"}) == "via execute"
