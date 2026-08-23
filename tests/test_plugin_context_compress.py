"""context_manager 插件压缩链路测试（策略链版，方案 2026-08-23）。

策略链 stateless 语义：无 pending 标记——post-llm 采集真实 usage，
每次 pre-step 由 compress 策略 should_apply（真实 usage 超阈值）判断。
"""

from qi_agent.agent import Agent
from qi_agent.events import EventBus
from qi_agent.llm import ChatResult
from qi_agent.plugins.builtin.context_manager import ContextManagerPlugin


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
    """压缩链插件（window=1000, threshold=0.5 → 500 触发）。"""
    compress_cfg = {
        "window": 1000, "threshold": 0.5, "keep_recent": 2, **overrides,
    }
    return ContextManagerPlugin(
        config={"chain": ["sticky", "compress"], "compress": compress_cfg},
        summarizer=summarizer or (lambda msgs: "摘要：早期对话"),
    )


def test_no_usage_no_trigger() -> None:
    """无 usage（异常场景）→ 不采集（_last_prompt_tokens 保持 0）。"""
    plugin = _make_plugin()
    plugin._on_post_llm(ChatResult(content="x", tool_calls=None,
                                   assistant_message={}, usage=None))
    assert plugin._last_prompt_tokens == 0


def test_real_usage_collected() -> None:
    """真实 usage 采集：post-llm 直接存 response 值（不估算）。"""
    plugin = _make_plugin()
    plugin._on_post_llm(_result(600))
    assert plugin._last_prompt_tokens == 600


def test_under_threshold_no_trigger() -> None:
    """真实 usage 未超阈值 → pre-step 不压缩。"""
    plugin = _make_plugin()
    plugin._on_post_llm(_result(400))
    result = plugin._on_pre_step(_messages(6))
    assert "摘要" not in result[1]["content"] if len(result) > 1 else True


def test_pre_step_executes_compression(capsys) -> None:
    """超阈值 usage → pre-step 压缩（早期 → 摘要，最近保留）。"""
    plugin = _make_plugin()
    plugin._on_post_llm(_result(600))
    result = plugin._on_pre_step(_messages(6))
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
    plugin._on_post_llm(_result(600))
    result = plugin._on_pre_step(_messages(6))
    roles = [m["role"] for m in result]
    assert roles[0] == "system"
    assert roles[1] == "user"  # 摘要块
    for i in range(2, len(roles)):
        assert roles[i] != roles[i - 1]


def test_compression_skipped_when_no_early() -> None:
    """无可压缩早期历史（keep_recent 覆盖全部）→ 不压缩。"""
    plugin = _make_plugin(keep_recent=10)
    plugin._on_post_llm(_result(600))
    messages = _messages(4)
    result = plugin._on_pre_step(messages)
    assert result == messages  # 不动


def test_agent_integration_real_usage(capsys) -> None:
    """agent 集成：第一轮高 usage → 第二轮 pre-step 压缩（stateless）。"""

    class UsageClient:
        def __init__(self):
            self.round = 0

        def chat(self, messages, tools=None) -> ChatResult:
            self.round += 1
            tokens = 600 if self.round == 1 else 100
            return _result(tokens)

    agent = Agent(client=UsageClient(), events=EventBus())
    plugin = _make_plugin()
    plugin.install(agent.events)

    agent.chat("第一轮")  # post-llm 采集 600（超阈值）
    assert plugin._last_prompt_tokens == 600
    agent.chat("第二轮")  # pre-step 压缩（600 仍是最新 usage）
    out = capsys.readouterr().out
    assert "已压缩" in out
    # 第三轮 usage=100 → 不压缩
    agent.chat("第三轮")
    assert plugin._last_prompt_tokens == 100
