"""上下文构成分解（阶段 A3）：system / 工具 schema / 历史 各占多少。

设计（方案 2026-08-22-上下文管理，对齐 Hermes context_breakdown）：
- 分段估算：system prompt（含 sticky）/ 工具 schema / 对话历史
- --debug 展示"当前上下文占用 X%"——压缩触发前用户可见
- 窗口默认 128K（DeepSeek 上限，可配置）
"""

from qi_agent.context.estimator import (
    estimate_tool_schemas_tokens,
    estimate_tokens,
)

# 默认上下文窗口（DeepSeek 上限，可配置）
DEFAULT_WINDOW = 128_000


def compute_breakdown(messages: list[dict], schemas: list[dict],
                      window: int = DEFAULT_WINDOW) -> dict:
    """计算上下文构成分解。

    Args:
        messages: 当前消息历史（含 system）
        schemas: 工具 schema 列表
        window: 上下文窗口大小（token）

    Returns:
        {
            "total": 总估算 token,
            "system": system prompt 估算,
            "tools": 工具 schema 估算,
            "history": 对话历史估算（system 之外的消息）,
            "history_pct": 历史占总 token 百分比,
            "window_pct": 总 token 占窗口百分比,
        }
    """
    tools_tokens = estimate_tool_schemas_tokens(schemas)
    system_tokens = 0
    history_tokens = 0
    for msg in messages:
        text = str(msg.get("content", "") or "")
        if msg.get("role") == "system":
            system_tokens += estimate_tokens(text)
        else:
            history_tokens += estimate_tokens(text)

    total = system_tokens + tools_tokens + history_tokens
    return {
        "total": total,
        "system": system_tokens,
        "tools": tools_tokens,
        "history": history_tokens,
        "history_pct": round(history_tokens / total * 100, 1) if total else 0.0,
        "window_pct": round(total / window * 100, 1) if window else 0.0,
    }


def format_breakdown(breakdown: dict) -> str:
    """人类可读展示（--debug / /context 用）。"""
    return (
        f"[上下文] 总 {breakdown['total']} tokens"
        f"（system {breakdown['system']} / tools {breakdown['tools']} / "
        f"历史 {breakdown['history']}），占用窗口 {breakdown['window_pct']}%"
    )
