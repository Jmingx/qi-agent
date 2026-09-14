"""审批交互插件：监听 agent/tool-approval，弹窗确认 + 会话级前缀记忆。

方案：docs/plans/2026-08-20-shell三档权限与审批机制方案.md
      docs/plans/2026-09-13-Web权限审批弹框方案.md（选项语义化 + 会话身份）
分层：工具管执行 · 插件管决策（security_guard 判档）· 插件管交互（本插件）

设计（2026-09-13 改造）：
- **选项语义化**：once / session / deny + 中文 label——决策层给语义，外壳
  只管渲染（Web 弹框不再硬编码 y/n/a 映射）。
- **档位裁剪**：沙箱降级 / 沙箱升级档只给 once / deny——"总是允许"= 变相
  恢复全局放行，与逐次确认的安全语义冲突。
- **会话身份**：install 时从 `bus.context_id` 取（`AgentContext` 构造即绑定），
  随 meta 下发给图形外壳——弹框能回到发起它的会话，且**内核执行路径零改动**。
- **fail-closed**：无监听器 / 交互不可用（非 tty / 无 provider / 超时）→ 拒绝。
- 红线不进审批：security_guard 在 tool-call 层已硬拒（本插件只处理 NEED_APPROVAL）。
"""

from qi_agent.events import EventBus
from qi_agent.interaction import (
    InteractionOption,
    InteractionUnavailableError,
    ask_user,
)
from qi_agent.plugins.registry import register_plugin
from qi_agent.tools.decision import (
    SEC_APPROVAL_ESCALATION,
    SEC_APPROVAL_SANDBOX,
)

# 选项集（决策层唯一来源）：文案即前端按钮文案
_OPTION_ONCE = InteractionOption(value="once", label="允许一次", tone="primary")
_OPTION_SESSION = InteractionOption(value="session", label="本会话总是允许该前缀", tone="muted")
_OPTION_DENY = InteractionOption(value="deny", label="拒绝", tone="danger")
_OPTIONS_ALL = [_OPTION_ONCE, _OPTION_SESSION, _OPTION_DENY]
_OPTIONS_ONCE_DENY = [_OPTION_ONCE, _OPTION_DENY]

# 不提供"总是允许"的档位（沙箱相关：逐次确认，防变相全局放行）
_NO_SESSION_CODES = (SEC_APPROVAL_SANDBOX, SEC_APPROVAL_ESCALATION)


class ApprovalGatePlugin:
    """审批交互插件：弹窗确认（once/session/deny）+ 会话级前缀记忆。"""

    def __init__(self, config: dict | None = None) -> None:
        self._approved_prefixes: list[str] = []
        self._session = ""  # 会话身份（bus.context_id，Web 弹框归属用）

    def install(self, bus: EventBus) -> None:
        # 会话身份取自总线（AgentContext 构造时绑定 events.context_id）——
        # 决策者本来就知道自己在哪个会话的总线上提问，无需线程本地状态。
        self._session = getattr(bus, "context_id", "") or ""
        bus.on("agent/tool-approval", self._on_tool_approval, priority=100)

    def _on_tool_approval(self, command: str, name: str | None = None,
                          code: str = "", arguments: dict | None = None,
                          tool_call_id: str = "", turn: int | None = None,
                          **_) -> bool | None:
        """审批决策：True=同意 / False=拒绝 / None=无意见（等同拒绝）。

        fail-closed 语义：返回 None 与 False 效果相同（agent 只认 True 放行）。
        """
        # 会话记忆：同前缀已允许 → 直接同意（不弹窗；沙箱档不记忆）
        if code not in _NO_SESSION_CODES and any(
            command.startswith(p) for p in self._approved_prefixes
        ):
            return True
        question, options = self._build_prompt(code, command)
        meta = {
            "session": self._session,
            "tool": name or "",
            "code": code,
            "command": command,
            "arguments": arguments or {},
            # 前端据此把审批绑到"触发它的那一行工具"（M2-a 内联记录）
            "tool_call_id": tool_call_id,
            # 定位所属回合（前端 UI 的回合容器是审批行的宿主）
            "turn": turn,
        }
        try:
            answer = str(ask_user(question, options, meta=meta)).strip().lower()
        except InteractionUnavailableError:
            return False  # fail-closed：交互不可用 → 拒绝
        if answer == "once":
            return True
        if answer == "session" and code not in _NO_SESSION_CODES:
            # 会话级前缀记忆（用户决策点 5=命令前缀）：记住第一个 token
            # （`rm /tmp/a` 允许 → 同前缀 `rm` 系列放行；已知悉误放行风险）
            tokens = command.strip().split()
            self._approved_prefixes.append(tokens[0] if tokens else command)
            return True
        return False

    @staticmethod
    def _build_prompt(code: str, command: str) -> tuple[str, list[InteractionOption]]:
        """按档位生成问题文案与选项集（弹窗透明：风险与权限范围说清楚）。"""
        if code == SEC_APPROVAL_SANDBOX:
            # run_python 降级弹窗：逐次确认（不提供 session）
            return (
                f"[审批] 降级沙箱安全等级（完整 Python 执行）？{command}",
                _OPTIONS_ONCE_DENY,
            )
        if code == SEC_APPROVAL_ESCALATION:
            # shell 代码执行命令：明确告知以完整权限执行（不受沙箱约束）
            return (
                "[审批] ⚠️ 命令以完整权限执行（不受沙箱约束），确认升级沙箱权限？\n"
                f"命令: {command}",
                _OPTIONS_ONCE_DENY,
            )
        # 常规档（危险命令 / 覆盖写 / 越界写 / 删文件）
        return f"[审批] 执行命令 '{command}'？", _OPTIONS_ALL


register_plugin(
    "approval_gate",
    ApprovalGatePlugin,
    description="审批交互插件：危险命令弹窗确认（允许一次/本会话总是允许/拒绝），非交互环境自动拒绝",
    default_enabled=True,
)
