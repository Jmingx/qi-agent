"""摘要压缩器（阶段 C，方案 2026-08-22-上下文管理）。

触发依据（用户要求 2026-08-22）：token 消耗**不用估算**——should_compress
直接用 response 真实 usage（result.usage.prompt_tokens = API 计费的真实
上下文大小），估算器仅作无 usage 时的兜底。

压缩策略（第一版算法，详见方案备注）：
- 结构化摘要：关键事实 / 用户要求 / 决策 / 未完成任务 / 工具结果结论
- 最近窗口保留：摘要 + 最近 N 条原文（细节不丢）
- L1 协议死线：摘要块 user 角色、最近消息从组边界开始、system 最前
"""

from qi_agent.context.window import _group_messages


def make_summarizer(model: str | None = None) -> callable:
    """摘要器工厂（C3 压缩模型独立配置，方案 2026-08-23）。

    Args:
        model: 压缩用模型名；None = 复用主模型（现状行为）

    Returns:
        callable(messages) -> str（摘要文本）

    惰性：client 在首次调用时才创建（零压缩零开销）。
    """
    def summarizer(messages: list[dict]) -> str:
        from qi_agent.agent_factory import load_api_key
        from qi_agent.llm import LLMClient

        client = LLMClient(load_api_key(), model=model or "deepseek-v4-flash")
        result = client.chat(
            [{"role": "user", "content": build_summary_prompt(messages)}]
        )
        return result.content or "[摘要失败]"

    return summarizer


def default_summarizer(messages: list[dict]) -> str:
    """默认摘要实现（兼容入口）：复用主模型（make_summarizer(None)）。"""
    return make_summarizer(None)(messages)

# 摘要提示中的结构化分区（对齐 Hermes 结构化压缩）
_SUMMARY_SECTIONS = ["关键事实", "用户要求", "决策", "未完成的任务", "工具结果结论"]


def should_compress(prompt_tokens: int, window: int,
                    threshold: float = 0.7) -> bool:
    """真实 usage 驱动：上下文占用超阈值 → 需要压缩。

    Args:
        prompt_tokens: 最近一次响应的 usage.prompt_tokens（真实值）
        window: 上下文窗口上限（DeepSeek 128K）
        threshold: 触发阈值（默认 0.7 = 70%）
    """
    return prompt_tokens >= window * threshold


def build_summary_prompt(messages: list[dict]) -> str:
    """构造结构化摘要提示（历史 → LLM 摘要）。"""
    lines = [
        "请将以下对话历史压缩为结构化摘要，保留所有关键信息：",
        "",
        "分区要求：",
    ]
    lines.extend(f"- {s}" for s in _SUMMARY_SECTIONS)
    lines.append("")
    lines.append("对话历史：")
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if msg.get("tool_calls"):
            calls = [tc.get("function", {}).get("name", "?")
                     for tc in msg["tool_calls"]]
            lines.append(f"[{role}] tool_calls={calls}")
        else:
            lines.append(f"[{role}] {content}")
    lines.append("")
    lines.append("直接输出摘要，不要其他内容。")
    return "\n".join(lines)


def compress_messages(messages: list[dict],
                      keep_recent: int = 10) -> tuple[list[dict], list[dict]]:
    """把消息切成 (早期待摘要, 最近保留)——边界在组边界上。

    最近保留从"最后一个完整消息组"开始取 keep_recent 组——保证
    切分点不会落在 tool 结果/assistant(tool_calls) 中间（L1 成对性）。
    """
    rest = [m for m in messages if m.get("role") != "system"]
    groups = _group_messages(rest)

    keep_count = max(0, min(keep_recent, len(groups)))
    recent_groups = groups[-keep_count:] if keep_count else []
    early_groups = groups[:-keep_count] if keep_count else groups

    early = [m for g in early_groups for m in g]
    recent = [m for g in recent_groups for m in g]
    return early, recent


def assemble(messages: list[dict], summary: str,
             keep_recent: int = 10) -> list[dict]:
    """组装压缩后消息：system + 摘要(user) + 最近保留。

    L1 协议：摘要块 user 角色（role 交替）；最近消息从组边界开始
    （compress_messages 已保证）；system 最前。
    """
    system = [m for m in messages if m.get("role") == "system"]
    _, recent = compress_messages(messages, keep_recent=keep_recent)
    summary_msg = {
        "role": "user",
        "content": "[早期对话已压缩为摘要]\n" + summary,
    }
    return system + [summary_msg] + recent
