"""write_file 工具：写文件（项目内 + 敏感保护 + 覆盖/越界审批）。

安全设计（方案 docs/plans/2026-08-20-write_file工具方案.md）：
- ① 敏感路径（.env/.git/密钥文件）→ 永远拒绝（红线，工具层兜底，不可审批）
- ② 项目内新增文件 → 自动允许（agent 写代码/配置的基本能力）
- ③ 项目内覆盖已有文件 → 需 approved（审批）
- ④ 项目外普通路径 → 需 approved（审批）
- approved 为内部参数（agent 审批注入，模型 schema 不可见——复用 v0.4.18 机制）

分层：插件层（security_guard）判档产生审批；工具层兜底（无插件也安全）。
"""

import os
from pathlib import Path

from qi_agent.tools.path_security import is_sensitive_path
from qi_agent.tools.registry import register

# 项目根（写文件限定的默认范围；测试可 monkeypatch）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _is_inside_project(path: str) -> bool:
    """路径是否在项目内（resolve 后前缀匹配，防 ../ 逃逸）。"""
    try:
        return Path(path).resolve().is_relative_to(_PROJECT_ROOT)
    except (OSError, ValueError):
        return False


def write_file(path: str, content: str, approved: bool = False) -> str:
    """写文件（UTF-8）。

    Args:
        path: 目标文件路径
        content: 文件内容
        approved: 内部参数（agent 审批注入）——覆盖/越界需 True

    Returns:
        成功提示或 [安全拦截]/[错误] 提示
    """
    # ① 红线：敏感路径永远拒绝（即使 approved——审批管不到红线）
    if is_sensitive_path(path):
        return f"[安全拦截] 禁止写入敏感路径: {path}"
    inside = _is_inside_project(path)
    exists = os.path.exists(path)
    # ③④ 覆盖/越界：需 approved（无审批插件/未同意时 fail-closed 拒绝）
    if not approved and (exists or not inside):
        reason = "覆盖已有文件" if exists else "项目外路径"
        return (
            f"[安全拦截] {reason}需用户审批，已拒绝执行。"
            f"（项目内新增文件可自动写入）"
        )
    # ② 执行写入（UTF-8 对齐项目；目录自动创建）
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path}（{len(content)} 字符）"
    except OSError as exc:
        return f"[错误] 写入失败: {exc}"


register(
    name="write_file",
    toolset="builtin",
    handler=write_file,
    description=(
        "写文件（UTF-8）：项目内新增文件自动允许；覆盖已有文件或项目外"
        "路径需用户审批。不能写敏感文件（.env/.git 等）"
    ),
    # 手写 schema：只暴露 path/content——approved 是内部参数（agent 审批注入）
    schema={
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "写文件（UTF-8）：项目内新增文件自动允许；覆盖已有文件或"
                "项目外路径会弹出审批请求，用户同意后执行。若被拒绝"
                "（[审批拒绝]），说明用户不同意，不要反复尝试"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目标文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容（UTF-8）",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
)
