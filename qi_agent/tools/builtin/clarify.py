"""clarify 澄清提问工具：agent 向用户提问（信息澄清，非安全审批）。

设计（方案 2026-08-22-工具三件套，对齐 Hermes clarify_tool）：
- 本工具只做 schema + 校验 + 分发——实际交互委托 interaction.ask_user()
  （交互形态由注册的 InteractionProvider 决定：CLI 终端/未来 Web UI）
- fail-safe：未注册 provider / 非 tty → 返回 [交互不可用] 错误
  （不抛异常不挂死，模型换策略——对齐 approval fail-closed 哲学）
- 边界：审批弹窗（y/n/a 安全确认）→ approval_gate；信息澄清 → 本工具
"""

from qi_agent.interaction import InteractionUnavailableError, ask_user
from qi_agent.tools.registry import register

# 选项上限（对齐 Hermes MAX_CHOICES=4，UI 自动追加"其他"）
_MAX_CHOICES = 4


def clarify(question: str, choices: list[str] | None = None,
            timeout: float = 60.0) -> str:
    """向用户澄清问题（同步等待回答，回答作为工具结果回填）。

    Args:
        question: 问题文本
        choices: 选项列表（≤4，None = 开放式提问）
        timeout: 等待秒数（评测等无交互环境自动 fail-safe）

    Returns:
        用户回答；或 [交互不可用] 错误提示（fail-safe，不挂死）
    """
    try:
        return ask_user(question, choices=choices, timeout=timeout)
    except InteractionUnavailableError as exc:
        # fail-safe：无交互环境返回可行动错误，模型自行决策
        return (
            f"[交互不可用] {exc}。当前环境无法向用户提问，"
            "请基于已有信息自行决策，或换用其他工具"
        )


register(
    name="clarify",
    toolset="builtin",
    handler=clarify,
    description=(
        "向用户提问澄清（信息交互，非审批）。用于信息不足/选项歧义时"
        "询问用户。纯交互无副作用——不用审批。"
        "【边界】安全确认类（y/n）走审批弹窗；本工具是自由信息澄清"
    ),
    # 审批声明：纯提问无副作用 → 放行（None）
    approval=None,
    # 手写 schema：choices 是 array（list[str]）——自动生成只支持标量
    schema={
        "type": "function",
        "function": {
            "name": "clarify",
            "description": (
                "向用户提问澄清（同步等待用户回答，回答直接返回给你）。"
                "当信息不足、选项歧义、或用户意图不明确时使用；"
                "可提供最多 4 个选项让用户选择，或开放式提问"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要问用户的问题",
                    },
                    "choices": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "候选选项（最多 4 个）；省略 = 开放式提问",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "等待秒数（默认 60）",
                    },
                },
                "required": ["question"],
            },
        },
    },
)
