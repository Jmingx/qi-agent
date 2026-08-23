"""web_extract 工具：网页内容提取（标题 + 正文文本）。

设计（方案 docs/plans/2026-08-22-Web工具方案.md）：
- 只读 GET 无副作用 → 白名单放行（无需审批，对齐 read_file 语义）
- SSRF 防护（安全底线）：拒绝内网/本地地址——agent 被恶意网页/文件诱导
  请求内网地址 = 数据泄露通道（经典攻击：云元数据 169.254.169.254）
- 零新依赖：urllib + html.parser（标准库）
"""

import html.parser
import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request

from qi_agent.tools.registry import register

# 请求超时（秒）
_REQUEST_TIMEOUT = 10
# 响应大小上限（字节）——防大页面撑爆上下文
_MAX_RESPONSE_BYTES = 256 * 1024
# 输出字符上限
_MAX_OUTPUT_CHARS = 3000
# 浏览器 UA（部分站点对裸 UA 返回错误页）
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _is_ssrf_blocked(url: str) -> bool:
    """SSRF 防护：URL 指向内网/本地地址 → True（拒绝）。

    检查 host 文本/IP 字面：
    - localhost / 0.0.0.0
    - 私网段（10/8、172.16/12、192.168/16）
    - 回环（127/8）、链路本地（169.254/16）
    局限（诚实记录）：域名解析到内网 IP（DNS rebinding）需要解析后检查——
    当前只查 host 文本/IP 字面，域名形式不做 DNS 探测（避免工具自身被利用
    做内网扫描）；已知局限，安全底线优先。
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not host:
        return True  # 无 host（畸形 URL）拒绝
    if host in ("localhost", "0.0.0.0"):
        return True
    # IPv4/IPv6 字面检查
    try:
        ip = ipaddress.ip_address(host.split(":")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return True
    except ValueError:
        pass  # 域名形式（见 docstring 局限说明）
    return False


class _TextExtractor(html.parser.HTMLParser):
    """提取 title + 正文文本（去 script/style 及标签）。"""

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self.text_parts: list[str] = []
        self._skip_depth = 0   # script/style 嵌套深度（跳过其内容）
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag == "title" and self._skip_depth == 0:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data.strip()
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.text_parts.append(text)


def web_extract(url: str) -> str:
    """提取网页标题 + 正文文本（简化版）。

    只读 GET 无副作用（白名单放行，无需审批）。SSRF 防护：内网/本地
    地址直接拒绝（不发请求）。

    Args:
        url: 目标网页 URL（http/https）

    Returns:
        标题 + 正文（截断），或错误提示。
    """
    if not url.strip():
        return "[参数错误] URL 不能为空"
    if _is_ssrf_blocked(url):
        return "[安全拦截] 目标地址指向内网/本地（SSRF 防护），已拒绝请求"
    if not url.startswith(("http://", "https://")):
        return "[参数错误] 仅支持 http/https 协议"

    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            html = resp.read(_MAX_RESPONSE_BYTES + 1).decode(
                "utf-8", errors="replace"
            )
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"[错误] 网页请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"

    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return "[错误] 网页解析失败（页面结构异常）"

    body = " ".join(parser.text_parts)[:_MAX_OUTPUT_CHARS]
    title = parser.title or url
    return f"标题: {title}\n正文: {body or '(无文本内容)'}"


register(
    name="web_extract",
    toolset="builtin",
    handler=web_extract,
    description=(
        "提取网页标题与正文文本（只读 GET，无副作用）。"
        "web_search 结果需要看全文时用本工具；内网/本地地址被 SSRF 防护拒绝"
    ),
    schema={
        "type": "function",
        "function": {
            "name": "web_extract",
            "description": (
                "提取网页标题与正文文本。只读 GET 无副作用；"
                "内网/本地地址被安全防护拒绝"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要提取的网页 URL（http/https）",
                    },
                },
                "required": ["url"],
            },
        },
    },
)
