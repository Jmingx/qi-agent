"""交互抽象层测试：InteractionProvider 协议 + 选项对象 + TerminalInteraction。

设计（方案 2026-09-13-Web权限审批弹框方案 §3.2）：
- 选项从 `list[str]` 升级为 `InteractionOption(value, label, tone)`——
  跨外壳（Web 弹框 / 终端）传递的是**机器语义**（once/session/deny），
  展示文案由 label 承载，前端不再硬编码映射。
- 兼容：传 `list[str]` 自动包装成 `InteractionOption(value=s, label=s)`。
- `meta` 承载结构化上下文（session/tool/code/command），供 Web 弹框渲染，
  终端实现忽略。
- fail-safe 不变：未注册 provider / 非 tty → InteractionUnavailableError。
"""

import pytest

from qi_agent.interaction import (
    InteractionOption,
    InteractionUnavailableError,
    TerminalInteraction,
    ask_user,
    get_interaction_provider,
    normalize_options,
    set_interaction_provider,
)


class FakeProvider:
    """可编程 provider：记录 question/options/meta，按序列返回答案。"""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = list(answers or [])
        self.questions: list[str] = []
        self.options_list: list[list[InteractionOption] | None] = []
        self.metas: list[dict | None] = []

    def ask(self, question: str, options=None, timeout: float | None = 60.0,
            *, meta: dict | None = None) -> str:
        self.questions.append(question)
        self.options_list.append(options)
        self.metas.append(meta)
        if not self.answers:
            raise InteractionUnavailableError("回答耗尽（测试断言不应弹窗）")
        return self.answers.pop(0)


@pytest.fixture(autouse=True)
def _clean_provider():
    """每个测试后清除 provider（避免测试间污染）。"""
    yield
    set_interaction_provider(None)


# ── 选项对象与兼容包装 ────────────────────────────────────────────────────


def test_option_normalizes_strings() -> None:
    """`list[str]` 兼容包装：value=label=原字符串。"""
    options = normalize_options(["A", "B"])
    assert [o.value for o in options] == ["A", "B"]
    assert [o.label for o in options] == ["A", "B"]
    assert all(o.tone == "" for o in options)


def test_option_keeps_objects() -> None:
    """已构造的 InteractionOption 原样保留（语义 + 文案 + 渲染提示）。"""
    option = InteractionOption(value="once", label="允许一次", tone="primary")
    assert normalize_options([option]) == [option]


def test_option_label_falls_back_to_value() -> None:
    """未填 label → 回退 value（避免前端渲染空按钮）。"""
    assert InteractionOption(value="deny").label == "deny"


def test_option_none_is_empty() -> None:
    """None → 空列表（开放式提问）。"""
    assert normalize_options(None) == []


# ── 注册机制与 fail-safe ──────────────────────────────────────────────────


def test_provider_not_registered_raises() -> None:
    """未注册 provider → ask_user 抛交互不可用（fail-safe）。"""
    with pytest.raises(InteractionUnavailableError):
        ask_user("问题")


def test_set_and_get_provider() -> None:
    """注册/获取 provider（外壳启动注入机制）。"""
    provider = TerminalInteraction()
    set_interaction_provider(provider)
    assert get_interaction_provider() is provider
    set_interaction_provider(None)
    assert get_interaction_provider() is None


def test_ask_user_passes_options_and_meta() -> None:
    """ask_user 把选项对象与 meta 原样交给 provider（选项 + 结构化上下文）。"""
    provider = FakeProvider(["once"])
    set_interaction_provider(provider)
    options = [InteractionOption(value="once", label="允许一次", tone="primary"),
               InteractionOption(value="deny", label="拒绝", tone="danger")]
    answer = ask_user("执行命令？", options, meta={"session": "ctx_1", "tool": "shell"})
    assert answer == "once"
    assert provider.options_list[-1] == options
    assert provider.metas[-1] == {"session": "ctx_1", "tool": "shell"}
    assert provider.questions[-1] == "执行命令？"


def test_ask_user_accepts_plain_strings() -> None:
    """旧调用方传字符串列表仍然可用（clarify 等零改动）。"""
    provider = FakeProvider(["B"])
    set_interaction_provider(provider)
    assert ask_user("选哪个", ["A", "B"]) == "B"
    assert [o.value for o in provider.options_list[-1]] == ["A", "B"]


# ── TerminalInteraction：选项编号 / 语义别名 / fail-safe ──────────────────


def test_terminal_choice_returns_value(monkeypatch) -> None:
    """编号选择返回选项的 **value**（不是 label、不是编号）。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "2")
    provider = TerminalInteraction()
    options = [InteractionOption(value="once", label="允许一次"),
               InteractionOption(value="session", label="本会话总是允许")]
    assert provider.ask("执行？", options) == "session"


def test_terminal_shows_labels(monkeypatch, capsys) -> None:
    """终端展示 label（人话），不展示机器语义值。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    provider = TerminalInteraction()
    provider.ask("执行？", [InteractionOption(value="once", label="允许一次")])
    out = capsys.readouterr().out
    assert "允许一次" in out
    assert "once" not in out


def test_terminal_alias_y_means_once(monkeypatch) -> None:
    """终端别名：y → once（CLI 老习惯保留，映射集中在本实现）。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    provider = TerminalInteraction()
    options = [InteractionOption(value="once", label="允许一次"),
               InteractionOption(value="deny", label="拒绝")]
    assert provider.ask("执行？", options) == "once"


def test_terminal_alias_a_means_session(monkeypatch) -> None:
    """终端别名：a → session（仅当该选项存在时）。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "a")
    provider = TerminalInteraction()
    options = [InteractionOption(value="once", label="允许一次"),
               InteractionOption(value="session", label="本会话总是允许"),
               InteractionOption(value="deny", label="拒绝")]
    assert provider.ask("执行？", options) == "session"


def test_terminal_alias_a_ignored_without_session(monkeypatch) -> None:
    """沙箱档没有 session 选项：输入 a 不被接受（重试后走 2=拒绝）。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["a", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    provider = TerminalInteraction()
    options = [InteractionOption(value="once", label="允许一次"),
               InteractionOption(value="deny", label="拒绝")]
    assert provider.ask("执行？", options) == "deny"


def test_terminal_choice_other_then_free_text(monkeypatch) -> None:
    """选项 0（其他）→ 转入自由文本输入（开放问答）。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["0", "自定义答案"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    provider = TerminalInteraction()
    assert provider.ask("选哪个", ["A", "B"]) == "自定义答案"


def test_terminal_open_question(monkeypatch) -> None:
    """开放式提问（无选项）：直接返回输入文本。"""
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
    answers = iter(["9", "1"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    provider = TerminalInteraction()
    assert provider.ask("选哪个", ["A", "B"]) == "A"


def test_terminal_meta_ignored(monkeypatch) -> None:
    """meta 是给图形外壳的渲染信息，终端实现忽略它（不影响回答）。"""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    provider = TerminalInteraction()
    answer = provider.ask("执行？", [InteractionOption(value="once", label="允许")],
                          meta={"session": "ctx_1", "code": "SEC_APPROVAL_GENERAL"})
    assert answer == "once"
