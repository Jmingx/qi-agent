"""工具决策码机制（方案 2026-08-23）：结构化决策替代字符串约定。

对齐业界（Hermes action 字典 / DSH 类型协议 / CC PermissionResult）：
- ToolAction：决策码枚举（agent 循环按 action 分发，不再 startswith 字符串）
- ToolDecision：结构化决策（action + reason 回填文本 + code 错误码 + command 审批上下文）
- 判档（security_guard）→ 分发（agent）→ 交互（approval_gate）三方解耦

错误码语义：SEC_ 前缀（安全域）；BLOCK 系不弹窗，APPROVAL 系走审批。
"""

from dataclasses import dataclass, field
from enum import Enum


class ToolAction(str, Enum):
    """工具调用决策码（可扩展：新增档位只加枚举 + agent 分发分支）。"""

    ALLOW = "allow"              # 放行执行
    BLOCK = "block"              # 硬拒（不弹窗——红线/黑名单/敏感路径）
    NEED_APPROVAL = "need_approval"  # 需审批（弹窗确认）
    WARN = "warn"                # 警告放行（执行 + 结果附警告）
    ESCALATION = "escalation"    # 沙箱升级（shell 代码执行——独立档，弹窗透明）


# ── 错误码（SEC_ 前缀；日志/统计/测试断言用） ────────────────────────────

SEC_BLOCK_BLACKLIST = "SEC_BLOCK_BLACKLIST"       # 黑名单命中
SEC_BLOCK_REDLINE = "SEC_BLOCK_REDLINE"           # 红线前缀（format/shutdown 等）
SEC_BLOCK_SENSITIVE = "SEC_BLOCK_SENSITIVE"       # 敏感路径（.env/.git）
SEC_BLOCK_PATH = "SEC_BLOCK_PATH"                 # 路径规则拦截（8.3/越界）
SEC_APPROVAL_GENERAL = "SEC_APPROVAL_GENERAL"     # 普通审批档
SEC_APPROVAL_SANDBOX = "SEC_APPROVAL_SANDBOX"     # 沙箱降级（run_python import 白名单外）
SEC_APPROVAL_ESCALATION = "SEC_APPROVAL_ESCALATION"  # 沙箱升级（shell 代码执行）
SEC_WARN_EXEC = "SEC_WARN_EXEC"                   # 警告放行（有风险但允许执行）


@dataclass
class ToolDecision:
    """结构化工具决策（判档插件返回，agent 按 action 分发）。"""

    action: ToolAction
    reason: str = ""              # 人类可读说明（回填给模型的文本）
    code: str = ""                # 错误码（SEC_*，见上）
    command: str = ""             # 审批交互上下文（如命令原文）
    extra: dict = field(default_factory=dict)  # 扩展字段（未来档位用）
