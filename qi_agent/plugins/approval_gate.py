"""审批交互插件：监听 agent/tool-approval，弹窗确认 + 会话级前缀记忆。

方案：docs/plans/2026-08-20-shell三档权限与审批机制方案.md
分层：工具管执行 · 插件管决策（security_guard 判档）· 插件管交互（本插件弹窗）
设计（业界对照）：
- fail-closed：无监听器 / 非 tty（评测、管道）→ 自动拒绝（Hermes deny fast）
- 会话级记忆：a=总是允许 → 记住命令【前缀】→ 同前缀命令不再弹窗
  （用户决策点 5：命令前缀，非精确——体验优先，已明示误放行风险）
- 红线不进审批：security_guard 在 tool-call 层已硬拒，本插件只处理 NEED_APPROVAL 档
"""

import sys

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin


class ApprovalGatePlugin:
    """审批交互插件：弹窗确认（y/n/a=总是允许）+ 会话级前缀记忆。"""

    def __init__(self, config: dict | None = None) -> None:
        self._approved_prefixes: list[str] = []

    def install(self, bus: EventBus) -> None:
        bus.on("agent/tool-approval", self._on_tool_approval, priority=100)

    def _on_tool_approval(self, command: str, **_) -> bool | None:
        """审批决策：True=同意 / False=拒绝 / None=无意见（等同拒绝）。

        fail-closed 语义：返回 None 与 False 效果相同（agent 只认 True 放行）。
        """
        # 会话记忆：同前缀已允许 → 直接同意（不弹窗）
        if any(command.startswith(p) for p in self._approved_prefixes):
            return True
        # 无交互环境（评测/管道/自动化）→ 拒绝（fail-closed 双保险）
        if not sys.stdin.isatty():
            return False
        # 弹窗确认
        answer = input(
            f"[审批] 执行命令 '{command}'？(y=同意 / n=拒绝 / a=总是允许) "
        ).strip().lower()
        if answer in ("y", "a"):
            if answer == "a":
                # 会话级前缀记忆（用户决策点 5=命令前缀）：记住第一个 token
                # （`rm /tmp/a` 允许 → 同前缀 `rm` 系列放行；已知悉误放行风险）
                tokens = command.strip().split()
                self._approved_prefixes.append(tokens[0] if tokens else command)
            return True
        return False


register_plugin(
    "approval_gate",
    ApprovalGatePlugin,
    description="审批交互插件：危险命令弹窗确认（y/n/a=总是允许），非交互环境自动拒绝",
    default_enabled=True,
)
