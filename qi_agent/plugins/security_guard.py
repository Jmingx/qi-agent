"""安全审核插件：监听 agent/tool-call，黑名单命中拦截工具调用。

设计（方案 docs/plans/2026-08-19-安全审核插件方案.md）：
- 双防线：shell 内置拦截（硬编码）管"危险操作"；本插件（可配置）管"用户自定义限制"
- 黑名单来自 plugins.toml [security_guard.blacklist]，按工具名分组
- 拦截值遵循回填协议（principles/08）：[安全拦截] 前缀，可行动
- 默认黑名单空 = 零规则 = 行为不变（零侵入），用户配置后生效
"""

import os
import re

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin
from qi_agent.tools.path_security import is_sensitive_path
from qi_agent.tools.write_file import _is_inside_project

# 工具名 -> 参数名映射（从 arguments 里取待审核内容）
_ARG_PARAM_MAP = {
    "shell": "command",
    "run_python": "code",
    "read_file": "path",
    "write_file": "path",
}

# 受限环境可 import 的模块（对齐 _sandbox_runner._ALLOWED_EXTRA_MODULES 默认值）
# run_python 降级判据：import 白名单外模块 → NEED_APPROVAL（v0.4.23 审批档）
_RESTRICTED_MODULES = (
    "math", "random", "json", "statistics", "fractions", "decimal",
)

# 需审批档：危险但可审的命令前缀（三档中的②——Claude Code ask 借鉴）
# 命中 → NEED_APPROVAL 标记 → agent 发审批事件 → approval_gate 弹窗
# 边界（方案 2026-08-20-审批档边界调整）：rm/del/curl/wget 等用户可审批；
# shutdown/format 等红线不在此列（见 _HARDLINE_PREFIXES）
_APPROVAL_PREFIXES = (
    "rm ", "rmdir ", "git push", "git reset --hard",
    "git checkout --", "del ", "rd ", "taskkill",
    "net user", "reg delete", "start ", "python", "py",
    "npm", "pip", "npx", "curl", "wget",
)

# 红线前缀（不可审批、不可执行——插件层直接硬拒，先于审批档判定）：
# 必须在插件层生效（实现时发现）：若只靠 shell 工具层硬拒，审批同意后
# approved=True 会跳过工具层 → format/shutdown 仍可执行（漏洞）
_HARDLINE_PREFIXES = (
    "format", "mkfs", "dd ", "shutdown", "reboot",
)

# 代码执行类命令（v0.4.23 弹窗透明）：shell 里跑这类命令 = 以完整权限执行，
# 不受 Python 沙箱（run_python）约束——判为【沙箱升级】档（NEED_APPROVAL:沙箱升级:），
# 弹窗明确告知"完整权限/绕沙箱"（Hermes/CC 都只是描述引导，无此透明层）。
# 注意：python/py 也在 _APPROVAL_PREFIXES——必须先于此档判定（沙箱升级优先）。
_CODE_EXEC_PREFIXES = (
    "python", "py ", "node", "npm", "pip", "npx",
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
        # ③b 红线前缀（format/shutdown 等，v0.4.21）：插件层直接硬拒——
        # 必须在审批档【之前】检查，否则 approved 绕过工具层后仍可执行
        if name == "shell":
            command = str(arguments.get("command", ""))
            lowered = command.lower().lstrip()
            if any(lowered.startswith(p) for p in _HARDLINE_PREFIXES):
                return f"[安全拦截] 命令属于红线操作（不可执行）: {command}"
        # ② 沙箱升级档（v0.4.23，先于普通审批档）：代码执行类命令（python/
        # py/node/npm/pip 等）→ 弹窗告知"完整权限（不受沙箱约束）"——用户
        # 批准 = 显式沙箱升级（DSH approveBashEscalation 同款语义）
        if name == "shell":
            command = str(arguments.get("command", ""))
            lowered = command.lower().lstrip()
            if any(lowered.startswith(p) for p in _CODE_EXEC_PREFIXES):
                return f"NEED_APPROVAL:沙箱升级:{command}"
        # ② 需审批档（仅 shell 命令）
        if name == "shell":
            command = str(arguments.get("command", ""))
            lowered = command.lower().lstrip()
            if any(lowered.startswith(p) for p in _APPROVAL_PREFIXES):
                return f"NEED_APPROVAL:{command}"
        # write_file 四档（v0.4.19）：红线已在上方路径规则检查（_ARG_PARAM_MAP
        # 含 write_file→path）；这里补覆盖/越界审批档
        if name == "write_file":
            path = str(arguments.get("path", ""))
            if os.path.exists(path):
                return f"NEED_APPROVAL:覆盖写入 {path}"
            if not _is_inside_project(path):
                return f"NEED_APPROVAL:项目外写入 {path}"
        # run_python 沙箱降级档（v0.4.23，环境变量开关退役）：import 白名单外
        # 模块 → 审批弹窗（对齐 shell 三档）——用户批准后 approved 注入走完整 Python
        if name == "run_python":
            code = str(arguments.get("code", ""))
            need = self._needs_sandbox_downgrade(code)
            if need:
                return f"NEED_APPROVAL:{need}"
        # ① 放行（白名单命令由工具层执行；项目内新增文件自动写入）
        return None

    @staticmethod
    def _needs_sandbox_downgrade(code: str) -> str | None:
        """run_python 代码是否需要沙箱降级：import 受限白名单外模块。

        restricted 环境只放行 _RESTRICTED_MODULES；需要其他模块的代码
        → NEED_APPROVAL 弹窗（降级=用户逐次批准，环境变量静默降级已退役）。
        """
        for m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M):
            module = m.group(1).split(".")[0]
            if module not in _RESTRICTED_MODULES:
                return f"代码需要 import '{module}'（受限环境白名单外），需降级审批"
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
