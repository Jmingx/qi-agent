"""file_delete 工具：删除文件（破坏性 → 审批 + 敏感路径红线）。

设计（2026-08-22 工具边界讨论）：
- 只删文件不删目录（目录操作用 shell）——职责单一
- 敏感路径红线（.env/.git/密钥等）：approved 也拒（审批管不到红线，
  对齐 write_file 四档——安全底线硬编码）
- 破坏性操作 → 审批档（security_guard 判 NEED_APPROVAL → approval_gate
  弹窗 → 用户批准后 approved 内部注入——模型无法自主删除）
- approved 是内部参数（schema 不可见 + 调用级 internal——防绕过）
"""

import os

from qi_agent.security.path_security import is_sensitive_path
from qi_agent.tools.registry import register


def file_delete(path: str, approved: bool = False) -> str:
    """删除文件（需审批；敏感路径永不删）。

    Args:
        path: 要删除的文件路径
        approved: 审批注入（内部参数，模型 schema 不可见）

    Returns:
        成功/拦截/错误提示。
    """
    # 红线：敏感路径永不删（approved 也拒——审批管不到红线）
    if is_sensitive_path(path):
        return f"[安全拦截] 敏感路径禁止删除（红线）: {path}"

    # 审批闸：无 approved → 拒绝（模型路径永远到不了这里——security_guard
    # 判档先弹窗，approved 由审批注入；这里是工具层兜底）
    if not approved:
        return f"[审批拒绝] 删除文件需要用户审批: {path}"

    if not os.path.exists(path):
        return f"[错误] 文件不存在: {path}"
    if os.path.isdir(path):
        return f"[错误] 这是目录不是文件——目录操作用 shell 工具: {path}"

    try:
        os.remove(path)
    except OSError as exc:
        return f"[错误] 删除失败: {exc}"
    return f"[已删除] {path}"


register(
    name="file_delete",
    toolset="builtin",
    handler=file_delete,
    description=(
        "删除文件（破坏性操作，需用户审批；敏感路径永不删）。"
        "【边界】只删文件不删目录——目录操作用 shell；"
        "创建/修改文件用 write_file"
    ),
    # 审批声明（v0.4.26 声明式）：无条件审批模板——删除是破坏性操作，
    # 任何调用都需弹窗审批（红线在工具层：敏感路径 approved 也拒）
    approval="删除文件 {path}",
    # 手写 schema：只暴露 path——approved 是内部参数（agent 审批注入），
    # 不进 schema → 模型看不到也传不了（防绕过，v0.4.18 机制）
    schema={
        "type": "function",
        "function": {
            "name": "file_delete",
            "description": (
                "删除文件（破坏性操作，会弹窗请求用户审批；敏感路径永不删）。"
                "只删文件，不删目录"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
)
