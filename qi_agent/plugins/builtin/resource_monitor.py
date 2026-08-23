"""资源监控插件：token 消耗 + 上下文窗口占用（阶段 1，v0.4.22 + 数据源修正 2026-08-21）。

方案：docs/plans/2026-08-20-资源监控插件方案.md + docs/plans/2026-08-21-资源监控数据源修正方案.md
设计：
- 监听 agent/post-llm（每次 LLM 调用后触发；result 含 usage，messages 供估算兜底）
- 真实样本（普通 + 流式，2026-08-22 修复后均可获取）：usage 直接用（API 准确值）
- 估算兜底（DSH token-meter 同款）：仅 usage 真缺失时（异常/极端场景），
  estimate_messages 估算 prompt + 锚点校准（anchor_prompt + surface 增量）；
  估算轮有标注（estimated_calls）——统计以真实为主，估算只保底
- 累积：total_tokens 累加（真实 + 估算混合，估算轮 ~ 标注）；prompt 取最新
- 展示（交互调整 2026-08-21）：**不打印每轮状态行**——平时安静，仅上下文
  ≥80% 时条件警告；会话汇总走 CLI `usage` 命令（report()）
- 评测/自动化：build_agent(interactive=False) 不装配（输出零污染）
遗留（5.2）：上下文上限写死 64000——模型化映射表待 TODO（docs/todos/cli-ui.md）
"""

import os

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin

# 上下文窗口上限（DeepSeek chat 默认；遗留：模型化见方案 5.2）
_DEFAULT_CONTEXT_LIMIT = 64_000
_CONTEXT_LIMIT = int(os.getenv("QI_CONTEXT_LIMIT", _DEFAULT_CONTEXT_LIMIT))
# 警告阈值：上下文占用 ≥80% → 提示（防上下文爆炸后质量下降/token 浪费）
_WARN_RATIO = 0.8
# 固定密度启发式（DSH estimate.ts 同款参数）：4 字符 ≈ 1 token
_CHARS_PER_TOKEN = 4
# 每条消息 JSON 结构开销（role 字段等，DSH ROLE_OVERHEAD 同款）
_MSG_OVERHEAD = 4


