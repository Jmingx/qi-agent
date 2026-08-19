"""安全审核插件：监听 agent/tool-call，黑名单命中拦截工具调用。

设计（方案 docs/plans/2026-08-19-安全审核插件方案.md）：
- 双防线：shell 内置拦截（硬编码）管"危险操作"；本插件（可配置）管"用户自定义限制"
- 黑名单来自 plugins.toml [security_guard.blacklist]，按工具名分组
- 拦截值遵循回填协议（principles/08）：[安全拦截] 前缀，可行动
- 默认黑名单空 = 零规则 = 行为不变（零侵入），用户配置后生效
"""

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin

# 工具名 -> 参数名映射（从 arguments 里取待审核内容）
_ARG_PARAM_MAP = {
    "shell": "command",
    "run_python": "code",
    "read_file": "path",
}


class SecurityGuardPlugin:
    """安全审核插件：黑名单命中返回拦截提示，否则放行（None）。"""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        # 黑名单：{工具名: [关键词, ...]}；默认空（零规则 = 行为不变）
        self.blacklist: dict[str, list[str]] = config.get("blacklist", {})

    def install(self, bus: EventBus) -> None:
        """注册监听器：决策类插件 priority=200（先于观测类 100 被询问）。"""
        bus.on("agent/tool-call", self._on_tool_call, priority=200)

    def _on_tool_call(self, name: str, arguments: dict, **_) -> str | None:
        """审核一次工具调用：命中黑名单返回拦截提示，否则 None（放行）。

        Args:
            name: 工具名（如 shell）
            arguments: 模型传入的参数（如 {"command": "..."}）

        Returns:
            拦截提示（[安全拦截] 前缀，回填给模型）；放行时返回 None
        """
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


# 自注册：安全底线类插件默认开（零规则 = 行为不变，配置后生效）
register_plugin(
    name="security_guard",
    factory=SecurityGuardPlugin,
    description="安全审核（黑名单拦截，plugins.toml 配置）",
    default_enabled=True,
)
