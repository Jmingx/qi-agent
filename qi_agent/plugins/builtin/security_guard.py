"""安全审核插件：监听 agent/tool-call，黑名单命中拦截工具调用。

设计（方案 docs/plans/2026-08-19-安全审核插件方案.md）：
- 双防线：shell 内置拦截（硬编码）管"危险操作"；本插件（可配置）管"用户自定义限制"
- 黑名单来自 plugins.toml [security_guard.blacklist]，按工具名分组
- 拦截值遵循回填协议（principles/08）：[安全拦截] 前缀，可行动
- 默认黑名单空 = 零规则 = 行为不变（零侵入），用户配置后生效
"""

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin
from qi_agent.security.path_security import is_sensitive_path
from qi_agent.security.rules import HARDLINE_PREFIXES, TOOL_APPROVAL_RULES
from qi_agent.tools.decision import (
    SEC_APPROVAL_GENERAL,
    SEC_BLOCK_BLACKLIST,
    SEC_BLOCK_REDLINE,
    SEC_BLOCK_SENSITIVE,
    ToolAction,
    ToolDecision,
)
from qi_agent.tools.registry import get_tool

# 工具名 -> 参数名映射（从 arguments 里取待审核内容）
_ARG_PARAM_MAP = {
    "shell": "command",
    "run_python": "code",
    "read_file": "path",
    "write_file": "path",
}


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

    def _on_tool_call(self, name: str, arguments: dict, **_) -> "ToolDecision | None":
        """审核一次工具调用（决策码机制 2026-08-23）。

        Args:
            name: 工具名（如 shell）
            arguments: 模型传入的参数（如 {"command": "..."}）

        Returns:
            ToolDecision：BLOCK（硬拒）/ NEED_APPROVAL（审批）/
              ESCALATION（沙箱升级独立档）；None = 放行
        """
        # ③ 红线优先：黑名单 + 路径规则 → 硬拒（不可审批，业界共识）
        hit = self._check_blacklist(name, arguments)
        if hit:
            return ToolDecision(ToolAction.BLOCK, reason=hit,
                                code=SEC_BLOCK_BLACKLIST)
        hit = self._check_sensitive_path(name, arguments)
        if hit:
            return ToolDecision(ToolAction.BLOCK, reason=hit,
                                code=SEC_BLOCK_SENSITIVE)
        # ③b 红线前缀（format/shutdown 等，v0.4.21）：插件层直接硬拒——
        # 必须在审批档【之前】检查，否则 approved 绕过工具层后仍可执行。
        # 红线是系统级底线（不可审批），不随工具声明——工具注册表可能被
        # 动态修改，底线必须硬编码在插件（安全底线哲学）
        if name == "shell":
            command = str(arguments.get("command", ""))
            lowered = command.lower().lstrip()
            if any(lowered.startswith(p) for p in HARDLINE_PREFIXES):
                return ToolDecision(
                    ToolAction.BLOCK,
                    reason=f"命令属于红线操作（不可执行）: {command}",
                    code=SEC_BLOCK_REDLINE,
                    command=command,
                )
        # ④ 工具级审批声明（v0.4.26 声明式判档 + v0.4.27 规则化）：查
        # registry 的 ToolEntry.approval——工具注册时自声明权限策略。
        # 字符串优先查规则表（TOOL_APPROVAL_RULES，条件逻辑单一数据源），
        # 未命中再当模板（无条件审批）。插件查表执行，零工具名分支。
        # 边界：系统级底线（黑名单/敏感路径/红线）永远优先于工具声明
        rule = get_tool(name).approval if get_tool(name) else None
        if rule is not None:
            if callable(rule):
                desc = rule(arguments)
            elif rule in TOOL_APPROVAL_RULES:
                desc = TOOL_APPROVAL_RULES[rule](arguments)
            else:
                try:
                    desc = rule.format(**arguments)
                except (KeyError, IndexError):
                    desc = rule  # 缺参/占位符不匹配 → 回退模板本身
            if desc:
                # 结构化决策（ESCALATION/SANDBOX 等）原样透传；
                # 字符串描述包成普通审批档（SEC_APPROVAL_GENERAL）
                if isinstance(desc, ToolDecision):
                    return desc
                return ToolDecision(
                    ToolAction.NEED_APPROVAL,
                    reason=desc,
                    code=SEC_APPROVAL_GENERAL,
                    command=desc,
                )
        # ① 放行（白名单命令由工具层执行；项目内新增文件自动写入）
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
        """内置路径规则：工具参数中的路径命中敏感路径 → 拦截。

        修复真实对抗暴露的绕过（v0.4.10）：模型通过 type .git\\config 读取
        敏感文件——path_security 只接入了 read_file，shell 没接。
        - shell：命令 token 化（去引号 + 空格拆分，安全优先宁可误伤）
        - 带 path 参数的工具（read_file/write_file，v0.4.19）：直接检查路径
        """
        if name == "shell":
            cmd = str(arguments.get("command", ""))
            tokens = cmd.replace('"', "").split()
            for token in tokens:
                if is_sensitive_path(token):
                    return (
                        f"[安全拦截] shell 命令包含敏感路径: '{token}'，"
                        f"已拒绝执行"
                    )
            return None
        param = _ARG_PARAM_MAP.get(name)
        if param and param in arguments:
            if is_sensitive_path(str(arguments[param])):
                return (
                    f"[安全拦截] {name} 目标为敏感路径，已拒绝执行"
                )
        return None


# 自注册：安全底线类插件默认开（零规则 = 行为不变，配置后生效）
register_plugin(
    name="security_guard",
    factory=SecurityGuardPlugin,
    description="安全审核（黑名单拦截+敏感路径拦截，plugins.toml 配置）",
    default_enabled=True,
)
