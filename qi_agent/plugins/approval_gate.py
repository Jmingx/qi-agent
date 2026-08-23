"""审批交互插件：监听 agent/tool-approval，弹窗确认 + 会话级前缀记忆。

方案：docs/plans/2026-08-20-shell三档权限与审批机制方案.md
分层：工具管执行 · 插件管决策（security_guard 判档）· 插件管交互（本插件弹窗）
设计（业界对照）：
- fail-closed：无监听器 / 交互不可用（非 tty 评测、管道）→ 自动拒绝（Hermes deny fast）
- 会话级记忆：a=总是允许 → 记住命令【前缀】→ 同前缀命令不再弹窗
  （用户决策点 5：命令前缀，非精确——体验优先，已明示误放行风险）
- 红线不进审批：security_guard 在 tool-call 层已硬拒，本插件只处理 NEED_APPROVAL 档

交互统一（2026-08-23 排查修正）：input() 硬编码 → InteractionProvider 抽象层
（ask_user）——与 clarify 同一交互通道，未来 Web/GUI 换实现本插件零改动。
"""

from qi_agent.events import EventBus
from qi_agent.interaction import InteractionUnavailableError, ask_user
from qi_agent.plugins.registry import register_plugin


class ApprovalGatePlugin:
    """审批交互插件：弹窗确认（y/n/a=总是允许）+ 会话级前缀记忆。"""

    def __init__(self, config: dict | None = None) -> None:
        self._approved_prefixes: list[str] = []

    def install(self, bus: EventBus) -> None:
        bus.on("agent/tool-approval", self._on_tool_approval, priority=100)

    def _on_tool_approval(self, command: str, name: str | None = None, **_) -> bool | None:
        """审批决策：True=同意 / False=拒绝 / None=无意见（等同拒绝）。

        fail-closed 语义：返回 None 与 False 效果相同（agent 只认 True 放行）。
        沙箱相关审批（v0.4.23，弹窗透明）不提供 a=总是允许：
        - name=run_python：沙箱降级（完整 Python 执行该次代码）
        - command 前缀 "沙箱升级:"：shell 代码执行命令（完整权限，不受沙箱约束）
        总允许 = 变相恢复全局放行，与逐次确认的安全语义冲突。
        """
        # 会话记忆：同前缀已允许 → 直接同意（不弹窗；沙箱相关档无 a 不记忆）
        if name != "run_python" and not command.startswith("沙箱升级:") and any(
            command.startswith(p) for p in self._approved_prefixes
        ):
            return True
        # 交互走抽象层（2026-08-23）：ask_user 内部检查 isatty，非 tty 抛
        # InteractionUnavailableError → fail-closed 拒绝（评测/管道双保险）
        try:
            if name == "run_python":
                # 沙箱降级弹窗：逐次确认（用户决策点 3：不提供 a=总是允许）
                answer = ask_user(
                    f"[审批] 降级沙箱安全等级（完整 Python 执行）？{command}",
                    choices=["y", "n"],
                )
            elif command.startswith("沙箱升级:"):
                # shell 代码执行命令弹窗（v0.4.23 弹窗透明）：明确告知完整权限
                real_command = command.split(":", 1)[1]
                answer = ask_user(
                    f"[审批] ⚠️ 命令以完整权限执行（不受沙箱约束），确认升级沙箱权限？\n"
                    f"命令: {real_command}",
                    choices=["y", "n"],
                )
            else:
                # 弹窗确认（shell/write_file 等现状）
                answer = ask_user(
                    f"[审批] 执行命令 '{command}'？",
                    choices=["y", "n", "a"],
                )
        except InteractionUnavailableError:
            return False  # fail-closed：交互不可用 → 拒绝
        answer = str(answer).strip().lower()
        if answer == "y":
            return True
        if answer == "a" and name != "run_python" and not command.startswith("沙箱升级:"):
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
