"""上下文管理插件（2026-08-23 策略链方案）：纯编排器，零算法逻辑。

设计（方案 docs/plans/2026-08-23-上下文压缩策略链方案.md）：
- 算法全部在 context/strategies/（1 策略 1 文件 + 注册表）
- 本插件只做：事件挂载 + 构建策略链 + 按链执行（should_apply → apply）

  [context_manager]
  enabled = true
  chain = ["sticky", "compress", "window"]   # 顺序可配置（责任链语义）
  compress = {window: 128000, threshold: 0.7, keep_recent: 10, async: false}
  window = {budget: 100000}

事件挂载：
  agent/post-llm  → 采集真实 usage（response.prompt_tokens，用户要求不估算）
  agent/pre-step  → 策略链执行（压缩/裁剪/注入）
"""

from qi_agent.context.async_compressor import AsyncCompressor
from qi_agent.context.compressor import default_summarizer
from qi_agent.context.strategies import build_chain
from qi_agent.context.strategies.base import ContextInfo
from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin


class ContextManagerPlugin:
    """上下文管理编排器：策略链执行 + 异步压缩调度（算法在 strategies/）。"""

    def __init__(self, config: dict | None = None,
                 summarizer=None) -> None:
        config = config or {}
        # 策略链（config.chain 顺序；每策略收各自配置段）
        self._chain = build_chain(config=config)
        # 摘要器注入（测试 mock；None → 默认惰性 LLMClient 实现——
        # load_plugins 装配只传 config，真实路径必须兜底，2026-08-23 修复）
        self._summarizer = summarizer or default_summarizer
        # 最近一次真实 usage（post-llm 采集）
        self._last_prompt_tokens: int = 0
        # 异步压缩（方案 2026-08-23 二期：默认开启，后台无感压缩）
        self._async_compressor: AsyncCompressor | None = None
        async_enabled = config.get("async_compress", True)
        compress_cfg = config.get("compress", {})
        if async_enabled and self._summarizer is not None:
            self._async_compressor = AsyncCompressor(
                summarizer=self._summarizer,
                keep_recent=compress_cfg.get("keep_recent", 10),
                max_growth=config.get("async_max_growth", 3),
            )

    @property
    def chain(self) -> list:
        """策略链（测试/展示）。"""
        return self._chain

    def install(self, bus: EventBus) -> None:
        """pre-step 改写历史（策略链）+ post-llm 采真实 usage。"""
        bus.on("agent/pre-step", self._on_pre_step, priority=100)
        bus.on("agent/post-llm", self._on_post_llm, priority=50)

    # ── post-llm：真实 usage 采集（决策数据源） ───────────────────────────

    def _on_post_llm(self, result, **extra) -> None:
        """从 response 实时获取 usage.prompt_tokens（不估算）——策略链决策依据。

        异步压缩：超阈值 → 提交后台任务（快照隔离，agent 循环不阻塞）。
        """
        usage = getattr(result, "usage", None)
        if usage and usage.get("prompt_tokens"):
            self._last_prompt_tokens = usage["prompt_tokens"]
            if self._async_compressor is not None:
                # 超阈值 → 后台压缩（消息快照来自 post-llm payload）
                compress_strategy = next(
                    (s for s in self._chain if s.name == "compress"), None)
                if compress_strategy and compress_strategy.should_apply(
                    ContextInfo(prompt_tokens=usage["prompt_tokens"],
                                summarizer=self._summarizer)
                ):
                    submitted = self._async_compressor.request(
                        extra.get("messages") or [])
                    if submitted:
                        print(
                            f"[CTX] 上下文占用 {usage['prompt_tokens']}"
                            f" 超阈值 → 后台异步压缩"
                        )

    # ── pre-step：异步快照切换 + 策略链执行 ──────────────────────────────

    def _on_pre_step(self, messages: list[dict], **extra) -> list[dict]:
        """瀑布改写：①异步压缩快照切换（就绪+新鲜）②策略链（消费即停）。"""
        # ① 异步快照：就绪且新鲜 → 切换（已压缩，跳过 compress 防重复）
        if self._async_compressor is not None:
            fresh = self._async_compressor.take_if_fresh(messages)
            if fresh is not None:
                messages = fresh
                print("[CTX] 异步压缩完成，已切换（消息数下降）")
                return self._run_chain(
                    messages, extra,
                    [s for s in self._chain if s.name != "compress"],
                )
            elif self._async_compressor.is_busy():
                # 任务进行中：跳过 compress 策略（防双重压缩），其余照跑
                return self._run_chain(
                    messages, extra,
                    [s for s in self._chain if s.name != "compress"],
                )
        return self._run_chain(messages, extra, self._chain)

    def _run_chain(self, messages: list[dict], extra: dict,
                   chain: list) -> list[dict]:
        """执行策略链（should_apply 判断 + apply 改写 + 消费即停）。"""
        ctx = ContextInfo(
            prompt_tokens=self._last_prompt_tokens,
            summarizer=self._summarizer,
            chain_name=",".join(s.name for s in chain),
            step=extra.get("step", 0),
        )
        for strategy in chain:
            if strategy.should_apply(ctx):
                messages, consumed = strategy.apply(messages, ctx)
                if consumed:
                    break  # 责任链：处理完就停
        return messages


# 自注册：默认开（默认链 sticky→compress→window；零规则 = 行为不变）
register_plugin(
    name="context_manager",
    factory=ContextManagerPlugin,
    description="上下文管理入口（策略链：压缩/裁剪/注入，真实 usage 驱动）",
    default_enabled=True,
)
