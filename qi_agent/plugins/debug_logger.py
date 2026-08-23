"""调试日志插件（2026-08-22 用户架构修正）：logger 插件化，事件驱动打印。

设计：日志是横切关注点，与上下文管理一样不侵入 agent——本插件监听
agent/* 事件，按事件类型打印对应日志段：
  agent/turn-start   → [USER] 用户输入
  agent/pre-llm      → [CTX] 上下文占用 + [REQ] 请求（消息历史 + 工具清单）
  agent/post-llm     → [RESP] 模型响应
  agent/tool-result  → [TOOL] 工具调用（名称/参数/输出）
  agent/final-answer → [ANSWER] 最终答案

配置（plugins.toml）：[debug_logger] enabled = true（CLI --debug 装配）

显示设计（原 debugger.py 内联，v0.4.4+）：[LOG] 前缀日志框左对齐——
与对话输出（你> / agent>）视觉分区，一眼分辨"日志 vs 输出"。
"""

import json
from typing import Any

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin

# 单条消息/工具的显示上限，防止刷屏
_MAX_ITEM_CHARS = 500


def _truncate(text: str, limit: int = _MAX_ITEM_CHARS) -> str:
    """截断超长文本，保留头部和省略标记。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[已截断, 共{len(text)}字符]"


def _to_json(data: Any) -> str:
    """把数据转成可读 JSON（中文不转义）。"""
    return json.dumps(data, ensure_ascii=False, indent=2)


def _print_box(title: str, lines: list[str]) -> None:
    """打印带分隔线和标题的日志块（左对齐 + [LOG] 前缀）。"""
    print()  # 关键：确保框独立成行（流式 + 日志混用时的显示修复）
    text_w = 76  # 内容宽度

    print("[LOG] ┌" + "─" * text_w + "┐")
    print(f"[LOG] │ {title.ljust(text_w - 1)}│")
    for line in lines:
        for sub in line.split("\n"):  # 处理含换行的内容（如模型多行回复）
            if len(sub) <= text_w - 1:
                display = sub
            else:
                display = sub[: text_w - 2] + "…"  # 超长截断
            print(f"[LOG] │ {display.ljust(text_w - 1)}│")
    print("[LOG] └" + "─" * text_w + "┘")


class DebugLoggerPlugin:
    """事件驱动调试日志：不同事件 → 对应日志段（订阅式打印）。"""

    def __init__(self, config: dict | None = None) -> None:
        self.enabled = True

    def install(self, bus: EventBus) -> None:
        """监听 5 个事件点（优先级 0，普通观测层）。"""
        bus.on("agent/turn-start", self._on_turn_start)
        bus.on("agent/pre-llm", self._on_pre_llm)
        bus.on("agent/post-llm", self._on_post_llm)
        bus.on("agent/tool-result", self._on_tool_result)
        bus.on("agent/final-answer", self._on_final_answer)

    # ── 事件处理（每个事件 → 对应日志段） ────────────────────────────────

    def _on_turn_start(self, user_input: str, **_) -> None:
        _print_box("[USER] 用户输入", [_truncate(user_input)])

    def _on_pre_llm(self, messages: list[dict], tools: list[dict], **_) -> None:
        self._log_context_breakdown(messages, tools)
        self._log_request(messages, tools)

    def _on_post_llm(self, result, **extra) -> None:
        # 真实 usage 统计（2026-08-22：准确 token 消耗——response 实时值，
        # 用于统计与事实窗口维护；估算仅作 pre-llm 预测）
        usage_line = ""
        usage = getattr(result, "usage", None)
        if usage:
            usage_line = (
                f"  usage（真实）: prompt={usage.get('prompt_tokens', '?')} "
                f"completion={usage.get('completion_tokens', '?')} "
                f"total={usage.get('total_tokens', '?')}"
            )
        if result.tool_calls:
            lines = [
                f"  tool_call id={tc.id}, name={tc.name}, arguments={_to_json(tc.arguments)}"
                for tc in result.tool_calls
            ]
            if usage_line:
                lines.append(usage_line)
            _print_box(f"[RESP] 模型响应（请求 {len(result.tool_calls)} 个工具调用）", lines)
        else:
            lines = [_truncate(str(result.content or ""))]
            if usage_line:
                lines.append(usage_line)
            _print_box("[RESP] 模型响应（直接回答）", lines)

    def _on_tool_result(self, name: str, arguments: dict,
                        output: str, **_) -> None:
        _print_box("[TOOL] 执行工具", [
            f"  {name}({_to_json(arguments)})",
            f"  → {_truncate(str(output))}",
        ])

    def _on_final_answer(self, content: str, **_) -> None:
        _print_box("[ANSWER] 最终答案", [_truncate(content)])

    # ── 内部格式化 ────────────────────────────────────────────────────────

    def _log_context_breakdown(self, messages: list[dict],
                               tools: list[dict] | None) -> None:
        """打印上下文构成（估算——请求前预测用；真实值见 [RESP] usage）。"""
        from qi_agent.context.breakdown import compute_breakdown, format_breakdown

        breakdown = compute_breakdown(messages, tools or [])
        _print_box("[CTX] 上下文占用（估算）", [format_breakdown(breakdown)])

    def _log_request(self, messages: list[dict], tools: list[dict] | None) -> None:
        """打印发送给模型的完整请求：全部消息历史 + 工具定义。"""
        lines: list[str] = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            if msg.get("tool_calls"):
                calls = [tc["function"]["name"] for tc in msg["tool_calls"]]
                lines.append(f"  messages[{i}] {role}: tool_calls={calls}")
            else:
                content = msg.get("content", "")
                lines.append(f"  messages[{i}] {role}: {_truncate(str(content), 200)}")
        if tools:
            names = [t["function"]["name"] for t in tools]
            lines.append(f"  tools({len(tools)}): {', '.join(names)}")
        _print_box(f"[REQ] 发送给模型（{len(messages)} 条消息）", lines)


# 自注册：默认关（--debug 才装配，正常会话零输出）
register_plugin(
    name="debug_logger",
    factory=DebugLoggerPlugin,
    description="调试日志（事件驱动打印 [USER]/[CTX]/[REQ]/[RESP]/[TOOL]/[ANSWER]）",
    default_enabled=False,
)
