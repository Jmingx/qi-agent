"""资源监控测试：usage 数据层（chat/chat_stream 提取）+ resource_monitor 插件。

方案：docs/plans/2026-08-20-资源监控插件方案.md（决策点 1-5 已批准）
"""

from unittest import mock

from qi_agent.llm import ChatResult, LLMClient
from qi_agent.plugins.resource_monitor import ResourceMonitorPlugin


# ── 数据层：usage 提取 ─────────────────────────────────────────────────────


def _fake_response(content="hi", usage=None):
    """构造 openai SDK 风格的响应对象。"""
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.usage = usage
    return resp


def test_chat_result_usage_default() -> None:
    """无 usage 时 ChatResult.usage 为 None（向后兼容）。"""
    r = ChatResult(content="hi", tool_calls=None)
    assert r.usage is None


def test_chat_extracts_usage() -> None:
    """chat() 从响应提取 usage 到 ChatResult。"""
    client = LLMClient(api_key="sk-test")
    resp = _fake_response(usage=mock.MagicMock(
        prompt_tokens=100, completion_tokens=50, total_tokens=150))
    with mock.patch.object(client._client.chat.completions, "create",
                           return_value=resp):
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result.usage == {
        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
    }


def test_chat_no_usage_ok() -> None:
    """响应无 usage → ChatResult.usage 为 None（不崩溃）。"""
    client = LLMClient(api_key="sk-test")
    with mock.patch.object(client._client.chat.completions, "create",
                           return_value=_fake_response(usage=None)):
        result = client.chat([{"role": "user", "content": "hi"}])
    assert result.usage is None


def _make_chunk(content=None, usage=None):
    """构造流式 chunk：普通 chunk（choices 有 delta）或最后 chunk（仅 usage）。"""
    chunk = mock.MagicMock()
    if usage:
        chunk.usage = usage
        chunk.choices = []  # 最后 chunk：choices 为空，纯 usage 携带者
    else:
        chunk.usage = None
        delta = mock.MagicMock()
        delta.content = content
        delta.tool_calls = None
        chunk.choices = [mock.MagicMock(delta=delta)]
    return chunk


def test_chat_stream_extracts_usage() -> None:
    """流式：stream_options 请求 + 最后 chunk 提取 usage。"""
    client = LLMClient(api_key="sk-test")
    chunks = [
        _make_chunk(content="你"),
        _make_chunk(content="好"),
        _make_chunk(usage=mock.MagicMock(
            prompt_tokens=100, completion_tokens=5, total_tokens=105)),
    ]
    with mock.patch.object(client._client.chat.completions, "create",
                           return_value=iter(chunks)) as m_create:
        result = client.chat_stream([{"role": "user", "content": "hi"}])
    assert result.usage == {
        "prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105,
    }
    # 流式请求必须带 stream_options（否则 usage 不返回）
    kwargs = m_create.call_args.kwargs
    assert kwargs.get("stream_options") == {"include_usage": True}
    assert kwargs.get("stream") is True


def test_chat_stream_no_choices_chunk_ok() -> None:
    """流式最后 chunk（choices 空）不 IndexError——usage 提取。"""
    client = LLMClient(api_key="sk-test")
    chunks = [
        _make_chunk(content="hi"),
        _make_chunk(usage=mock.MagicMock(
            prompt_tokens=10, completion_tokens=2, total_tokens=12)),
    ]
    with mock.patch.object(client._client.chat.completions, "create",
                           return_value=iter(chunks)):
        result = client.chat_stream([{"role": "user", "content": "hi"}])
    assert result.usage == {
        "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
    }
    assert result.content == "hi"


# ── 插件 resource_monitor ──────────────────────────────────────────────────


def _usage(prompt: int = 1000, completion: int = 200) -> dict:
    return {
        "prompt_tokens": prompt, "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def test_monitor_accumulates() -> None:
    """多次调用累加 total_tokens + 记录历史。"""
    m = ResourceMonitorPlugin(config={})
    m._on_post_llm(mock.MagicMock(usage=_usage(1000, 200)))
    m._on_post_llm(mock.MagicMock(usage=_usage(2000, 300)))
    assert m.total_tokens == 3500
    assert len(m.usage_history) == 2


def test_no_status_line_normal(capsys) -> None:
    """正常轮（<80%）不再打印每轮状态行（交互调整 2026-08-21）。"""
    m = ResourceMonitorPlugin(config={})
    m._on_post_llm(mock.MagicMock(usage=_usage(12000, 800)))
    assert capsys.readouterr().out == ""


def test_monitor_warning_threshold(capsys) -> None:
    """≥80% 上下文 → 条件警告（平时安静，临界提醒）。"""
    m = ResourceMonitorPlugin(config={})
    # 52000/64000 ≈ 81% → 警告
    m._on_post_llm(mock.MagicMock(usage=_usage(52000, 500)))
    out = capsys.readouterr().out
    assert "⚠️" in out
    assert "81%" in out


def test_monitor_report() -> None:
    """会话汇总格式（累计/次数/成本估算）。"""
    m = ResourceMonitorPlugin(config={})
    m._on_post_llm(mock.MagicMock(usage=_usage(1000, 200)))
    m._on_post_llm(mock.MagicMock(usage=_usage(2000, 300)))
    report = m.report()
    assert "累计" in report
    assert "3,500" in report  # 千分位格式
    assert "2 次" in report


# ── 数据源修正：估算 + 锚点校准（DSH 式混合，方案 2026-08-21） ──────────────


def test_estimate_messages_basic() -> None:
    """估算：ceil(长度/4) + 每条 overhead。"""
    from qi_agent.plugins.resource_monitor import estimate_messages
    assert estimate_messages([]) == 0
    assert estimate_messages([{"role": "user", "content": "hi"}]) == 5  # 4 + ceil(2/4)
    assert estimate_messages([{"role": "user", "content": "你好"}]) == 5  # 2 字符
    assert estimate_messages([{"role": "user", "content": "a" * 10}]) == 7  # 4 + ceil(10/4)


def test_estimate_messages_tool_calls() -> None:
    """估算：tool_calls 消息额外计价（name + arguments）。"""
    from qi_agent.plugins.resource_monitor import estimate_messages
    msg = {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "1", "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"}}],
    }
    # 4(overhead) + 0(content) + ceil(8/4) + ceil(2/4) = 7
    assert estimate_messages([msg]) == 7


