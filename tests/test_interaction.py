"""交互抽象层测试：InteractionProvider 协议 + TerminalInteraction + 注册机制。

设计（方案 2026-08-22-工具三件套）：工具与交互形态分离——clarify 等工具
调用 provider.ask()，CLI 启动时注入 TerminalInteraction；未来 Web/GUI 换
实现工具零改动。未注册/非 tty → fail-safe（InteractionUnavailableError）。
"""

import pytest

from qi_agent.interaction import (
    InteractionUnavailableError,
    TerminalInteraction,
    ask_user,
    get_interaction_provider,
    set_interaction_provider,
)


@pytest.fixture(autouse=True)
def _clean_provider():
    """每个测试后清除 provider（避免测试间污染）。"""
    yield
    set_interaction_provider(None)


def test_provider_not_registered_raises() -> None:
    """未注册 provider → ask_user 抛交互不可用（fail-safe）。"""
    with pytest.raises(InteractionUnavailableError):
        ask_user("问题")


def test_terminal_choices(monkeypatch) -> None:
    """选项选择：输入编号返回对应选项。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "2")
    provider = TerminalInteraction()
    assert provider.ask("选哪个", choices=["A", "B", "C"]) == "B"


def test_terminal_choice_other_then_free_text(monkeypatch) -> None:
    """选项 0（其他）→ 转入自由文本输入。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["0", "自定义答案"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    provider = TerminalInteraction()
    assert provider.ask("选哪个", choices=["A", "B"]) == "自定义答案"


def test_terminal_open_question(monkeypatch) -> None:
    """开放式提问：直接返回输入文本。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "我的回答")
    provider = TerminalInteraction()
    assert provider.ask("你怎么想？") == "我的回答"


def test_terminal_not_tty_raises(monkeypatch) -> None:
    """非 tty（评测/管道）→ 交互不可用（fail-safe 不挂死）。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    provider = TerminalInteraction()
    with pytest.raises(InteractionUnavailableError):
        provider.ask("问题")


def test_terminal_invalid_choice_retries(monkeypatch) -> None:
    """非法选项编号 → 提示重试（不崩溃），最终接受合法输入。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["9", "1"])  # 9 超出范围 → 重试 → 1
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    provider = TerminalInteraction()
    assert provider.ask("选哪个", choices=["A", "B"]) == "A"


def test_set_and_get_provider() -> None:
    """注册/获取 provider（CLI 启动注入机制）。"""
    provider = TerminalInteraction()
    set_interaction_provider(provider)
    assert get_interaction_provider() is provider
    set_interaction_provider(None)
    assert get_interaction_provider() is None


def test_ask_user_uses_registered_provider(monkeypatch) -> None:
    """ask_user 委托给注册的 provider（工具入口）。"""
    class FakeProvider:
        def ask(self, question, choices=None, timeout=60.0):
            return f"已答:{question}"

    set_interaction_provider(FakeProvider())
    assert ask_user("今天吃什么") == "已答:今天吃什么"
