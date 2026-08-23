"""sticky notes（阶段 B3）：用户显式要求保留的信息，裁剪永不碰。

设计（方案 2026-08-22-上下文管理）：sticky 区挂在 system prompt 里——
滑动窗口裁剪只动非 system 历史，sticky 天然免疫。写入方式第一版：
CLI /remember 命令（agent 写入工具留二期）。与 todo 注入统一收敛为
"上下文注入层"（context/inject.py，阶段 D）。
"""

# 会话级 sticky 存储（进程级单例；CLI 会话内有效）
_STICKY: list[str] = []

# system prompt 里的 sticky 区标题
_STICKY_HEADER = "[重要信息（用户要求保留，永不裁剪）]"


def remember(text: str) -> None:
    """记录一条重要信息（去重：相同内容不重复记录）。"""
    text = text.strip()
    if not text:
        return
    if text not in _STICKY:
        _STICKY.append(text)


def get_sticky_text() -> str:
    """渲染 sticky 区文本（空 → 空串，不污染 system prompt）。"""
    if not _STICKY:
        return ""
    lines = [_STICKY_HEADER]
    lines.extend(f"- {s}" for s in _STICKY)
    return "\n".join(lines)


def list_sticky() -> list[str]:
    """当前 sticky 列表（查看/展示）。"""
    return list(_STICKY)


def reset() -> None:
    """清空（测试隔离/会话重置）。"""
    _STICKY.clear()
