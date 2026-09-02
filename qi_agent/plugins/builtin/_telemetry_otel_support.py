"""telemetry_otel 的内部辅助逻辑。

只放纯辅助函数和状态容器，避免主插件文件过长。
"""

from __future__ import annotations

import inspect
import time
import threading
from dataclasses import dataclass, field
from typing import Any

_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "code",
    "command",
    "content",
    "file",
    "filename",
    "input",
    "message",
    "output",
    "password",
    "path",
    "prompt",
    "query",
    "secret",
    "text",
    "token",
}
_MEMORY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("我喜欢", "user"),
    ("我习惯", "user"),
    ("请记住", "user"),
    ("我的爱好", "user"),
    ("我最爱", "user"),
    ("我们决定", "memory"),
    ("我们约定", "memory"),
    ("以后用", "memory"),
    ("以后都用", "memory"),
)
_FAIL_PREFIXES = ("[安全拦截]", "[审批拒绝]")
_PARENT_BY_CONTEXT: dict[str, str] = {}
_DELEGATE_SPANS: dict[str, Any] = {}
_GLOBAL_LOCK = threading.RLock()


@dataclass
class PendingSpan:
    """待结束的 span。"""

    span: Any
    started_at: float


@dataclass
class State:
    """单个 context 的观测状态。"""

    root: Any | None = None
    root_token: Any | None = None
    turn: Any | None = None
    turn_token: Any | None = None
    turn_no: int | None = None
    llm_started: dict[tuple[int, int], float] = field(default_factory=dict)
    tool_spans: dict[tuple[int, int], list[PendingSpan]] = field(default_factory=dict)
    turn_usage: dict[int, dict[str, int]] = field(default_factory=dict)
    pre_steps: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)


class NullSpan:
    """opentelemetry 不可用时的空对象。"""

    def set_attribute(self, *_, **__) -> None:
        return None

    def end(self) -> None:
        return None

    def get_span_context(self) -> Any:
        return None


def stack_context() -> Any | None:
    """从调用栈里找 AgentContext，读取 parent_id。"""

    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            context = frame.f_locals.get("context")
            if context is not None and hasattr(context, "parent_id"):
                return context
            frame = frame.f_back
    finally:
        del frame
    return None


def truncate(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + f"...[截断 {len(text)}]"


def summarize_value(key: str, value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return f"bool({value})"
    if isinstance(value, (int, float)):
        return f"{type(value).__name__}({value})"
    if isinstance(value, str):
        if key.lower() in _SENSITIVE_KEYS:
            return f"str(len={len(value)})"
        return f"str(len={len(value)}, value={truncate(value)!r})"
    if isinstance(value, dict):
        parts = []
        for idx, (sub_key, sub_value) in enumerate(value.items()):
            if idx >= 5:
                parts.append(f"...(+{len(value) - 5})")
                break
            parts.append(f"{sub_key}={summarize_value(str(sub_key), sub_value)}")
        return f"dict(len={len(value)}, {{{', '.join(parts)}}})"
    if isinstance(value, list):
        return f"list(len={len(value)})"
    return f"{type(value).__name__}(len={len(str(value))})"


def summarize_arguments(arguments: dict[str, Any]) -> str:
    return ", ".join(
        f"{key}={summarize_value(key, value)}" for key, value in arguments.items()
    )


def last_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return str(message["content"])
    return ""


def estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        total += 4
        total += (len(str(message.get("content") or "")) + 3) // 4
    return total


def wrap_bail(plugin: Any, bus: Any, approval_name: str, turn_context) -> None:
    """包一层 bail，记录完整审批耗时。"""

    if getattr(bus, "_qi_otel_wrapped", False):
        return
    original_bail = bus.bail

    def wrapped_bail(event: str, **data: Any) -> Any:
        if event != "agent/tool-approval":
            return original_bail(event, **data)
        plugin._ensure_root()
        start = time.perf_counter()
        span = plugin._start_span(
            approval_name,
            parent=turn_context(),
            attrs={"tool.name": str(data.get("name", "")), "result": "pending"},
        )
        try:
            result = original_bail(event, **data)
        except Exception:
            plugin._finish_span(
                span,
                {
                    "result": "error",
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                },
            )
            raise
        plugin._finish_span(
            span,
            {
                "result": "approved" if result is True else "denied",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            },
        )
        return result

    bus.bail = wrapped_bail
    bus._qi_otel_wrapped = True  # type: ignore[attr-defined]


def wrap_tool_call(plugin: Any, bus: Any) -> None:
    """包一层 tool-call bail，确保决策点一定能采到 span。"""

    if getattr(bus, "_qi_otel_tool_wrapped", False):
        return
    original_bail = bus.bail

    def wrapped_bail(event: str, **data: Any) -> Any:
        if event == "agent/tool-call":
            plugin._on_tool_call(
                name=str(data.get("name", "")),
                arguments=dict(data.get("arguments") or {}),
                turn=int(data.get("turn", 0) or 0),
                step=int(data.get("step", 0) or 0),
            )
        return original_bail(event, **data)

    bus.bail = wrapped_bail
    bus._qi_otel_tool_wrapped = True  # type: ignore[attr-defined]
