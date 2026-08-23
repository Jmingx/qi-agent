"""web_search 工具：双后端自动降级（DeepSeek 官方搜索主 + Bing HTML 兜底）。

设计（方案 docs/plans/2026-08-22-Web工具方案.md）：
- DeepSeek 官方搜索（web_search_20250305 server tool）：结构化结果、复用
  DEEPSEEK_API_KEY、国内直连（参考 DSH web-search-deepseek）——主后端
- Bing HTML 爬（www.bing.com/search + b_algo 解析）：免费、零 key、不耗
  token——兜底（主后端失效才用，HTML 脆弱性由"仅兜底"缓解）
- 降级链对模型完全透明：一个工具一个接口，后端切换无感知
- 零新依赖：urllib（标准库）+ html.parser（标准库）
"""

import html.parser
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from qi_agent.tools.registry import register

# 单次搜索返回的最大结果数（默认）
_DEFAULT_LIMIT = 5
# 输出字符上限（防撑爆上下文）
_MAX_OUTPUT_CHARS = 3000
# DeepSeek 搜索超时（LLM 调用耗时较长）
_DEEPSEEK_TIMEOUT = 30
# Bing 请求超时
_BING_TIMEOUT = 10
# 浏览器 UA（Bing 对裸 UA 返回空壳页，实测）
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/anthropic/v1/messages"


def _decode_bing_url(href: str) -> str:
    """还原 Bing /ck/a 跳转链接中的真实 URL（u 参数是 base64url，a1 前缀）。

    Bing 结果链接全部是 https://www.bing.com/ck/a?...&u=a1<base64url>&...
    实测（2026-08-22）：u 参数 a1 前缀 + base64url 编码的真实 URL。
    解析失败 → 原样返回（降级为 ck 链接，总比丢弃结果好）。
    """
    if "bing.com/ck" not in href:
        return href
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m:
        return href
    try:
        import base64

        padding = "=" * (-len(m.group(1)) % 4)
        decoded = base64.urlsafe_b64decode(m.group(1) + padding).decode(
            "utf-8", "replace"
        )
        return decoded if decoded.startswith("http") else href
    except Exception:
        return href


def _search_deepseek(query: str) -> list[dict] | None:
    """DeepSeek 官方搜索：POST web_search_20250305 server tool。

    Returns:
        结果列表 [{title, url, snippet}]；失败/无 key/无结果 → None
    """
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None  # 无 key → 走 Bing 兜底（不发请求）

    body = {
        "model": "deepseek-chat",
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": f"Perform a web search for the query: {query}",
            }],
        }],
        "tools": [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 1,
        }],
    }
    req = urllib.request.Request(
        _DEEPSEEK_SEARCH_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_DEEPSEEK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None  # 触发降级链

    # 提取 web_search_tool_result 块的 web_search_result 项
    results: list[dict] = []
    for block in data.get("content", []):
        if block.get("type") != "web_search_tool_result":
            continue
        for item in block.get("content", []):
            if item.get("type") != "web_search_result":
                continue
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet") or item.get("content", ""),
            })
    return results or None


class _BingParser(html.parser.HTMLParser):
    """Bing b_algo 结果块解析（状态机）。

    每块提取：标题（h2 文本）/ 链接（第一个非 bing ck 的 href）/ 摘要（p 文本）。
    结构变化容错：坏块跳过，能提多少提多少。
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._li_depth = 0         # li 嵌套深度（真实 Bing 块内嵌套 li——只数
                                   # b_algo 会提前截断，所有 li 计数才正确）
        self._cur: dict | None = None
        self._in_h2 = False
        self._h2_text: list[str] = []
        self._in_p = False
        self._p_text: list[str] = []
        self._capture_p = False    # 是否记录当前 p（块内第一个 p）

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "li":
            self._li_depth += 1
            classes = dict(attrs).get("class", "").split()
            if "b_algo" in classes and self._cur is None:
                self._cur = {"title": "", "url": "", "snippet": ""}
        if self._cur is None:
            return
        attrs = dict(attrs)
        if tag == "h2":
            self._in_h2 = True
        elif tag == "p":
            self._in_p = True
            self._capture_p = True
        elif tag == "a" and not self._cur["url"]:
            href = attrs.get("href", "")
            if href.startswith("http"):
                # Bing 链接是 /ck/a 跳转包装——还原真实 URL（实测 u 参数）
                real = _decode_bing_url(href)
                if real.startswith("http"):
                    self._cur["url"] = real

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._li_depth > 0:
            self._li_depth -= 1
            if self._li_depth == 0 and self._cur:
                # 块完成：标题取 h2 文本（无则用 URL 尾部）
                title = "".join(self._h2_text).strip()
                if not title:
                    title = self._cur["url"].rstrip("/").rsplit("/", 1)[-1]
                self._cur["title"] = title
                self._cur["snippet"] = "".join(self._p_text).strip()
                if self._cur["url"]:
                    self.results.append(self._cur)
                self._cur = None
            self._in_h2 = False
            self._in_p = False
            self._h2_text = []
            self._p_text = []
            self._capture_p = False
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "p":
            self._in_p = False

    def handle_data(self, data: str) -> None:
        if self._cur is None:
            return
        if self._in_h2:
            self._h2_text.append(data)
        if self._in_p and self._capture_p:
            self._p_text.append(data)


def _search_bing(query: str) -> list[dict] | None:
    """Bing HTML 爬取：www.bing.com/search + b_algo 解析。"""
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=_BING_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError):
        return None
    parser = _BingParser()
    try:
        parser.feed(html)
    except Exception:
        return None
    return parser.results or None


def _format_results(results: list[dict]) -> str:
    """格式化结果列表 → 可读文本（编号 + 标题/链接/摘要）。"""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:200]}")
    return "\n".join(lines)[:_MAX_OUTPUT_CHARS]


def web_search(query: str, limit: int = _DEFAULT_LIMIT) -> str:
    """搜索网络，返回结构化结果（标题/链接/摘要）。

    双后端自动降级（方案 2026-08-22）：
    1. DeepSeek 官方搜索（结构化、复用 key、国内直连）——主
    2. Bing HTML 爬（免费、零 key、不耗 token）——兜底
    失败链：DeepSeek 异常/无 key → Bing；双失败 → 可行动错误提示。

    Args:
        query: 搜索关键词
        limit: 返回结果条数（默认 5）

    Returns:
        格式化搜索结果文本，或错误提示。
    """
    if not query.strip():
        return "[参数错误] 搜索关键词不能为空"

    # 降级链：主后端失败自动切备选（对模型完全透明）
    results = _search_deepseek(query)
    if not results:
        results = _search_bing(query)
    if not results:
        return "[错误] 搜索失败（双后端均不可用）——可换关键词重试，或检查网络"

    return _format_results(results[:max(1, min(int(limit), 10))])


register(
    name="web_search",
    toolset="builtin",
    handler=web_search,
    description=(
        "搜索网络获取最新信息（结构化结果：标题/链接/摘要）。"
        "查网上信息/新闻/资料用本工具；本地文件搜索用 search_files/read_file"
    ),
    schema={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "搜索网络获取最新信息（结构化结果：标题/链接/摘要）。"
                "查网上信息/新闻/资料用本工具；本地文件搜索用 read_file"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（简洁准确）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回结果条数（默认 5，最多 10）",
                    },
                },
                "required": ["query"],
            },
        },
    },
)
