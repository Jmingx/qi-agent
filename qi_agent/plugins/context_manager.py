"""上下文管理插件（2026-08-22 用户架构修正）：上下文管理统一入口。

设计：本插件是"编排器"（策略分发 + 事件挂载），不写算法——
算法在 context/ 子包（window 滑动裁剪 / compressor 摘要压缩 /
inject 注入 / estimator 估算），换算法不碰插件（config 选策略）：

  [context_manager]
  enabled = true
  budget = 100000          # 裁剪预算（None 禁用）
  strategy = window        # window（滑动裁剪）| summarize（摘要压缩，阶段 C）

事件挂载：
  agent/pre-step  → 改写历史（裁剪/压缩——超预算/超阈值才动）
  agent/pre-llm   → 注入（sticky/todo 上下文，幂等）
  agent/post-llm  → usage 累计 → should_compress 检查（阶段 C）

设计说明：对齐 Hermes ContextEngine——一个引擎承载上下文管理的
全生命周期（感知 → 决策 → 行动），算法可插拔。
"""

from qi_agent.context.sticky import get_sticky_text
from qi_agent.context.window import trim_messages
from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin


class ContextManagerPlugin:
    """上下文管理编排器：裁剪/压缩/注入按事件分发（算法在 context/ 模块）。"""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        # token 预算（默认 100K ≈ DeepSeek 窗口 78%——短对话零影响，
        # 长对话自动保护）；None/0 = 禁用裁剪
        self.budget: int | None = config.get("budget", 100_000)
        # 算法选择（阶段 C 扩展）：window | summarize | hybrid
        self.strategy: str = config.get("strategy", "window")

    def install(self, bus: EventBus) -> None:
        """监听 pre-step（改写）——pre-llm 注入在阶段 C/D 挂。"""
        bus.on("agent/pre-step", self._on_pre_step, priority=100)

    # ── pre-step：改写历史（裁剪/压缩） ───────────────────────────────────

    def _on_pre_step(self, messages: list[dict], **extra) -> list[dict]:
        """瀑布改写：①sticky 挂载（幂等）②按策略改写（当前 window 裁剪）。"""
        messages = self._mount_sticky(messages)
        if self.budget:
            messages, trimmed = trim_messages(messages, self.budget)
            if trimmed:
                print(f"[CTX] 已裁剪 {trimmed} 组早期历史（预算 {self.budget} tokens）")
        # 阶段 C：strategy=summarize 时在此走 compressor（超阈值 → 摘要替换）
        return messages

    @staticmethod
    def _mount_sticky(messages: list[dict]) -> list[dict]:
        """确保 system 含 sticky 区（幂等——clear 重建 system 后自动补挂）。"""
        sticky = get_sticky_text()
        if not sticky or not messages:
            return messages
        first = messages[0]
        if first.get("role") != "system":
            return messages
        from qi_agent.context.sticky import _STICKY_HEADER

        if _STICKY_HEADER in first.get("content", ""):
            return messages  # 已挂载（幂等）
        updated = dict(first)
        updated["content"] = first.get("content", "") + "\n\n" + sticky
        return [updated] + messages[1:]


# 自注册：默认开（零规则 = 行为不变，预算内不裁剪）
register_plugin(
    name="context_manager",
    factory=ContextManagerPlugin,
    description="上下文管理入口（裁剪/压缩/注入/算法选择，事件驱动）",
    default_enabled=True,
)
