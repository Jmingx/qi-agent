"""compress 摘要压缩策略（策略链）：真实 usage 超阈值 → 摘要压缩。

触发依据（用户要求 2026-08-22）：token 消耗不估算——should_apply 用
ctx.prompt_tokens（post-llm 采集的 response 真实 usage）。
消费语义：压缩后责任链停止（压缩是主要动作，窗口兜底仅在 hybrid
链配置时排其后）。
"""

from qi_agent.context.compressor import (
    assemble,
    compress_messages,
    should_compress,
)
from qi_agent.context.strategies.registry import register_strategy
from qi_agent.context.strategies.base import ContextInfo, ContextStrategy


@register_strategy
class CompressStrategy(ContextStrategy):
    """摘要压缩：早期历史 → LLM 结构化摘要 → 组装（L1 协议）。"""

    name = "compress"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self.window: int = config.get("window", 128_000)
        self.threshold: float = config.get("threshold", 0.7)
        self.keep_recent: int = config.get("keep_recent", 10)
        # 异步开关（二期）：True = 后台压缩（快照隔离）；False = 同步
        self.async_mode: bool = config.get("async", False)

    def should_apply(self, ctx: ContextInfo) -> bool:
        # 真实 usage 驱动（不估算）；无 summarizer 时不可用（fail-safe）
        if ctx.summarizer is None:
            return False
        return should_compress(ctx.prompt_tokens, self.window, self.threshold)

    def apply(self, messages: list[dict], ctx: ContextInfo
              ) -> tuple[list[dict], bool]:
        early, _ = compress_messages(messages, keep_recent=self.keep_recent)
        if not early:
            return messages, False  # 无可压缩的早期历史
        summary = ctx.summarizer(early)
        compressed = assemble(messages, summary, keep_recent=self.keep_recent)
        before = len([m for m in messages if m.get("role") != "system"])
        after = len([m for m in compressed if m.get("role") != "system"])
        print(f"[CTX] 已压缩：{before} → {after} 条消息")
        return compressed, True  # 消费：压缩后责任链停止
