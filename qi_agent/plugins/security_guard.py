"""安全审核插件：监听 agent/tool-call，黑名单命中拦截工具调用。

设计（方案 docs/plans/2026-08-19-安全审核插件方案.md）：
- 双防线：shell 内置拦截（硬编码）管"危险操作"；本插件（可配置）管"用户自定义限制"
- 黑名单来自 plugins.toml [security_guard.blacklist]，按工具名分组
- 拦截值遵循回填协议（principles/08）：[安全拦截] 前缀，可行动
- 默认黑名单空 = 零规则 = 行为不变（零侵入），用户配置后生效
"""

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin
from qi_agent.tools.path_security import is_sensitive_path

# 工具名 -> 参数名映射（从 arguments 里取待审核内容）
_ARG_PARAM_MAP = {
    "shell": "command",
    "run_python": "code",
    "read_file": "path",
}

# 需审批档：危险但可审的命令前缀（三档中的②——Claude Code ask 借鉴）
# 命中 → NEED_APPROVAL 标记 → agent 发审批事件 → approval_gate 弹窗
_APPROVAL_PREFIXES = (
    "rm ", "rmdir ", "shutdown", "reboot", "git push", "git reset --hard",
    "git checkout --", "del ", "rd ", "format ", "taskkill",
    "net user", "reg delete", "start ",
)


class SecurityGuardPlugin:
    """安全审核插件：黑名单命中返回拦截提示，否则放行（None）。

    两层规则（方案 v0.4.11）：
    ① 用户配置黑名单（plugins.toml，关键词子串匹配）
    ② 内置路径规则（安全底线硬编码，始终生效——修复 shell 读 .git 绕过漏洞）
    """

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        # 黑名单：{工具名: [关键词, ...]}；默认空（零规则 = 行为不变）
        self.blacklist: dict[str, list[str]] = config.get("blacklist", {})

    def install(self, bus: EventBus) -> None:
        """注册监听器：决策类插件 priority=200（先于观测类 100 被询问）。"""
        bus.on("agent/tool-call", self._on_tool_call, priority=200)

    def _on_tool_call(self, name: str, arguments: dict, **_) -> str | None:
        """审核一次工具调用：三档判定（v0.4.18）。

        Args:
            name: 工具名（如 shell）
            arguments: 模型传入的参数（如 {"command": "..."}）

        Returns:
            - [安全拦截] 前缀：红线硬拒（回填模型）
            - NEED_APPROVAL:<命令>：需审批档（agent 发审批事件）
            - None：放行（白名单命令或非 shell 工具）
        """
        # ③ 红线优先：黑名单 + 路径规则 → 硬拒（不可审批，业界共识）
        hit = self._check_blacklist(name, arguments)
        if hit:
            return hit
        hit = self._check_sensitive_path(name, arguments)
        if hit:
            return hit
        # ② 需审批档（仅 shell 命令）
        if name == "shell":
            command = str(arguments.get("command", ""))
            lowered = command.lower().lstrip()
            if any(lowered.startswith(p) for p in _APPROVAL_PREFIXES):
                return f"NEED_APPROVAL:{command}"
        # ① 放行（白名单命令由 shell 工具层执行）
        return None

    def _check_blacklist(self, name: str, arguments: dict) -> str | None:
        """黑名单关键词匹配（子串 + 小写，对齐 shell 内置拦截风格）。"""
        keywords = self.blacklist.get(name, [])
        if not keywords:
            return None  # 该工具未配置规则 → 放行
        param = _ARG_PARAM_MAP.get(name)
        if param is None or param not in arguments:
            return None  # 未知工具/参数缺失 → 放行（防御性，不误伤）
        content = str(arguments[param]).lower()
        for keyword in keywords:
            if keyword.lower() in content:
                return (
                    f"[安全拦截] {name} 内容包含危险关键词: '{keyword}'，"
                    f"已拒绝执行"
                )
        return None

    def _check_sensitive_path(self, name: str, arguments: dict) -> str | None:
        """内置路径规则：shell 命令中的路径 token 命中敏感路径 → 拦截。

        修复真实对抗暴露的绕过（v0.4.10）：模型通过 type .git\\config 读取
        敏感文件——path_security 只接入了 read_file，shell 没接。
        token 化：去引号 + 空格拆分（安全优先，宁可误伤）。
        """
        if name != "shell":
            return None
        cmd = str(arguments.get("command", ""))
        tokens = cmd.replace('"', "").split()
        for token in tokens:
            if is_sensitive_path(token):
                return (
                    f"[安全拦截] shell 命令包含敏感路径: '{token}'，"
                    f"已拒绝执行"
                )
        return None


# 自注册：安全底线类插件默认开（零规则 = 行为不变，配置后生效）
register_plugin(
    name="security_guard",
    factory=SecurityGuardPlugin,
    description="安全审核（黑名单拦截+敏感路径拦截，plugins.toml 配置）",
    default_enabled=True,
)
