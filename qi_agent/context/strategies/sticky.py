"""sticky 挂载策略（策略链）：确保 system 含 sticky 区（幂等）。

永不消费（不阻止后续策略）——sticky 挂载是恒常操作，裁剪/压缩在
它之后继续执行。
"""

from qi_agent.context.sticky import _STICKY_HEADER, get_sticky_text
from qi_agent.context.strategies.registry import register_strategy
from qi_agent.context.strategies.base import ContextInfo, ContextStrategy


@register_strategy
class StickyStrategy(ContextStrategy):
    """sticky 挂载：用户要求保留的信息永不裁剪（在 system 里免疫）。"""

    name = "sticky"

    def should_apply(self, ctx: ContextInfo) -> bool:
        return bool(get_sticky_text())

    def apply(self, messages: list[dict], ctx: ContextInfo
              ) -> tuple[list[dict], bool]:
        if not messages:
            return messages, False
        first = messages[0]
        if first.get("role") != "system":
            return messages, False
        if _STICKY_HEADER in first.get("content", ""):
            return messages, False  # 已挂载（幂等）
        updated = dict(first)
        updated["content"] = first.get("content", "") + "\n\n" + get_sticky_text()
        return [updated] + messages[1:], False  # 不消费（后续策略继续）
