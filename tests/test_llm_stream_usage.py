"""流式 usage 提取测试（修复 v0.4.22 遗留）。

2026-08-22 真实 API 验证发现：DeepSeek 的 usage 末 chunk **带 choices=1**
（与 OpenAI 的空 choices 不同）——旧代码 `if not chunk.choices` 提取
被跳过 → 流式 usage 恒为 None。修复：chunk 带 usage 就提取。
"""

import pytest


def _make_chunk(content=None, tool_calls=None, usage=None):
    """构造 mock chunk（SimpleNamespace，模拟两种 API 形态）。"""
    from types import SimpleNamespace

    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choices = [SimpleNamespace(delta=delta)] if content or tool_calls or usage is None else []
    # choices 空 vs 带 usage：DeepSeek 末 chunk = choices 有内容 + usage
    if usage is not None:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None))]
    return SimpleNamespace(choices=choices, usage=usage)


@pytest.fixture
def stream_client(monkeypatch):
    """mock LLMClient._client.chat.completions.create 返回流。"""
    from types import SimpleNamespace

    from qi_agent.llm import LLMClient

    client = LLMClient(api_key="test-key")
    stream = [
        _make_chunk(content="你好"),
        _make_chunk(content="世界"),
        _make_chunk(usage=SimpleNamespace(
            prompt_tokens=85, completion_tokens=43, total_tokens=128)),
    ]
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: iter(stream))))
    monkeypatch.setattr(client, "_client", fake)
    return client


def test_stream_usage_extracted(stream_client) -> None:
    """DeepSeek 形态（usage chunk 带 choices）→ usage 正确透出。"""
    result = stream_client.chat_stream([{"role": "user", "content": "hi"}])
    assert result.content == "你好世界"
    assert result.usage == {
        "prompt_tokens": 85,
        "completion_tokens": 43,
        "total_tokens": 128,
    }


def test_stream_openai_style_usage_chunk(monkeypatch) -> None:
    """OpenAI 形态（usage chunk choices 空）→ 也正确提取（兼容）。"""
    from types import SimpleNamespace

    from qi_agent.llm import LLMClient

    client = LLMClient(api_key="test-key")
    stream = [
        _make_chunk(content="hi"),
        # OpenAI 形态：choices 空 + usage
        SimpleNamespace(choices=[], usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15)),
    ]
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: iter(stream))))
    monkeypatch.setattr(client, "_client", fake)
    result = client.chat_stream([{"role": "user", "content": "hi"}])
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5,
                            "total_tokens": 15}
