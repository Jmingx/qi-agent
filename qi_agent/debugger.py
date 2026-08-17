"""调试日志器：打印 LLM 交互全链路（独立模块）。

设计原则（回顾阶段 2.5 方案）：
- 日志逻辑与 agent 核心逻辑分离——本模块负责全部格式化/打印
- agent.py 只通过依赖注入的可选 logger 调用，核心逻辑零改动
- enabled=False 时所有方法直接返回（零开销）

输出链路：
[USER] 用户输入 → [REQ] 发送请求 → [RESP] 模型响应
→ [TOOL] 工具执行 → [REQ] 第二轮 → [RESP] 最终答案 → [ANSWER]
"""

import json
from typing import Any

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
    """打印带分隔线和标题的日志块。

    开头强制换行：保证日志框永远从行首开始——
    流式输出是行内模式（end="" 不换行），如果日志框不先换行，
    会直接粘连在流式文本后面（如 "😊┌─────┐" 错位）。
    """
    print()  # 关键：确保框独立成行（流式 + 日志混用时的显示修复）
    width = max(len(title) + 4, 60)
    print("┌─" + "─" * width + "┐")
    print(f"│ {title}")
    for line in lines:
        print(f"│ {line}")
    print("└" + "─" * width + "┘")


class DebugLogger:
    """格式化输出请求/响应/工具调用的完整信息。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def log_user_input(self, text: str) -> None:
        """打印用户输入。"""
        if not self.enabled:
            return
        _print_box("[USER] 用户输入", [_truncate(text)])

    def log_request(self, messages: list[dict], tools: list[dict] | None) -> None:
        """打印发送给模型的完整请求：全部消息历史 + 工具定义。"""
        if not self.enabled:
            return

        lines: list[str] = []
        # 消息历史：逐条精简展示（role + 内容/工具调用）
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            if msg.get("tool_calls"):
                calls = [tc["function"]["name"] for tc in msg["tool_calls"]]
                lines.append(f"  messages[{i}] {role}: tool_calls={calls}")
            else:
                content = msg.get("content", "")
                lines.append(f"  messages[{i}] {role}: {_truncate(str(content), 200)}")

        # 工具定义：数量 + 名称列表（完整 schema 太长）
        if tools:
            names = [t["function"]["name"] for t in tools]
            lines.append(f"  tools({len(tools)}): {', '.join(names)}")

        _print_box(f"[REQ] 发送给模型（{len(messages)} 条消息）", lines)

    def log_response(self, result: Any) -> None:
        """打印模型响应：文本内容或工具调用。"""
        if not self.enabled:
            return

        if result.tool_calls:
            lines = [
                f"  tool_call id={tc.id}, name={tc.name}, arguments={_to_json(tc.arguments)}"
                for tc in result.tool_calls
            ]
            _print_box(f"[RESP] 模型响应（请求 {len(result.tool_calls)} 个工具调用）", lines)
        else:
            _print_box("[RESP] 模型响应（直接回答）", [_truncate(str(result.content or ""))])

    def log_tool_call(self, name: str, arguments: dict, output: str) -> None:
        """打印工具执行：入参 + 返回结果。"""
        if not self.enabled:
            return
        lines = [
            f"  {name}({_to_json(arguments)})",
            f"  → {_truncate(str(output))}",
        ]
        _print_box("[TOOL] 执行工具", lines)

    def log_final_answer(self, text: str) -> None:
        """打印最终答案。"""
        if not self.enabled:
            return
        _print_box("[ANSWER] 最终答案", [_truncate(text)])
