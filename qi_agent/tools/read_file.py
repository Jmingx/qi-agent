"""read_file 工具：读取文本文件指定行范围（分页，v0.4.20）。

安全设计：接入 path_security 路径检查——拒绝读取敏感文件
（.env、.ssh、.git 等），防止 agent 被诱导读取 API key 等机密。

分页设计（方案 docs/plans/2026-08-20-read_file分页升级方案.md）：
- offset/limit 行级分页：大文件分段读完（模型用 offset 续读）
- 返回 header 元信息（"第 X-Y 行（共 N 行）"）——模型知道文件大小
- tail 续读提示（"剩余 M 行，可用 offset=N 继续"）——可行动信息设计
- 字符上限 50_000 双保险：2000 行超大文件防撑爆上下文
"""

from qi_agent.security.path_security import is_sensitive_path
from qi_agent.tools.registry import register

# 单次返回字符上限（双保险：limit 2000 行仍可能超大）
_MAX_CHARS = 50_000
# 单次最大行数（用户需求"能读取2000行"）
_MAX_LIMIT = 2000


def read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
    """读取文本文件指定行范围。

    Args:
        path: 文件路径
        offset: 起始行（1 起；<1 自动修正为 1）
        limit: 最大读取行数（上限 2000）

    Returns:
        带元信息的行内容（header + 内容 + 续读提示），或拦截/错误提示。
    """
    # 路径安全检查（安全底线，硬编码不可配置）
    if is_sensitive_path(path):
        return f"[安全拦截] 路径敏感，禁止读取: {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return f"[错误] 文件不存在: {path}"
    except IsADirectoryError:
        return f"[错误] 路径是目录: {path}"
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"

    total = len(lines)
    offset = max(offset, 1)  # 修正非法 offset
    limit = min(max(limit, 1), _MAX_LIMIT)
    end = min(offset + limit - 1, total)  # 越界自动截到末尾
    content = "".join(lines[offset - 1:end])

    # 字符上限双保险（超大行/超大文件）
    if len(content) > _MAX_CHARS:
        content = (
            content[:_MAX_CHARS]
            + "\n...内容过长已截断（可减小 limit 或增大 offset 分段读取）"
        )

    # 返回结构：header（范围+总行数）+ 内容 + tail（续读提示）
    header = f"第 {offset}-{end} 行（共 {total} 行）"
    parts = [header, content]
    if end < total:
        parts.append(
            f"...已截断（剩余 {total - end} 行），可用 offset={end + 1} 继续读取"
        )
    return "\n".join(parts)


register(
    name="read_file",
    toolset="builtin",
    handler=read_file,
    description=(
        "读取指定路径的文本文件内容（行级分页：offset 起始行、limit 最大行数；"
        "返回中带总行数与续读提示，大文件可分段读取）"
    ),
    # 手写 schema（offset/limit 有默认值，非必填）
    schema={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取指定路径的文本文件内容（行级分页）。返回格式："
                "'第 X-Y 行（共 N 行）' + 内容；未读完时末尾提示剩余行数"
                "与续读 offset。大文件请用 offset 分段读取"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "offset": {
                        "type": "integer",
                        "description": "起始行（1 起，默认 1）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大读取行数（默认 2000，上限 2000）",
                    },
                },
                "required": ["path"],
            },
        },
    },
)
