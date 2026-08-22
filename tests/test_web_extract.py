"""web_extract 工具测试：SSRF 防护 + 内容提取（mock 网络层）。

方案：docs/plans/2026-08-22-Web工具方案.md
SSRF：拒绝内网/本地地址（agent 被诱导探测内网 = 数据泄露通道）。
"""

from unittest import mock

import qi_agent.tools.web_extract as we


# ── SSRF 防护 ─────────────────────────────────────────────────────────────


def test_ssrf_blocked_addresses() -> None:
    """内网/本地地址全部拒绝（localhost/IPv4 私网/链路本地）。"""
    blocked = [
        "http://localhost:8080/admin",
        "http://127.0.0.1/secret",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.1/corp",
        "http://169.254.169.254/metadata",  # 云元数据端点（经典 SSRF 目标）
        "http://0.0.0.0/x",
    ]
    for url in blocked:
        assert we._is_ssrf_blocked(url), f"应拒绝: {url}"


def test_ssrf_allows_public_urls() -> None:
    """公网 URL 放行。"""
    allowed = [
        "https://www.example.com/page",
        "https://docs.python.org/3/",
        "http://example.org",
    ]
    for url in allowed:
        assert not we._is_ssrf_blocked(url), f"应放行: {url}"


def test_extract_blocks_ssrf_before_request() -> None:
    """被 SSRF 拦截的 URL 不发起请求（直接返回拒绝提示）。"""
    with mock.patch("urllib.request.urlopen") as m:
        result = we.web_extract("http://127.0.0.1/secret")
    assert "内网" in result or "拒绝" in result
    m.assert_not_called()  # 没发请求


# ── 内容提取 ──────────────────────────────────────────────────────────────

SIMPLE_HTML = """
<html><head><title>测试页面</title></head>
<body>
<nav>导航链接</nav>
<article>
<h1>标题一</h1>
<p>这是正文第一段，包含关键信息。</p>
<script>var x = 1;</script>
<style>.cls { color: red }</style>
<p>第二段。</p>
</article>
</body></html>
"""


def _resp_with(content: bytes):
    """with 友好的 mock 响应（__enter__.return_value 设置——经典坑）。"""
    resp = mock.MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = content
    return resp


def test_extract_title_and_text() -> None:
    """提取标题 + 正文（去 script/style/导航标签）。"""
    with mock.patch("urllib.request.urlopen",
                    return_value=_resp_with(SIMPLE_HTML.encode())):
        result = we.web_extract("https://www.example.com/page")
    assert "测试页面" in result  # 标题
    assert "这是正文第一段" in result  # 正文
    assert "var x" not in result  # script 被去掉
    assert ".cls" not in result  # style 被去掉


def test_extract_truncates_long_content() -> None:
    """超长内容截断（防撑爆上下文）。"""
    content = (
        f"<html><head><title>T</title></head><body>{'x' * 300_000}</body></html>"
    ).encode()
    with mock.patch("urllib.request.urlopen",
                    return_value=_resp_with(content)):
        result = we.web_extract("https://www.example.com/big")
    assert len(result) < we._MAX_OUTPUT_CHARS * 2  # 截断生效


def test_extract_network_error() -> None:
    """网络错误 → 可行动提示。"""
    import urllib.error

    with mock.patch("urllib.request.urlopen",
                    side_effect=urllib.error.URLError("timeout")):
        result = we.web_extract("https://www.example.com/")
    assert "失败" in result or "错误" in result
