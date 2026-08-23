"""上下文管理插件（2026-08-22 用户架构修正）：上下文管理统一入口。

设计：本插件是"编排器"（策略分发 + 事件挂载），不写算法——
算法在 context/ 子包（window 滑动裁剪 / compressor 摘要压缩 /
inject 注入 / estimator 估算），换算法不碰插件（config 选策略）：

  [context_manager]
  enabled = true
  window = 128000          # 上下文窗口（DeepSeek 上限）
  threshold = 0.7          # 压缩触发阈值（窗口占比）
  keep_recent = 10         # 压缩时保留的最近消息组数
  strategy = summarize     # window（滑动裁剪）| summarize（摘要压缩）
  budget = 100000          # window 策略的裁剪预算

事件挂载：
  agent/post-llm  → 采集真实 usage（response.prompt_tokens）→ 触发检查
                    （用户要求：token 消耗不用估算，从 response 实时获取）
  agent/pre-step  → 改写历史（压缩/裁剪）
  agent/pre-llm   → 注入（sticky/todo 上下文，幂等）——阶段 D

压缩执行：summarizer 依赖注入（测试 mock；默认实现惰性建 LLMClient，
首次压缩才创建——不压缩零开销）。
"""

from qi_agent.context.compressor import (
    assemble,
    build_summary_prompt,
    compress_messages,
    should_compress,
)
from qi_agent.context.sticky import get_sticky_text
from qi_agent.context.window import trim_messages
from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin


def _default_summarizer(messages: list[dict]) -> str:
    """默认摘要实现：独立 LLM 调用（惰性建 client，压缩才发生）。"""
    from qi_agent.agent_factory import load_api_key
    from qi_agent.llm import LLMClient

    client = LLMClient(load_api_key())
    result = client.chat([{"role": "user", "content": build_summary_prompt(messages)}])
    return result.content or "[摘要失败]"


class ContextManagerPlugin:
    """上下文管理编排器：压缩/裁剪/注入按事件分发（算法在 context/ 模块）。"""

    def __init__(self, config: dict | None = None,
                 summarizer=None) -> None:
        config = config or {}
        # 上下文窗口与压缩阈值（真实 usage 驱动，方案阶段 C）
        self.window: int = config.get("window", 128_000)
        self.threshold: float = config.get("threshold", 0.7)
        self.keep_recent: int = config.get("keep_recent", 10)
        # 算法选择：window（滑动裁剪）| summarize（摘要压缩）| hybrid
        self.strategy: str = config.get("strategy", "summarize")
        # window 策略的裁剪预算（None 禁用裁剪）
        self.budget: int | None = config.get("budget", 100_000)
        # 摘要器注入（测试 mock；默认惰性 LLMClient）
        self._summarizer = summarizer or _default_summarizer
        # 最近一次真实 usage（post-llm 采集）
        self._last_prompt_tokens: int = 0
        self._compression_pending: bool = False

    def install(self, bus: EventBus) -> None:
        """pre-step 改写历史（裁剪/压缩）+ post-llm 采真实 usage。"""
        bus.on("agent/pre-step", self._on_pre_step, priority=100)
        bus.on("agent/post-llm", self._on_post_llm, priority=50)

    # ── post-llm：真实 usage 采集 → 触发检查 ──────────────────────────────

    def _on_post_llm(self, result, **extra) -> None:
        """从 response 实时获取 usage.prompt_tokens（不估算）→ 超阈值标记压缩。"""
        usage = getattr(result, "usage", None)
        if not usage or not usage.get("prompt_tokens"):
            return  # 无 usage（异常/兜底场景）——本次不触发
        self._last_prompt_tokens = usage["prompt_tokens"]
        if self.strategy in ("summarize", "hybrid") and should_compress(
            usage["prompt_tokens"], self.window, self.threshold
        ):
            self._compression_pending = True
            print(
                f"[CTX] 上下文占用 {usage['prompt_tokens']}/{self.window} tokens"
                f"（{usage['prompt_tokens'] / self.window:.0%}）超阈值"
                f" {self.threshold:.0%} → 摘要压缩"
            )

    # ── pre-step：改写历史（sticky 挂载 + 压缩/裁剪） ─────────────────────

    def _on_pre_step(self, messages: list[dict], **extra) -> list[dict]:
        """瀑布改写：①sticky 挂载（幂等）②压缩/裁剪。"""
        messages = self._mount_sticky(messages)
        if self._compression_pending:
            messages = self._do_compress(messages)
            self._compression_pending = False
        elif self.strategy == "window" and self.budget:
            messages, trimmed = trim_messages(messages, self.budget)
            if trimmed:
                print(f"[CTX] 已裁剪 {trimmed} 组早期历史（预算 {self.budget} tokens）")
        return messages

    def _do_compress(self, messages: list[dict]) -> list[dict]:
        """执行摘要压缩：早期历史 → 摘要（独立 LLM 调用）→ 组装。"""
        early, _ = compress_messages(messages, keep_recent=self.keep_recent)
        if not early:
            return messages  # 没有可压缩的早期历史
        summary = self._summarizer(early)
        compressed = assemble(messages, summary, keep_recent=self.keep_recent)
        print(
            f"[CTX] 已压缩：{len([m for m in messages if m.get('role') != 'system'])}"
            f" → {len([m for m in compressed if m.get('role') != 'system'])} 条消息"
        )
        return compressed

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


# 自注册：默认开（strategy=summarize，真实 usage 驱动；零规则 = 行为不变）
register_plugin(
    name="context_manager",
    factory=ContextManagerPlugin,
    description="上下文管理入口（压缩/裁剪/注入/算法选择，真实 usage 驱动）",
    default_enabled=True,
)
