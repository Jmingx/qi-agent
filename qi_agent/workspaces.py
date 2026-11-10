"""会话工作空间：登记记录与工具路径边界。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionWorkspace:
    """已由服务端确认、绑定到一个 AgentContext 的工作空间快照。"""

    workspace_id: str
    root: Path
    label: str

    def resolve_path(self, value: str) -> Path:
        """把模型给出的路径限定在根目录内，拒绝绝对路径和逃逸。"""
        candidate = Path(value)
        if candidate.is_absolute():
            target = candidate.resolve(strict=False)
        else:
            target = (self.root / candidate).resolve(strict=False)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("路径越出当前工作空间") from exc
        return target


def normalize_workspace(path: str) -> Path:
    """登记时的服务端规范化：仅接受存在的本机绝对目录。"""
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("工作空间必须是绝对路径")
    root = candidate.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("工作空间必须是目录")
    return root