def test_no_usage_skip_without_messages(capsys) -> None:
    """无 usage 且无 messages（旧调用方）→ 跳过不崩溃、不统计。"""
    m = ResourceMonitorPlugin(config={})
    m._on_post_llm(mock.MagicMock(usage=None, content="hi"))
    assert m.total_tokens == 0
    assert m.estimated_calls == 0
    assert capsys.readouterr().out == ""


def test_no_usage_pure_estimate(capsys) -> None:
    """无真实样本 → 纯估算累积（正常阈值下无打印）。"""
    m = ResourceMonitorPlugin(config={})
    messages = [{"role": "user", "content": "hello world"}]  # est = 4 + ceil(11/4) = 7
    m._on_post_llm(mock.MagicMock(usage=None, content="hi"), messages=messages)
    assert m.estimated_calls == 1
    assert m.total_tokens == 8  # prompt 7 + completion ceil(2/4)=1
    assert capsys.readouterr().out == ""


def test_estimate_warning_threshold(capsys) -> None:
    """估算轮 ≥80% 也触发条件警告。"""
    m = ResourceMonitorPlugin(config={})
    # 长消息让估算 prompt 超阈值：ceil(204800/4) = 51200 → 正好 80%
    messages = [{"role": "user", "content": "a" * 204_800}]
    m._on_post_llm(mock.MagicMock(usage=None, content="hi"), messages=messages)
    assert "⚠️" in capsys.readouterr().out


def test_anchor_calibration() -> None:
    """有真实样本后无 usage → 锚点 + surface 增量（≠ 纯估算）。"""
    m = ResourceMonitorPlugin(config={})
    # 第一轮：真实样本（锚点建立）
    m._on_post_llm(
        mock.MagicMock(usage={"prompt_tokens": 100, "completion_tokens": 50,
                              "total_tokens": 150}),
        messages=[{"role": "user", "content": "你好"}],  # est=5 → anchor_surface=5
    )
    assert m.total_tokens == 150
    # 第二轮：无 usage → 锚点校准
    messages2 = [
        {"role": "user", "content": "你好"},      # 5
        {"role": "assistant", "content": "回复"},  # 5
    ]  # surface_now = 10
    m._on_post_llm(mock.MagicMock(usage=None, content="回复"), messages=messages2)
    # est_prompt = 100 + (10 - 5) = 105；est_completion = ceil(2/4) = 1
    assert m.total_tokens == 150 + 106
    assert m.estimated_calls == 1
    assert len(m.usage_history) == 1  # 估算不进真实样本历史


def test_estimated_accumulate() -> None:
    """多轮估算累计。"""
    m = ResourceMonitorPlugin(config={})
    msgs = [{"role": "user", "content": "hi"}]  # est = 5
    m._on_post_llm(mock.MagicMock(usage=None, content="a"), messages=msgs)   # 5 + 1 = 6
    m._on_post_llm(mock.MagicMock(usage=None, content="ab"), messages=msgs)  # 5 + 1 = 6
    assert m.estimated_calls == 2
    assert m.total_tokens == 12
    assert m.usage_history == []


def test_report_with_estimated() -> None:
    """report：真实 + 估算混合时标注估算轮。"""
    m = ResourceMonitorPlugin(config={})
    m._on_post_llm(mock.MagicMock(
        usage={"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}),
        messages=[{"role": "user", "content": "hi"}])
    m._on_post_llm(mock.MagicMock(usage=None, content="ok"),
                   messages=[{"role": "user", "content": "hi"}])
    report = m.report()
    assert "累计" in report
    assert "1,200" in report
    assert "含估算" in report


def test_report_empty_unchanged() -> None:
    """无任何数据 → 原文案。"""
    m = ResourceMonitorPlugin(config={})
    assert m.report() == "  [资源] 本次会话无 LLM 调用"


def test_post_llm_payload_messages() -> None:
    """agent.py：post-llm 事件携带 messages（估算数据源）。"""
    from qi_agent.agent import Agent
    client = mock.MagicMock()
    client.chat_stream.return_value = ChatResult(content="ok", tool_calls=None)
    agent = Agent(client=client, system_prompt="sys")
    seen = {}
    agent.events.on("agent/post-llm", lambda **kw: seen.update(kw))
    agent.chat("你好", stream_callback=lambda d: None)
    assert "messages" in seen
    assert any(m.get("content") == "你好" for m in seen["messages"])
