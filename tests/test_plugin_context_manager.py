"""context_manager 插件测试（阶段 B 插件化 + L2 有界性）。

2026-08-22 用户架构修正：上下文管理统一入口（context_manager 编排器）
——不侵入 agent.py，监听 agent/pre-step（waterfall）改写历史。
算法在 context/ 模块（window/sticky），插件按 config 选策略。

本测试验证：
- sticky 挂载（幂等，clear 后自动补挂）
- 滑动裁剪（超预算裁最旧）
- L1 协议（裁剪后无连续同 role）
- L2 有界性（mock 50 轮 token 封顶 + 裁剪触发）
"""

import pytest

from qi_agent.agent import Agent
from qi_agent.context.estimator import estimate_tokens
from qi_agent.context.sticky import remember, reset
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult
from qi_agent.plugins.context_manager import ContextManagerPlugin


@pytest.fixture(autouse=True)
def _clean_sticky():
    yield
    reset()


def _make_agent(budget: int | None = 100_000) -> Agent:
    """装配 context_manager 插件的 agent（真实形态：Agent + 插件 install）。"""
    agent = Agent(client=_FakeClient(), events=EventBus())
    plugin = ContextManagerPlugin(config={"budget": budget})
    plugin.install(agent.events)
    return agent


class _FakeClient:
    def chat(self, messages, tools=None) -> ChatResult:
        return ChatResult(
            content="好的",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "好的"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    def chat_stream(self, messages, tools=None, on_delta=None) -> ChatResult:
        return self.chat(messages, tools)


# ── sticky 挂载（插件幂等） ──────────────────────────────────────────────


def test_sticky_mounted_by_plugin() -> None:
    """插件 pre-step 把 sticky 挂进 system（agent 零侵入）。"""
    remember("记住这个事实")
    agent = _make_agent()
    agent.chat("你好")
    system = agent.messages[0]["content"]
    assert "记住这个事实" in system  # 插件挂载生效


def test_sticky_survives_clear() -> None:
    """clear 重建 system 后，插件自动补挂 sticky（幂等）。"""
    remember("关键信息A")
    agent = _make_agent()
    agent.chat("你好")
    agent.clear_context()
    assert "关键信息A" not in agent.messages[0]["content"]  # 重建后没有
    agent.chat("再来")
    assert "关键信息A" in agent.messages[0]["content"]  # 插件补挂


def test_sticky_never_trimmed_by_plugin() -> None:
    """sticky 永不裁（在 system 里，裁剪只动历史）。"""
    remember("关键信息A")
    agent = _make_agent(budget=1)
    agent.messages.append({"role": "user", "content": "历史" * 500})
    agent.messages.append({"role": "assistant", "content": "回复" * 500})
    result = agent.events.waterfall("agent/pre-step", agent.messages)
    assert "关键信息A" in result[0]["content"]  # sticky 在 system，没被裁
    # 大历史被裁：user 的长文本内容消失（被 anchor 或裁剪替代）
    assert "历史" * 100 not in str(result)


# ── L1 协议（裁剪后无连续同 role） ───────────────────────────────────────


def test_l1_role_alternation_via_plugin() -> None:
    """裁剪后消息序列协议合法（无连续同 role）。"""
    agent = _make_agent(budget=2000)
    for i in range(50):
        agent.chat(f"第{i}轮" + "很长" * 100)
    roles = [m["role"] for m in agent.messages if m["role"] != "system"]
    for i in range(1, len(roles)):
        assert roles[i] != roles[i - 1], f"连续同 role: {roles}"


# ── L2 有界性 ────────────────────────────────────────────────────────────


def test_l2_history_bounded_50_rounds() -> None:
    """50 轮历史 token 有界（预算 2000，不随轮数线性增长）。"""
    agent = _make_agent(budget=2000)
    for i in range(50):
        agent.chat(f"第{i}轮问题" + "内容" * 30)
    history_tokens = sum(
        estimate_tokens(str(m.get("content", "") or ""))
        for m in agent.messages if m.get("role") != "system"
    )
    assert history_tokens <= 2500  # 预算 2000 + 最新组余量


def test_l2_trim_triggered() -> None:
    """裁剪确实触发（早期消息被裁，最新保留）。"""
    agent = _make_agent(budget=2000)
    for i in range(50):
        agent.chat(f"第{i}轮" + "很长" * 100)
    all_text = str(agent.messages)
    assert "第0轮" not in all_text
    assert "第49轮" in all_text


def test_budget_none_disables_trim() -> None:
    """budget=None → 禁用裁剪（历史无限增长）。"""
    agent = _make_agent(budget=None)
    for i in range(5):
        agent.chat(f"第{i}轮" + "很长" * 100)
    assert "第0轮" in str(agent.messages)  # 未裁剪
