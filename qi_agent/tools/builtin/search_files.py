"""search_files 工具：文件内容搜索（只读，边界：只定位不读全文）。

设计（2026-08-22 工具边界讨论）：
- 纯 Python 实现（os.walk + 正则）——不依赖 rg 二进制（跨平台，零新依赖）
- 返回 文件+行号+匹配行（结构化）——模型定位后自己决定读哪个（read_file）
- 跳过敏感/大目录（.git/.venv/node_modules/__pycache__）——防噪音 + 防敏感
- 只读无副作用 → 白名单放行
"""

import os
import re

from qi_agent.security.path_security import _SENSITIVE_DIRS
from qi_agent.tools.registry import register

# 输出字符上限
_MAX_OUTPUT_CHARS = 4000
# 最大匹配数（防海量匹配撑爆输出）
_MAX_MATCHES = 50
# 单行匹配文本截断
_MAX_LINE_CHARS = 150
# 跳过的隐藏目录（防噪音；与 path_security 敏感目录对齐）
_SKIP_DIRS = _SENSITIVE_DIRS | {"node_modules"}
# 跳过的文件扩展名（二进制/大文件）
_SKIP_EXTENSIONS = {".pyc", ".exe", ".dll", ".png", ".jpg", ".gif",
                    ".zip", ".7z", ".pdf", ".lock", ".svg"}


def _walk_skip_hidden(root: str):
    """os.walk 变体：跳过隐藏目录（_SKIP_DIRS 和点开头目录）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        yield dirpath, filenames


def search_files(pattern: str, path: str = ".",
                 file_glob: str | None = None, limit: int = _MAX_MATCHES) -> str:
    """在文件中搜索内容（正则），返回 文件:行号: 匹配行。

    只定位不读全文——找到后模型用 read_file 看具体内容。
    跳过隐藏/敏感目录（.git/.venv 等）与二进制文件。

    Args:
        pattern: 正则表达式（大小写不敏感）
        path: 搜索根目录（默认当前目录）
        file_glob: 文件名过滤（如 *.py；None = 全部）
        limit: 最大匹配数（默认 50）

    Returns:
        格式化匹配列表，或提示。
    """
    if not os.path.isdir(path):
        return f"[错误] 目录不存在或不是目录: {path}"
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"[参数错误] 正则表达式无效: {exc}"

    # file_glob → 正则（*.py → \.py$；支持 ? 通配）
    glob_re = None
    if file_glob:
        import fnmatch

        glob_re = re.compile(
            fnmatch.translate(file_glob), re.IGNORECASE
        )

    matches: list[str] = []
    try:
        for dirpath, filenames in _walk_skip_hidden(path):
            for fname in filenames:
                if glob_re and not glob_re.match(fname):
                    continue
                if fname.startswith("."):
                    continue
                if os.path.splitext(fname)[1].lower() in _SKIP_EXTENSIONS:
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            if regex.search(line):
                                snippet = line.strip()[:_MAX_LINE_CHARS]
                                matches.append(f"{fpath}:{lineno}: {snippet}")
                                if len(matches) >= limit:
                                    break
                except OSError:
                    continue
                if len(matches) >= limit:
                    break
            if len(matches) >= limit:
                break
    except OSError as exc:
        return f"[错误] 搜索失败: {exc}"

    if not matches:
        return f"[提示] 无匹配（关键词: {pattern}，目录: {path}）——可换关键词重试"
    return "\n".join(matches)[:_MAX_OUTPUT_CHARS]


register(
    name="search_files",
    toolset="builtin",
    handler=search_files,
    description=(
        "在文件中搜索内容（正则，返回 文件:行号: 匹配行）。"
        "【边界】只定位不读全文——看全文用 read_file；"
        "列目录用 list_dir；执行任意命令用 shell（别用 findstr 拼接）"
    ),
    schema={
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "在文件中搜索内容（正则，返回 文件:行号: 匹配行）。"
                "只读操作；跳过隐藏/敏感目录与二进制文件"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式（大小写不敏感）",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索根目录（默认当前目录）",
                    },
                    "file_glob": {
                        "type": "string",
                        "description": "文件名过滤（如 *.py；可选）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大匹配数（默认 50）",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
)
