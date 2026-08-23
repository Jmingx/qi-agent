"""list_dir 工具：结构化列目录（只读，边界：只列不读）。

设计（2026-08-22 工具边界讨论）：
- 只列目录（名称/类型/大小），不读内容（读 → read_file）
- 结构化返回替代 shell dir 的文本解析（模型拿干净列表）
- 只读无副作用 → 白名单放行（无需审批）
- 敏感目录（.git/.env/__pycache__ 等）不列出——防模型看到敏感路径
"""

import os

from qi_agent.security.path_security import _SENSITIVE_DIRS
from qi_agent.tools.registry import register

# 输出字符上限
_MAX_OUTPUT_CHARS = 3000
# 单目录最多列出条目（防超大目录撑爆输出）
_MAX_ENTRIES = 100

# 隐藏文件前缀（Windows/Linux 通用：.git 等点开头文件）
_HIDDEN_PREFIX = (".")


def _format_size(size: int) -> str:
    """人类可读大小（B/KB/MB）。"""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size // 1024}KB"
    return f"{size // (1024 * 1024)}MB"


def list_dir(path: str = ".") -> str:
    """列出目录内容（名称/类型/大小）。

    只读操作（白名单放行）；敏感目录（.git 等）不列出。

    Args:
        path: 目录路径（默认当前目录）

    Returns:
        格式化列表，或错误提示。
    """
    if not os.path.isdir(path):
        return f"[错误] 目录不存在或不是目录: {path}"

    try:
        entries = sorted(os.listdir(path))
    except OSError as exc:
        return f"[错误] 读取目录失败: {exc}"

    lines = [f"目录: {os.path.abspath(path)}"]
    count = 0
    for name in entries:
        if count >= _MAX_ENTRIES:
            lines.append(f"...（超出 {_MAX_ENTRIES} 条，其余省略）")
            break
        # 敏感目录不列出（.git/.env/__pycache__ 等——防模型看到敏感路径）
        if name.lower() in _SENSITIVE_DIRS or name.startswith(_HIDDEN_PREFIX):
            continue
        full = os.path.join(path, name)
        try:
            if os.path.isdir(full):
                lines.append(f"  [目录] {name}/")
            else:
                size = os.path.getsize(full)
                lines.append(f"  [文件] {name} ({_format_size(size)})")
        except OSError:
            continue
        count += 1

    if count == 0:
        lines.append("  （空目录或无可见条目）")
    return "\n".join(lines)[:_MAX_OUTPUT_CHARS]


register(
    name="list_dir",
    toolset="builtin",
    handler=list_dir,
    description=(
        "列出目录内容（名称/类型/大小，结构化）。"
        "【边界】只列目录不读内容——看文件内容用 read_file；"
        "搜索文件用 search_files；执行任意命令用 shell"
    ),
    schema={
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "列出目录内容（名称/类型/大小，结构化）。"
                "只读操作；敏感目录不列出。看文件内容用 read_file"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（默认当前目录）",
                    },
                },
                "required": [],
            },
        },
    },
)
