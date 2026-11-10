"""window 滑动裁剪策略（策略链）：超预算裁最旧（保底）。

消费语义：裁剪后责任链停止（裁剪是兜底动作——通常排链尾）。
"""

from qi_agent.context.strategies.registry import register_strategy
from qi_agent.context.strategies.base import ContextInfo, ContextStrategy
from qi_agent.context.window import trim_messages


@register_strategy
class WindowStrategy(ContextStrategy):
    """滑动窗口裁剪：消息组 + token 预算 + role 交替 + anchor（L1）。"""

    name = "window"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        # 裁剪预算（None/0 禁用）
        self.budget: int | None = config.get("budget", 100_000)

    def should_apply(self, ctx: ContextInfo) -> bool:
        return self.budget is not None and self.budget > 0

    def apply(self, messages: list[dict], ctx: ContextInfo
              ) -> tuple[list[dict], bool]:
        messages, trimmed = trim_messages(messages, self.budget)
        if trimmed:
            print(f"[CTX] 已裁剪 {trimmed} 组早期历史（预算 {self.budget} tokens）")
            return messages, True
        return messages, False
