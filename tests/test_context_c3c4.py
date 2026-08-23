"""阶段 C 收尾测试（方案 2026-08-23）：压缩模型独立配置 + /compact 手动命令。

覆盖：
- C3: make_summarizer 工厂（None=复用主模型；指定 model=独立模型）
- C4: context_manager.compact_now()（手动同步压缩）+ CLI /context /compact
"""

from unittest import mock

from qi_agent.context.compressor import make_summarizer
from qi_agent.plugins.builtin.context_manager import ContextManagerPlugin


# ── C3: 压缩模型独立配置 ────────────────────────────────────────────────


def test_make_summarizer_default_uses_main_model() -> None:
    """make_summarizer(None) = 复用主模型（现状行为）。"""
    s = make_summarizer(None)
    assert callable(s)


def test_make_summarizer_custom_model() -> None:
    """指定 compression_model → LLMClient 用该模型（独立压缩模型）。"""
    created: dict = {}

    class FakeLLMClient:
        def __init__(self, api_key: str, model: str = "deepseek-v4-flash",
                     **kw) -> None:
            created["api_key"] = api_key
            created["model"] = model

        def chat(self, messages, tools=None):
            return type("R", (), {"content": "摘要"})

    with mock.patch("qi_agent.llm.LLMClient", FakeLLMClient):
        s = make_summarizer("deepseek-chat")
        result = s([{"role": "user", "content": "hi"}])
    assert created["model"] == "deepseek-chat"  # 独立模型
    assert result == "摘要"


def test_plugin_reads_compression_model() -> None:
    """插件读 compress.compression_model → summarizer 用独立模型。"""
    created: dict = {}

    class FakeLLMClient:
        def __init__(self, api_key: str, model: str = "deepseek-v4-flash",
                     **kw) -> None:
            created["model"] = model

        def chat(self, messages, tools=None):
            return type("R", (), {"content": "摘要"})

    with mock.patch("qi_agent.llm.LLMClient", FakeLLMClient):
        plugin = ContextManagerPlugin(
            {"compress": {"compression_model": "deepseek-chat"}})
        summary = plugin._summarizer([{"role": "user", "content": "hi"}])
    assert created["model"] == "deepseek-chat"
    assert summary == "摘要"


def test_plugin_default_summarizer_uses_main_model() -> None:
    """未配置 compression_model → 默认（复用主模型）。"""
    with mock.patch("qi_agent.llm.LLMClient") as fake_cls:
        plugin = ContextManagerPlugin()
        plugin._summarizer([{"role": "user", "content": "hi"}])
    # 默认模型（deepseek-v4-flash）
    assert fake_cls.call_args.kwargs.get("model", "deepseek-v4-flash") \
        == "deepseek-v4-flash"


# ── C4: compact_now 手动同步压缩 ─────────────────────────────────────────


def _msgs(n: int) -> list[dict]:
    """构造 n 条消息（system + 交替 user/assistant）。"""
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"msg{i}"})
    return msgs


def test_compact_now_forces_compression() -> None:
    """compact_now：强制压缩（不检查阈值）+ 返回 (新消息, 摘要)。"""
    plugin = ContextManagerPlugin(
        {}, summarizer=lambda msgs: "关键事实：测试摘要")
    messages = _msgs(20)

    new_msgs, summary = plugin.compact_now(messages)

    assert summary == "关键事实：测试摘要"
    assert len(new_msgs) < len(messages)  # 压缩后消息减少
    # L1 协议：system 最前 + 摘要块 user 角色
    assert new_msgs[0]["role"] == "system"
    assert new_msgs[1]["role"] == "user"
    assert "摘要" in new_msgs[1]["content"]


def test_compact_now_no_compress_strategy() -> None:
    """链中无 compress 策略 → 原样返回（空摘要）。"""
    plugin = ContextManagerPlugin(
        {"chain": ["sticky", "window"]},
        summarizer=lambda msgs: "不应被调用")
    messages = _msgs(5)

    new_msgs, summary = plugin.compact_now(messages)

    assert new_msgs == messages  # 无压缩策略 → 不动
    assert summary == ""


def test_compact_now_empty_history() -> None:
    """空历史/仅 system → 安全返回。"""
    plugin = ContextManagerPlugin({}, summarizer=lambda msgs: "摘要")
    messages = [{"role": "system", "content": "sys"}]

    new_msgs, summary = plugin.compact_now(messages)

    assert new_msgs == messages
    assert summary == ""
