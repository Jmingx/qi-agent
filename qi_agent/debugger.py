"""调试日志器：打印 LLM 交互全链路（独立模块）。

设计原则（回顾阶段 2.5 方案）：
- 日志逻辑与 agent 核心逻辑分离——本模块负责全部格式化/打印
- agent.py 只通过依赖注入的可选 logger 调用，核心逻辑零改动
- enabled=False 时所有方法直接返回（零开销）

输出链路：
[USER] 用户输入 → [REQ] 发送请求 → [RESP] 模型响应
→ [TOOL] 工具执行 → [REQ] 第二轮 → [RESP] 最终答案 → [ANSWER]

显示设计（v0.4.4+）：
- 日志框右对齐打印——与左侧的对话输出（你> / agent>）形成视觉分区，
  用户一眼分辨"哪些是日志、哪些是输出"
"""

import json
import shutil
from typing import Any

# 单条消息/工具的显示上限，防止刷屏
_MAX_ITEM_CHARS = 500

# 日志框宽度（固定值，保证所有框一致）
_BOX_WIDTH = 66


def _truncate(text: str, limit: int = _MAX_ITEM_CHARS) -> str:
    """截断超长文本，保留头部和省略标记。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[已截断, 共{len(text)}字符]"


def _to_json(data: Any) -> str:
    """把数据转成可读 JSON（中文不转义）。"""
    return json.dumps(data, ensure_ascii=False, indent=2)


def _box_width() -> int:
    """日志框总宽度（含左右边框）。"""
    return _BOX_WIDTH


def _right_pad() -> int:
    """计算右对齐的左侧填充空格数（日志框贴着终端右缘）。"""
    try:
        term_width = shutil.get_terminal_size().columns
    except Exception:
        term_width = 120  # 无法获取终端宽度时用默认值（如测试环境）
    return max(0, term_width - _box_width())


def _print_box(title: str, lines: list[str]) -> None:
    """打印带分隔线和标题的日志块（右对齐）。

    显示设计：
    - 开头强制换行：保证日志框永远从行首开始（流式 + 日志混用不粘连）
    - 右对齐：框整体贴终端右缘，与左侧对话输出形成视觉分区
    """
    print()  # 关键：确保框独立成行（流式 + 日志混用时的显示修复）
    pad = " " * _right_pad()
    inner_w = _BOX_WIDTH - 4  # 内容宽度（去掉 │ 和两侧空格）

    # 标题行（右对齐）
    print(f"{pad}┌─" + "─" * inner_w + "┐")
    print(f"{pad}│ {title:<{inner_w - 1}}│")
    # 内容行（超长截断 + 右对齐）
    for line in lines:
        display = line if len(line) <= inner_w - 1 else line[: inner_w - 1] + "…"
        print(f"{pad}│ {display:<{inner_w - 1}}│")
    print(f"{pad}└" + "─" * inner_w + "┘")


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
