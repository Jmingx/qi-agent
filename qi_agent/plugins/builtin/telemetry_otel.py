"""OTel 观测插件：事件驱动采集 → OTLP 批量导出。"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin
from qi_agent.plugins.builtin._telemetry_otel_support import (
    _DELEGATE_SPANS,
    _FAIL_PREFIXES,
    _GLOBAL_LOCK,
    _MEMORY_PATTERNS,
    _PARENT_BY_CONTEXT,
    NullSpan,
    PendingSpan,
    State,
    estimate_tokens,
    last_user_text,
    stack_context,
    summarize_arguments,
    wrap_bail,
    wrap_tool_call,
)

try:  # pragma: no cover - 依赖缺失时自动降级
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.context import attach, detach
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Link
except Exception:  # pragma: no cover
    trace = None
    OTLPSpanExporter = None
    attach = None
    detach = None
    Resource = None
    TracerProvider = None
    BatchSpanProcessor = None
    Link = None

_LOG = logging.getLogger(__name__)
_DEFAULT_ENDPOINT = "http://127.0.0.1:4318"
_ROOT_NAME = "agent/run-start"
_TURN_NAME = "agent/turn"
_LLM_NAME = "agent/post-llm"
_TOOL_NAME = "agent/tool-call"
_APPROVAL_NAME = "agent/tool-approval"
_COMPRESS_NAME = "context/compress"
_MEMORY_NAME = "memory/write"


class TelemetryOtelPlugin:
    """事件驱动 OTel 采集器。"""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.config = config
        self.model = str(config.get("model") or os.getenv("QI_MODEL") or "deepseek-v4-flash")
        self.service_name = str(config.get("service_name") or "qi-agent")
        self.endpoint = self._resolve_endpoint(config)
        self.enabled = (
            bool(config.get("enabled", True))
            and trace is not None
            and bool(self.endpoint)
        )
        self._state = State()
        self._lock = threading.RLock()
        self._bus: EventBus | None = None
        self._provider = None
        self._tracer = None
        if self.enabled:
            self._init_otel()

    def _resolve_endpoint(self, config: dict) -> str:
        endpoint = (
            config.get("otlp_endpoint")
            or config.get("endpoint")
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or _DEFAULT_ENDPOINT
        )
        endpoint = str(endpoint).strip()
        if not endpoint:
            return ""
        if endpoint.endswith("/v1/traces"):
            return endpoint
        return endpoint.rstrip("/") + "/v1/traces"

    def _init_otel(self) -> None:
        try:
            exporter = OTLPSpanExporter(endpoint=self.endpoint)
            provider = TracerProvider(resource=Resource.create({"service.name": self.service_name}))
            provider.add_span_processor(
                BatchSpanProcessor(
                    exporter,
                    max_queue_size=2048,
                    max_export_batch_size=2048,
                    schedule_delay_millis=60_000,
                    export_timeout_millis=10_000,
                )
            )
            self._provider = provider
            self._tracer = provider.get_tracer(__name__)
        except Exception as exc:  # pragma: no cover
            _LOG.warning("OTel 初始化失败，降级为 no-op: %s", exc)
            self.enabled = False

    def install(self, bus: EventBus) -> None:
        if not self.enabled:
            return
        self._bus = bus
        bus.on("agent-manager/register", self._on_register, priority=200)
        bus.on("agent/run-start", self._on_root_start, priority=200)
        bus.on("agent/turn-start", self._on_root_start, priority=200)
        bus.on("agent/pre-step", self._on_pre_step_capture, priority=200)
        bus.on("agent/pre-step", self._on_pre_step_compare, priority=-200)
        bus.on("agent/pre-llm", self._on_pre_llm, priority=200)
        bus.on("agent/post-llm", self._on_post_llm, priority=200)
        wrap_tool_call(self, bus)
        bus.on("agent/tool-result", self._on_tool_result, priority=200)
        bus.on("agent/turn-end", self._on_turn_end, priority=200)
        # 收尾语义（2026-09-02 实测修正）：agent.py 正常完成只发
        # agent/final-answer（turn-end 仅在 stopped/max_turns 路径发）——
        # 两者都要触发 root/turn span 收尾 + 导出，否则正常对话永不导出
        bus.on("agent/final-answer", self._on_turn_end, priority=200)
        wrap_bail(self, bus, _APPROVAL_NAME, self._turn_context)

    def _context_id(self) -> str:
        return getattr(self._bus, "context_id", "") if self._bus is not None else ""

    def _root_context(self) -> Any | None:
        return None if self._state.root is None else trace.set_span_in_context(self._state.root)

    def _turn_context(self) -> Any | None:
        if self._state.turn is None:
            return self._root_context()
        return trace.set_span_in_context(self._state.turn)

    def _delegate_link(self) -> list[Any] | None:
        if not (context_id := self._context_id()):
            return None
        with _GLOBAL_LOCK:
            parent_ctx = _DELEGATE_SPANS.get(_PARENT_BY_CONTEXT.get(context_id, ""))
        return None if parent_ctx is None or Link is None else [Link(parent_ctx)]

    def _attach_span(self, span: Any) -> Any | None:
        if attach is None or trace is None:
            return None
        return attach(trace.set_span_in_context(span))

    def _detach_span(self, token: Any | None) -> None:
        if token is None or detach is None:
            return
        detach(token)

    def _ensure_root(self) -> Any:
        with self._lock:
            if self._state.root is None:
                cid = self._context_id()
                self._state.root = self._start_span(
                    _ROOT_NAME,
                    attrs={"session_id": cid, "context_id": cid, "model": self.model},
                    links=self._delegate_link(),
                )
                self._state.root_token = self._attach_span(self._state.root)
            return self._state.root

    def _on_register(self, context_id: str, role: str, **_) -> None:
        if role != "subagent":
            return
        if parent_id := str(getattr(stack_context(), "parent_id", "") or ""):
            with _GLOBAL_LOCK:
                _PARENT_BY_CONTEXT[context_id] = parent_id

    def _on_root_start(self, **_) -> None:
        self._ensure_root()

    def _on_pre_step_capture(
        self,
        messages: list[dict],
        turn: int = 0,
        step: int = 0,
        **_,
    ) -> list[dict]:
        try:
            with self._lock:
                self._state.pre_steps[(turn, step)] = (len(messages), estimate_tokens(messages))
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel pre-step capture 失败: %s", exc)
        return messages

    def _on_pre_step_compare(
        self,
        messages: list[dict],
        turn: int = 0,
        step: int = 0,
        **_,
    ) -> list[dict]:
        try:
            with self._lock:
                before = self._state.pre_steps.get((turn, step))
            if before and len(messages) < before[0]:
                self._finish_span(
                    self._start_span(
                        _COMPRESS_NAME,
                        parent=self._turn_context(),
                        attrs={
                            "reason": "messages_shrunk",
                            "before_messages": before[0],
                            "after_messages": len(messages),
                            "before_tokens": before[1],
                            "after_tokens": estimate_tokens(messages),
                        },
                    ),
                    {"status": "compressed"},
                )
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel compress 失败: %s", exc)
        return messages

    def _on_pre_llm(self, messages: list[dict], turn: int, step: int, **_) -> None:
        try:
            self._ensure_root()
            with self._lock:
                if self._state.turn_no != turn:
                    self._state.turn = self._start_span(
                        _TURN_NAME,
                        parent=self._root_context(),
                        attrs={"turn": turn},
                    )
                    self._state.turn_token = self._attach_span(self._state.turn)
                    self._state.turn_no = turn
                self._state.llm_started[(turn, step)] = time.perf_counter()
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel pre-llm 失败: %s", exc)

    def _on_post_llm(
        self,
        result: Any,
        messages: list[dict] | None = None,
        turn: int = 0,
        step: int = 0,
        **_,
    ) -> None:
        try:
            usage = getattr(result, "usage", None) or {}
            started = self._state.llm_started.pop((turn, step), None)
            self._finish_span(
                self._start_span(
                    _LLM_NAME,
                    parent=self._turn_context(),
                    attrs={
                        "model": self.model,
                        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                        "total_tokens": int(usage.get("total_tokens", 0) or 0),
                        "latency_ms": int((time.perf_counter() - started) * 1000) if started else 0,
                        "status": "ok" if usage else "missing_usage",
                    },
                ),
                {},
            )
            if messages:
                self._maybe_write_memory(messages)
            with self._lock:
                bucket = self._state.turn_usage.setdefault(
                    turn, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                )
                bucket["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
                bucket["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
                bucket["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel post-llm 失败: %s", exc)

    def _maybe_write_memory(self, messages: list[dict]) -> None:
        if text := last_user_text(messages):
            for pattern, target in _MEMORY_PATTERNS:
                if pattern in text:
                    self._finish_span(
                        self._start_span(
                            _MEMORY_NAME,
                            parent=self._turn_context(),
                            attrs={"target": target, "match": pattern, "summary_len": len(text)},
                        ),
                        {"status": "written"},
                    )
                    return

    def _on_tool_call(
        self,
        name: str,
        arguments: dict,
        turn: int = 0,
        step: int = 0,
        **_,
    ) -> None:
        try:
            span = self._start_span(
                _TOOL_NAME,
                parent=self._turn_context(),
                attrs={
                    "tool.name": name,
                    "tool.args_summary": summarize_arguments(arguments or {}),
                    "status": "running",
                },
            )
            with self._lock:
                self._state.tool_spans.setdefault((turn, step), []).append(
                    PendingSpan(span, time.perf_counter())
                )
            if name == "delegate_task":
                with _GLOBAL_LOCK:
                    _DELEGATE_SPANS[self._context_id()] = span.get_span_context()
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel tool-call 失败: %s", exc)

    def _on_tool_result(
        self,
        name: str,
        arguments: dict,
        output: str,
        duration: float,
        turn: int = 0,
        step: int = 0,
        **_,
    ) -> None:
        try:
            with self._lock:
                pending = self._state.tool_spans.get((turn, step), [])
                record = pending.pop(0) if pending else None
            if record is None:
                return
            self._finish_span(
                record.span,
                {
                    "status": "blocked"
                    if str(output).startswith(_FAIL_PREFIXES)
                    else "ok",
                    "duration_ms": int(duration * 1000),
                },
            )
            if name == "delegate_task":
                with _GLOBAL_LOCK:
                    _DELEGATE_SPANS.pop(self._context_id(), None)
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel tool-result 失败: %s", exc)

    def _on_turn_end(self, reason: str = "completed", error: str | None = None, **_) -> None:
        try:
            with self._lock:
                turn_no = self._state.turn_no if self._state.turn_no is not None else -1
                usage = self._state.turn_usage.get(turn_no, {})
                turn_span = self._state.turn
                root_span = self._state.root
            if turn_span is not None:
                self._finish_span(turn_span, usage)
            if root_span is not None:
                self._finish_span(root_span, {})
            self._flush()
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel turn-end 失败: %s", exc)
        finally:
            with self._lock:
                turn_token = self._state.turn_token
                root_token = self._state.root_token
                self._state = State()
            self._detach_span(turn_token)
            self._detach_span(root_token)

    def _start_span(
        self,
        name: str,
        parent: Any | None = None,
        attrs: dict[str, Any] | None = None,
        links: list[Any] | None = None,
    ) -> Any:
        if not self.enabled or self._tracer is None:
            return NullSpan()
        span = self._tracer.start_span(name, context=parent, links=links or None)
        for key, value in (attrs or {}).items():
            span.set_attribute(key, value)
        return span

    def _finish_span(self, span: Any, attrs: dict[str, Any]) -> None:
        if isinstance(span, NullSpan):
            return
        try:
            for key, value in attrs.items():
                span.set_attribute(key, value)
            span.end()
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel span finish 失败: %s", exc)

    def _flush(self) -> None:
        if self._provider is None:
            return
        try:
            self._provider.force_flush()
        except Exception as exc:  # pragma: no cover
            _LOG.debug("OTel flush 失败: %s", exc)


def _factory(config: dict | None = None) -> TelemetryOtelPlugin:
    return TelemetryOtelPlugin(config)


register_plugin(
    name="telemetry_otel",
    factory=_factory,
    description="OTel 观测（事件→span 映射 + OTLP 批量导出）",
    default_enabled=False,
)
