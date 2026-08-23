"""策略链测试（方案 2026-08-23）：每策略 should_apply/apply + 责任链语义。

验证：
- 注册表（注册/查名/重复注册拒绝）
- 各策略：should_apply 条件 + apply 改写
- 责任链：顺序执行 + 消费即停 + 未消费继续
"""

import pytest

import qi_agent.context.strategies  # noqa: F401  导入即注册
from qi_agent.context.sticky import remember, reset
from qi_agent.context.strategies import (
    build_chain,
    get_strategy_names,
    register_strategy,
)
from qi_agent.context.strategies.base import ContextInfo, ContextStrategy


@pytest.fixture(autouse=True)
def _clean_sticky():
    yield
    reset()


# ── 注册表 ───────────────────────────────────────────────────────────────


def test_strategies_registered() -> None:
    """三个内置策略已注册。"""
    names = get_strategy_names()
    assert "sticky" in names
    assert "compress" in names
    assert "window" in names


def test_duplicate_register_rejected() -> None:
    """重复注册同名策略 → 拒绝。"""
    with pytest.raises(ValueError):

        @register_strategy
        class Duplicate(ContextStrategy):
            name = "sticky"

            def should_apply(self, ctx):
                return False

            def apply(self, messages, ctx):
                return messages, False


def test_build_chain_unknown_strategy() -> None:
    """未注册策略名 → 报错（可用列表提示）。"""
    with pytest.raises(ValueError, match="未注册"):
        build_chain(chain=["ghost"])


# ── sticky 策略 ──────────────────────────────────────────────────────────


def test_sticky_mounts_and_not_consumes() -> None:
    """sticky 挂载 + 不消费（后续策略继续）。"""
    remember("关键信息A")
    from qi_agent.context.strategies.sticky import StickyStrategy

    s = StickyStrategy()
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"}]
    result, consumed = s.apply(messages, ContextInfo())
    assert consumed is False  # 不消费
    assert "关键信息A" in result[0]["content"]


def test_sticky_idempotent() -> None:
    """幂等：已挂载不重复。"""
    remember("A")
    from qi_agent.context.strategies.sticky import StickyStrategy

    s = StickyStrategy()
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"}]
    once, _ = s.apply(messages, ContextInfo())
    twice, _ = s.apply(once, ContextInfo())
    assert once[0]["content"] == twice[0]["content"]  # 不重复挂


# ── compress 策略 ────────────────────────────────────────────────────────


def test_compress_should_apply_real_usage() -> None:
    """真实 usage 超阈值 → should_apply；无 summarizer → 不触发（fail-safe）。"""
    from qi_agent.context.strategies.compress import CompressStrategy

    s = CompressStrategy({"window": 1000, "threshold": 0.5})
    assert s.should_apply(ContextInfo(prompt_tokens=600)) is False  # 无 summarizer
    assert s.should_apply(ContextInfo(prompt_tokens=600, summarizer=lambda m: "s")) is True
    assert s.should_apply(ContextInfo(prompt_tokens=400, summarizer=lambda m: "s")) is False


def test_compress_applies_and_consumes() -> None:
    """压缩执行 + 消费（责任链停止）。"""
    from qi_agent.context.strategies.compress import CompressStrategy

    s = CompressStrategy({"window": 1000, "threshold": 0.5, "keep_recent": 2})
    messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"q{i}"} if i % 2
        else {"role": "assistant", "content": f"a{i}"}
        for i in range(1, 7)
    ]
    result, consumed = s.apply(
        messages, ContextInfo(summarizer=lambda m: "摘要内容")
    )
    assert consumed is True
    assert "摘要内容" in result[1]["content"]  # 摘要块
    # L1：system + user 摘要 + 交替
    roles = [m["role"] for m in result]
    assert roles[0] == "system" and roles[1] == "user"


# ── window 策略 ──────────────────────────────────────────────────────────


def test_window_budget_none_disabled() -> None:
    """budget=None → should_apply False（禁用裁剪）。"""
    from qi_agent.context.strategies.window import WindowStrategy

    s = WindowStrategy({"budget": None})
    assert s.should_apply(ContextInfo()) is False


def test_window_trims_and_consumes() -> None:
    """超预算裁剪 + 消费；未超预算不消费。"""
    from qi_agent.context.strategies.window import WindowStrategy

    s = WindowStrategy({"budget": 10})
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "很长" * 100},
                {"role": "assistant", "content": "回" * 100}]
    result, consumed = s.apply(messages, ContextInfo())
    assert consumed is True
    assert "很长" * 100 not in str(result)  # 大文本被裁（anchor 替代）

    # 未超预算
    s2 = WindowStrategy({"budget": 100_000})
    result2, consumed2 = s2.apply(messages, ContextInfo())
    assert consumed2 is False
    assert result2 == messages


# ── 责任链语义 ───────────────────────────────────────────────────────────


def test_chain_order_and_consume() -> None:
    """链顺序执行：compress 消费后 window 不再执行。"""
    chain = build_chain(
        chain=["sticky", "compress", "window"],
        config={"compress": {"window": 1000, "threshold": 0.5, "keep_recent": 2}},
    )
    assert [s.name for s in chain] == ["sticky", "compress", "window"]

    messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"q{i}"} if i % 2
        else {"role": "assistant", "content": f"a{i}"}
        for i in range(1, 7)
    ]
    # 有 summarizer + 超阈值 → compress 消费（window 不执行）
    ctx = ContextInfo(prompt_tokens=600, summarizer=lambda m: "摘要")
    result = messages
    for s in chain:
        if s.should_apply(ctx):
            result, consumed = s.apply(result, ctx)
            if consumed:
                break
    assert "摘要" in result[1]["content"]  # 压缩生效（window 未执行——消息数大幅减少）


def test_chain_window_fallback() -> None:
    """compress 不触发（usage 低）→ window 兜底执行。"""
    chain = build_chain(
        chain=["sticky", "compress", "window"],
        config={"window": {"budget": 10}},
    )
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "很长" * 100},
                {"role": "assistant", "content": "回" * 100}]
    ctx = ContextInfo(prompt_tokens=100, summarizer=lambda m: "摘要")
    result = messages
    for s in chain:
        if s.should_apply(ctx):
            result, consumed = s.apply(result, ctx)
            if consumed:
                break
    assert "很长" * 100 not in str(result)  # window 裁了（compress 未触发）
