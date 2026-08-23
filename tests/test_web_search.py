"""web_search 工具测试：双后端解析 + 自动降级链（mock 网络层，不碰真实网络）。

方案：docs/plans/2026-08-22-Web工具方案.md
后端：DeepSeek 官方搜索（web_search_20250305）主 + Bing HTML 兜底。
"""

import json
from unittest import mock

import qi_agent.tools.builtin.web_search as ws

# ── DeepSeek 后端解析 ─────────────────────────────────────────────────────

DEEPSEEK_RESPONSE = {
    "content": [
        {"type": "text", "text": "searching..."},
        {"type": "server_tool_use", "id": "call_1", "name": "web_search",
         "input": {"query": "量子计算"}},
        {"type": "web_search_tool_result", "tool_use_id": "call_1", "content": [
            {"type": "web_search_result", "title": "量子计算进展",
             "url": "https://example.com/q", "snippet": "自旋量子比特..."},
            {"type": "web_search_result", "title": "量子霸权",
             "url": "https://example.com/2", "snippet": "谷歌宣布..."},
        ]},
    ],
}


def _resp_with(content: bytes):
    """构造 with 友好的 mock 响应（with urlopen() as resp 时 resp 是
    __enter__.return_value——不设置会拿到未配置的 MagicMock，经典坑）。"""
    resp = mock.MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = content
    return resp


def _mock_deepseek_ok(mocker_side=None):
    """mock urlopen 返回 DeepSeek 响应。"""
    return mock.patch(
        "urllib.request.urlopen",
        return_value=_resp_with(json.dumps(DEEPSEEK_RESPONSE).encode()),
        side_effect=mocker_side,
    )


def test_deepseek_backend_parses_results(monkeypatch) -> None:
    """DeepSeek 响应 → 解析出 title/url/snippet 列表。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    with _mock_deepseek_ok():
        results = ws._search_deepseek("量子计算")
    assert results is not None
    assert len(results) == 2
    assert results[0]["title"] == "量子计算进展"
    assert results[0]["url"] == "https://example.com/q"


def test_deepseek_backend_failure_returns_none() -> None:
    """DeepSeek API 失败（HTTPError）→ 返回 None（触发降级链）。"""
    import urllib.error

    with mock.patch("urllib.request.urlopen",
                    side_effect=urllib.error.HTTPError(
                        "url", 500, "err", None, None)):
        assert ws._search_deepseek("x") is None


def test_deepseek_no_key_returns_none(monkeypatch) -> None:
    """无 DEEPSEEK_API_KEY → None（直接走 Bing 兜底）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with mock.patch("urllib.request.urlopen") as m:
        assert ws._search_deepseek("x") is None
        m.assert_not_called()  # 无 key 不发请求


# ── Bing 后端解析 ─────────────────────────────────────────────────────────

BING_HTML = """
<html><body><ol id="b_results">
<li class="b_algo">
  <h2><a href="https://www.skillupai.com/blog/python">Python 入门教程</a></h2>
  <p>Python 的基本语法与信号量示例。</p>
</li>
<li class="b_algo">
  <h2><a href="https://docs.python.org/zh-cn/3/">Python 官方文档</a></h2>
  <p>官方参考手册，包含标准库。</p>
</li>
</ol></body></html>
"""


def _mock_bing_ok(html: str = BING_HTML):
    return mock.patch(
        "urllib.request.urlopen",
        return_value=_resp_with(html.encode()),
    )


def test_bing_backend_parses_results() -> None:
    """Bing b_algo 块 → 解析出标题/链接/摘要。"""
    with _mock_bing_ok():
        results = ws._search_bing("python 信号量")
    assert results is not None
    assert len(results) == 2
    assert results[0]["title"] == "Python 入门教程"
    assert results[0]["url"] == "https://www.skillupai.com/blog/python"
    assert "信号量" in results[0]["snippet"]


def test_bing_no_results_returns_none() -> None:
    """Bing 无结果块 → None（降级链最终失败 → 错误提示）。"""
    with _mock_bing_ok(html="<html><body>no results</body></html>"):
        assert ws._search_bing("x") is None


# ── 降级链 ────────────────────────────────────────────────────────────────


def test_fallback_chain_deepseek_to_bing(monkeypatch) -> None:
    """DeepSeek 失败 → 自动切 Bing → 返回 Bing 结果（模型无感知）。"""
    import urllib.error

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    calls = []

    def fake_urlopen(req, *a, **kw):
        calls.append(str(req))
        if "anthropic" in str(req):
            raise urllib.error.HTTPError("u", 500, "e", None, None)
        return _resp_with(BING_HTML.encode())

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = ws.web_search("python 信号量", limit=2)
    assert "Python 入门教程" in result  # Bing 结果回来了
    assert len(calls) == 2  # DeepSeek 一次 + Bing 一次


def test_fallback_chain_all_fail() -> None:
    """双后端都失败 → 可行动错误提示。"""
    import urllib.error

    with mock.patch("urllib.request.urlopen",
                    side_effect=urllib.error.HTTPError(
                        "u", 500, "e", None, None)):
        result = ws.web_search("x")
    assert result.startswith("[错误]") or "搜索失败" in result


def test_web_search_limit_respected(monkeypatch) -> None:
    """limit 限制返回条数。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    with _mock_deepseek_ok():
        result = ws.web_search("量子计算", limit=1)
    assert "1." in result
    assert "2." not in result  # limit=1 只有 1 条
