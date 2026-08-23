"""context_manager 压缩链路测试（阶段 C）：post-llm 真实 usage → 压缩。

验证（用户要求 2026-08-22）：压缩触发依据 = response 真实 usage
（result.usage.prompt_tokens），不是估算。
- post-llm 收到超阈值 usage → 标记压缩
- 下一轮 pre-step → 执行压缩（摘要替换早期历史）
- 压缩后协议合法（L1）+ 最近消息保留
"""

from qi_agent.events import EventBus
from qi_agent.llm import ChatResult
from qi_agent.plugins.context_manager import ContextManagerPlugin


def _result(prompt_tokens: int) -> ChatResult:
    return ChatResult(
        content="好的",
        tool_calls=None,
        assistant_message={"role": "assistant", "content": "好的"},
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 10,
            "total_tokens": prompt_tokens + 10,
        },
    )


def _messages(n: int = 6) -> list[dict]:
    return [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"问题{i}"} if i % 2 == 0
        else {"role": "assistant", "content": f"回答{i}"}
        for i in range(1, n + 1)
    ]


def _make_plugin(summarizer=None, **overrides) -> ContextManagerPlugin:
    config = {"window": 1000, "threshold": 0.5, "keep_recent": 2, **overrides}
    return ContextManagerPlugin(
        config=config,
        summarizer=summarizer or (lambda msgs: "摘要：早期对话"),
    )


def test_no_usage_no_trigger() -> None:
    """无 usage（异常场景）→ 不触发压缩（fail-safe）。"""
    plugin = _make_plugin()
    plugin._on_post_llm(ChatResult(content="x", tool_calls=None,
                                   assistant_message={}, usage=None))
    assert plugin._compression_pending is False


def test_real_usage_triggers_compression() -> None:
    """真实 usage 超阈值 → 标记压缩（不估算，直接用 response 值）。"""
    plugin = _make_plugin()  # window=1000, threshold=0.5 → 500 触发
    plugin._on_post_llm(_result(600))
    assert plugin._compression_pending is True
    assert plugin._last_prompt_tokens == 600


def test_under_threshold_no_trigger() -> None:
    """真实 usage 未超阈值 → 不压缩。"""
    plugin = _make_plugin()
    plugin._on_post_llm(_result(400))
    assert plugin._compression_pending is False


def test_pre_step_executes_compression(capsys) -> None:
    """标记压缩后 → pre-step 执行（早期历史 → 摘要，最近保留）。"""
    plugin = _make_plugin()
    messages = _messages(6)
    plugin._compression_pending = True
    result = plugin._on_pre_step(messages)
    out = capsys.readouterr().out
    assert "已压缩" in out
    assert result[0]["role"] == "system"
    assert "摘要：早期对话" in result[1]["content"]  # 摘要块
    # 最近 2 组保留（keep_recent=2）
    recent = result[2:]
    assert recent and recent[-1]["content"] == "问题6"


def test_compression_protocol_valid() -> None:
    """压缩后协议合法：system + user 摘要 + 交替。"""
    plugin = _make_plugin()
    messages = _messages(6)
    plugin._compression_pending = True
    result = plugin._on_pre_step(messages)
    roles = [m["role"] for m in result]
    assert roles[0] == "system"
    assert roles[1] == "user"  # 摘要块
    for i in range(2, len(roles)):
        assert roles[i] != roles[i - 1]


def test_compression_skipped_when_no_early() -> None:
    """无可压缩早期历史（keep_recent 覆盖全部）→ 原样返回。"""
    plugin = _make_plugin(keep_recent=10)
    messages = _messages(4)
    plugin._compression_pending = True
    result = plugin._on_pre_step(messages)
    assert result == messages  # 不动


def test_pending_cleared_after_compression() -> None:
    """压缩执行后清除 pending（避免每轮重复压缩）。"""
    plugin = _make_plugin()
    plugin._compression_pending = True
    plugin._on_pre_step(_messages(6))
    assert plugin._compression_pending is False


def test_agent_integration_real_usage(capsys) -> None:
    """agent 集成：post-llm 超阈值 → 下轮 pre-step 压缩。"""

    class UsageClient:
        def __init__(self):
            self.round = 0

        def chat(self, messages, tools=None) -> ChatResult:
            self.round += 1
            # 第一轮返回高 usage（触发压缩标记）→ 第二轮低 usage
            tokens = 600 if self.round == 1 else 100
            return _result(tokens)

    from qi_agent.agent import Agent

    agent = Agent(client=UsageClient(), events=EventBus())
    plugin = _make_plugin()
    plugin.install(agent.events)

    agent.chat("第一轮")
    assert plugin._compression_pending is True  # usage 600 超阈值
    agent.chat("第二轮")
    out = capsys.readouterr().out
    assert "已压缩" in out  # pre-step 执行了压缩
    assert plugin._compression_pending is False