def estimate_messages(messages: list[dict]) -> int:
    """估算消息列表的 prompt tokens（无真实 usage 时的兜底，DSH estimate.ts 同款思路）。

    固定密度启发式：每条消息 ceil(字符数/4) + 4 结构开销；tool_calls 额外计价。
    中文偏高/英文偏低可接受——只作展示与预警，非计费依据。
    """
    total = 0
    for msg in messages:
        total += _MSG_OVERHEAD
        content = msg.get("content") or ""
        total += (len(content) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN  # ceil 除法
        for tc in msg.get("tool_calls") or []:  # 工具调用消息额外计价
            fn = tc.get("function", {})
            total += (len(fn.get("name") or "") + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN
            total += (len(fn.get("arguments") or "") + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN
    return total


class ResourceMonitorPlugin:
    """资源监控：token 消耗 + 上下文占用（监听 agent/post-llm）。"""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}  # 预留业务配置段（factory 约定；当前无配置项）
        self.usage_history: list[dict] = []  # 真实样本（估算轮不进，语义不变）
        self.total_tokens = 0  # 真实 + 估算混合累计（估算轮 ~ 标注）
        self._sample_tokens = 0  # 真实样本累计（report 拆分用）
        self.estimated_calls = 0  # 估算轮次数
        # 锚点校准（DSH projectedTokens 语义）：最近真实样本作锚点
        self._anchor_prompt: int | None = None  # 锚点 prompt_tokens
        self._anchor_surface: int | None = None  # 锚点时的 estimate_messages

    def install(self, bus: EventBus) -> None:
        bus.on("agent/post-llm", self._on_post_llm, priority=100)

    def _on_post_llm(self, result, messages=None, **_) -> None:
        """每次 LLM 调用后：真实样本直接统计；缺失时估算兜底（DSH 式混合）。"""
        usage = getattr(result, "usage", None)
        if usage:
            self._record_sample(usage, messages)
        elif messages is not None:
            # 流式 usage 缺失（DeepSeek 实测基本不给）→ 估算兜底
            self._record_estimate(result, messages)
        # messages 也为 None（旧调用方/测试直调）→ 跳过，不崩溃

    def _record_sample(self, usage: dict, messages) -> None:
        """真实样本：准确统计 + 锚点更新。"""
        self.usage_history.append(usage)
        total = usage.get("total_tokens", 0)
        self.total_tokens += total
        self._sample_tokens += total
        prompt = usage.get("prompt_tokens", 0)
        self._anchor_prompt = prompt
        if messages is not None:
            self._anchor_surface = estimate_messages(messages)
        self._maybe_warn(prompt)

    def _record_estimate(self, result, messages: list[dict]) -> None:
        """估算轮：锚点校准 prompt + 内容估算 completion（~ 标注见 report）。"""
        prompt = self._estimate_prompt(messages)
        content = getattr(result, "content", None) or ""
        completion = (len(content) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN
        self.estimated_calls += 1
        self.total_tokens += prompt + completion
        self._maybe_warn(prompt)

    def _estimate_prompt(self, messages: list[dict]) -> int:
        """估算本轮 prompt_tokens：有锚点 → 锚点 + surface 增量；无锚点 → 纯估算。

        DSH projectedTokens 语义：真实样本作锚点，自锚点以来的消息增量用
        估算补齐——样本期准，间歇期比纯估算准。
        """
        surface = estimate_messages(messages)
        if self._anchor_prompt is not None and self._anchor_surface is not None:
            return max(0, self._anchor_prompt + (surface - self._anchor_surface))
        return surface

    def _maybe_warn(self, prompt: int) -> None:
        """上下文占用 ≥80% → 条件警告（平时安静，临界提醒——交互调整 2026-08-21）。

        不打印完整状态行（用户要求每轮安静）；仅在接近上下文上限时提醒，
        保留原方案"防上下文爆炸"价值。
        """
        pct = prompt / _CONTEXT_LIMIT * 100
        if pct >= _WARN_RATIO * 100:
            print(
                f"[资源] ⚠️ 上下文占用 {pct:.0f}%（{prompt:,}/{_CONTEXT_LIMIT:,}），"
                "建议 clear 或压缩",
                flush=True,
            )

    def report(self) -> str:
        """会话结束汇总（CLI 退出时打印，tool_stats 同款模式）。

        混合统计诚实拆分：真实样本 + 估算（~ 标注），不混淆两者。
        """
        if not self.usage_history and self.estimated_calls == 0:
            return "  [资源] 本次会话无 LLM 调用"
        parts = [f"  [资源] 累计消耗 {self.total_tokens:,} tokens"]
        if self.usage_history and self.estimated_calls == 0:
            # 纯真实样本：原格式（平均基于真实累计）
            avg = self._sample_tokens / len(self.usage_history)
            parts.append(f"（{len(self.usage_history)} 次调用，平均 {avg:,.0f}/次）")
        elif self.usage_history:
            # 混合：真实与估算拆分展示
            parts.append(
                f"（真实 {self._sample_tokens:,}"
                f" + 估算 {self.total_tokens - self._sample_tokens:,}）"
            )
            parts.append(f" · 含估算 {self.estimated_calls} 轮（~ 标注）")
        else:
            parts.append(f"（{self.estimated_calls} 轮估算，~ 标注）")
        parts.append(f" · 上下文上限 {_CONTEXT_LIMIT:,}")
        return "".join(parts)


register_plugin(
    "resource_monitor",
    ResourceMonitorPlugin,
    description="资源监控：token 消耗 + 上下文窗口占用（每轮状态行 + 汇总 + 上限警告）",
    default_enabled=True,
)
