"""LLM 客户端超时测试：timeout 参数传递（v0.4.24）。

方案：docs/plans/2026-08-22-LLM调用超时方案.md（用户拍板直接做）
背景：评测线程残留（wait_for 无法终止线程）——LLM 调用加 SDK timeout 后
最多 timeout 秒返回（抛异常），线程不再残留，asyncio.run join 不再卡 300s。
"""

from unittest import mock

from qi_agent.llm import LLMClient


def test_timeout_default_60s() -> None:
    """默认 timeout=60.0（对齐方案默认值）。"""
    with mock.patch("qi_agent.llm.OpenAI") as m_openai:
        LLMClient(api_key="test-key")
    m_openai.assert_called_once_with(
        api_key="test-key", base_url="https://api.deepseek.com", timeout=60.0
    )


def test_timeout_custom() -> None:
    """自定义 timeout 透传到 SDK 客户端。"""
    with mock.patch("qi_agent.llm.OpenAI") as m_openai:
        LLMClient(api_key="test-key", timeout=10.0)
    _, kwargs = m_openai.call_args
    assert kwargs["timeout"] == 10.0


def test_chat_passes_timeout() -> None:
    """chat 的 create 调用显式传 timeout（防客户端默认漂移）。"""
    with mock.patch("qi_agent.llm.OpenAI") as m_openai:
        client = LLMClient(api_key="test-key", timeout=7.0)
        m_create = m_openai.return_value.chat.completions.create
        client.chat([{"role": "user", "content": "hi"}])
    _, kwargs = m_create.call_args
    assert kwargs["timeout"] == 7.0


def test_chat_stream_passes_timeout() -> None:
    """chat_stream 的 create 调用显式传 timeout。"""
    with mock.patch("qi_agent.llm.OpenAI") as m_openai:
        client = LLMClient(api_key="test-key", timeout=7.0)
        m_create = m_openai.return_value.chat.completions.create
        m_create.return_value = iter([])  # 空流（无 chunk）
        client.chat_stream([{"role": "user", "content": "hi"}])
    _, kwargs = m_create.call_args
    assert kwargs["timeout"] == 7.0
