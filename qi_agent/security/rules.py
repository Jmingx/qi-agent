"""命令权限规则统一来源：红线 / 审批档 / 代码执行档（单一数据源）。

背景（方案 2026-08-22-权限规则统一方案）：规则曾散落 4 处（shell.py
_DANGEROUS_KEYWORDS / security_guard._HARDLINE_PREFIXES / _APPROVAL_PREFIXES /
_CODE_EXEC_PREFIXES）——同一命令多份维护，改一处漏一处（npm 硬拒缺口、
v0.4.21 红线差点漏）。收敛为单一数据源，各层（shell 工具层兜底 /
security_guard 插件判档）import 引用。

设计说明：
- 本模块只放"命令权限规则"（前缀/关键词）——run_python 的代码模式检测
  （_FORBIDDEN_PATTERNS）与受限模块白名单（_RESTRICTED_MODULES）是沙箱
  内容策略，与命令权限正交，不在此列（远期 DSH 声明式再统合）
- 检测逻辑不在此模块——shell 工具层兜底与 security_guard 插件判档各自
  实现如何使用这些规则（安全底线哲学：插件可关（plugins.toml），工具层
  兜底不可关——两层独立，数据共享）
- 代码执行类命令（CODE_EXEC_PREFIXES）不在 APPROVAL_PREFIXES 重复——
  判档时代码执行档先于普通审批档，重复条目是永远不命中的死代码
"""

from typing import Any, Callable

# 注意：本模块【不】import qi_agent.tools.decision——tools 包初始化会触发
# security.rules（shell 工具 import 本模块），若 rules 反向依赖 tools.decision
# 则循环导入。规则函数返回值用 Any（ToolDecision 由调用方判别，见 security_guard）。

# 红线前缀（不可审批、不可执行——删库跑路 + 重启关机）：
# 工具层硬拒 + 插件层直接 [安全拦截]（不产生 NEED_APPROVAL → 永远无
# approved 可注入）。v0.4.21 教训：只放工具层不够，审批同意路径会跳过工具层
HARDLINE_PREFIXES = (
    "format", "mkfs", "dd ", "shutdown", "reboot",
)

# 组合命令语法（shell 专属：前缀检测盲区兜底——echo a | rm -rf / 组合里的
# rm 抓不到前缀，组合语法本身硬拒）
SHELL_COMBINATOR_SYNTAX = (
    ">", ">>", "|", "&&", ";",
)

# 需审批档：危险但可审的命令前缀（Claude Code ask 借鉴）——
# 命中 → NEED_APPROVAL 标记 → agent 发审批事件 → approval_gate 弹窗
# 边界（方案 2026-08-20-审批档边界调整）：rm/del/curl/wget 等可审批；
# shutdown/format 等红线不在此列。python/py/npm/pip/npx/node 见
# CODE_EXEC_PREFIXES（代码执行档先判定，此处不重复）
APPROVAL_PREFIXES = (
    "rm ", "rmdir ", "git push", "git reset --hard",
    "git checkout --", "del ", "rd ", "taskkill",
    "net user", "reg delete", "start ", "curl", "wget",
)

# 代码执行类命令（v0.4.23 弹窗透明）：shell 里跑这类命令 = 以完整权限执行，
# 不受 Python 沙箱（run_python）约束——判为【沙箱升级】档（NEED_APPROVAL:沙箱升级:），
# 弹窗明确告知"完整权限/绕沙箱"（Hermes/CC 都只是描述引导，无此透明层）。
# 判档必须先于 APPROVAL_PREFIXES 检查（沙箱升级优先）
CODE_EXEC_PREFIXES = (
    "python", "py ", "node", "npm", "pip", "npx",
)

# ────────────────────────────────────────────────────────────
# 工具级审批规则表（v0.4.27 声明式判档的规则化——单一数据源）
#
# 背景：delegate_task 的审批条件曾硬编码在工具文件（lambda），
# 与"规则统一来源"（本模块定位）矛盾——条件逻辑散落工具层。
# 收敛：工具注册时只声明规则名（approval="subagent"），
# security_guard 查本表拿判档函数，条件逻辑全部集中在此。
#
# 语义：键 = 规则名（工具 approval 字段引用），值 = 判档函数
#   (arguments: dict) -> str | ToolDecision | None
#   - 返回 None         → 放行（不弹窗）
#   - 返回 str          → 普通审批档（SEC_APPROVAL_GENERAL，弹窗）
#   - 返回 ToolDecision → 结构化决策（原样透传，可携带 code/action）
# 与命令前缀规则（上方）正交：那是 shell 命令，这是工具参数。
# 类型注解用 Any（ToolDecision 在 tools 包，本模块不 import 避免循环导入）
TOOL_APPROVAL_RULES: dict[str, Callable[[dict], Any]] = {}


def tool_approval_rule(name: str):
    """注册一个工具级审批规则（装饰器）。"""

    def decorator(fn: Callable[[dict], Any]) -> Callable:
        if name in TOOL_APPROVAL_RULES:
            raise ValueError(f"工具审批规则 '{name}' 重复注册")
        TOOL_APPROVAL_RULES[name] = fn
        return fn

    return decorator


# subagent 规则（方案 2026-08-23-subagent）：
# - 纯只读委派（无 write_paths）→ 子 agent 无写权限+无危险工具 → 放行
# - 带写权限委派（write_paths 非空）→ 用户背书（弹框审批，先问再给）
@tool_approval_rule("subagent")
def _rule_subagent(arguments: dict) -> str | None:
    write_paths = arguments.get("write_paths")
    if not write_paths:
        return None  # 纯只读委派 → 放行
    return (
        f"委派子任务给 subagent（goal={str(arguments.get('goal', ''))[:50]}）"
        f"——子 agent 将获得写权限: {write_paths}"
    )
