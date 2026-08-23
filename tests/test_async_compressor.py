"""异步压缩测试（方案 2026-08-23 二期）：快照隔离 + 新鲜度 + 单任务锁。

并发安全三条防线验证：
① 快照隔离（后台只读副本，agent.messages 单线程写）
② 新鲜度（快照后消息大增长 → 丢弃）
③ 单任务锁（进行中 request 被跳过）
"""

import threading

from qi_agent.agent import Agent
from qi_agent.context.async_compressor import AsyncCompressor
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult
from qi_agent.plugins.builtin.context_manager import ContextManagerPlugin


def _messages(n: int = 6) -> list[dict]:
    return [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"问题{i}"} if i % 2 == 0
        else {"role": "assistant", "content": f"回答{i}"}
        for i in range(1, n + 1)
    ]


# ── AsyncCompressor 单元 ─────────────────────────────────────────────────


def test_request_and_take_if_fresh() -> None:
    """提交 → 后台完成 → take_if_fresh 返回压缩结果。"""
    compressor = AsyncCompressor(
        summarizer=lambda msgs: "摘要：早期对话", keep_recent=2)
    messages = _messages(6)
    assert compressor.request(messages) is True
    compressor.wait_idle()
    result = compressor.take_if_fresh(messages)
    assert result is not None
    assert "摘要：早期对话" in result[1]["content"]
    assert result[0]["role"] == "system"
    # 取走后清空
    assert compressor.take_if_fresh(messages) is None


def test_single_task_lock() -> None:
    """单任务锁：进行中再 request → 跳过（不堆积）。"""
    calls = []
    barrier = threading.Event()

    def slow_summarizer(msgs):
        calls.append(1)
        barrier.wait(timeout=5)  # 卡住第一个任务
        return "摘要"

    compressor = AsyncCompressor(summarizer=slow_summarizer, keep_recent=2)
    messages = _messages(6)
    assert compressor.request(messages) is True
    assert compressor.is_busy() is True
    assert compressor.request(messages) is False  # 进行中 → 跳过
    assert len(calls) == 1  # 只跑一个
    barrier.set()
    compressor.wait_idle()


def test_stale_snapshot_discarded() -> None:
    """新鲜度：快照后消息大增长 → take_if_fresh 丢弃（返回 None）。"""
    compressor = AsyncCompressor(summarizer=lambda m: "摘要", keep_recent=2)
    messages = _messages(6)
    compressor.request(messages)
    compressor.wait_idle()
    # 当前消息大增长（远超 3 组）→ 快照过期
    grown = _messages(30)
    assert compressor.take_if_fresh(grown) is None


# ── 插件集成（异步链路） ─────────────────────────────────────────────────


def _result(prompt_tokens: int) -> ChatResult:
    return ChatResult(
        content="好的", tool_calls=None,
        assistant_message={"role": "assistant", "content": "好的"},
        usage={"prompt_tokens": prompt_tokens, "completion_tokens": 10,
               "total_tokens": prompt_tokens + 10},
    )


class _UsageClient:
    """第一轮高 usage（触发异步），后续低。"""

    def __init__(self):
        self.round = 0

    def chat(self, messages, tools=None) -> ChatResult:
        self.round += 1
        return _result(600 if self.round == 1 else 100)


def test_plugin_async_trigger_and_switch(capsys) -> None:
    """post-llm 超阈值 → 后台压缩 → 下轮 pre-step 快照切换（无重复压缩）。"""
    agent = Agent(client=_UsageClient(), events=EventBus())
    plugin = ContextManagerPlugin(
        config={"chain": ["sticky", "compress"],
                "compress": {"window": 1000, "threshold": 0.5,
                             "keep_recent": 2},
                "async_compress": True},
        summarizer=lambda msgs: "异步摘要",
    )
    plugin.install(agent.events)

    # 第一轮：6 条历史 + 高 usage → 触发异步
    agent.messages = _messages(6)
    agent.chat("第一轮")
    out = capsys.readouterr().out
    assert "后台异步压缩" in out

    # 等后台完成 → 第二轮 pre-step 切换
    plugin._async_compressor.wait_idle()
    agent.chat("第二轮")
    out = capsys.readouterr().out
    assert "异步压缩完成" in out
    # 切换后无重复压缩（同步压缩日志"已压缩："不应出现）
    assert "已压缩：" not in out
    # 压缩后摘要块在 system 之后
    assert "异步摘要" in agent.messages[1]["content"]


def test_plugin_async_inflight_skips_compress(capsys) -> None:
    """任务进行中 → 跳过 compress 策略（防双重压缩）。"""
    barrier = threading.Event()

    def slow_summarizer(msgs):
        barrier.wait(timeout=5)
        return "摘要"

    agent = Agent(client=_UsageClient(), events=EventBus())
    plugin = ContextManagerPlugin(
        config={"chain": ["sticky", "compress", "window"],
                "compress": {"window": 1000, "threshold": 0.5,
                             "keep_recent": 2},
                "window": {"budget": None},
                "async_compress": True},
        summarizer=slow_summarizer,
    )
    plugin.install(agent.events)
    agent.messages = _messages(6)
    agent.chat("第一轮")  # 触发异步（后台卡住）
    capsys.readouterr()  # 清空第一轮输出
    # 下一轮 pre-step：任务进行中 → compress 被跳过（无"已压缩"输出）
    plugin._on_pre_step(agent.messages)
    out2 = capsys.readouterr().out
    assert "已压缩" not in out2  # 未同步压缩（防双重）
    barrier.set()
    plugin._async_compressor.wait_idle()


def test_no_summarizer_injection_still_works(monkeypatch) -> None:
    """回归（2026-08-23 修复）：真实装配路径（load_plugins 只传 config，
    不注入 summarizer）→ 默认惰性实现兜底 → 压缩仍触发。"""
    from qi_agent.plugins.builtin.context_manager import ContextManagerPlugin as CMP

    # patch 插件模块里的名字绑定（from-import 后 monkeypatch 模块属性无效）
    # C3 后默认实现是 make_summarizer 工厂（None=复用主模型）——patch 工厂
    monkeypatch.setattr(
        "qi_agent.plugins.builtin.context_manager.make_summarizer",
        lambda model=None: lambda msgs: "默认兜底摘要",
    )
    # 关键：不传 summarizer（模拟 load_plugins 装配）
    plugin = CMP(config={"chain": ["sticky", "compress"],
                         "compress": {"window": 1000, "threshold": 0.5,
                                      "keep_recent": 2}})
    assert plugin._summarizer is not None  # 默认实现已接线（修复点）
    assert plugin._async_compressor is not None  # 异步也启用（默认实现可用）
    plugin._last_prompt_tokens = 600
    result = plugin._on_pre_step(_messages(6))
    assert "默认兜底摘要" in result[1]["content"]  # 压缩触发 ✓


def test_async_disabled_falls_back_sync() -> None:
    """async_compress=False → 无异步，走同步策略链。"""
    plugin = ContextManagerPlugin(
        config={"chain": ["sticky", "compress"],
                "compress": {"window": 1000, "threshold": 0.5,
                             "keep_recent": 2},
                "async_compress": False},
        summarizer=lambda msgs: "同步摘要",
    )
    assert plugin._async_compressor is None
    # 超阈值 → pre-step 同步压缩
    plugin._last_prompt_tokens = 600
    result = plugin._on_pre_step(_messages(6))
    assert "同步摘要" in result[1]["content"]
