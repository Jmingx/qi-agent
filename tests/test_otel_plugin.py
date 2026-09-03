"""OTel 观测插件测试。

覆盖 Phase 1 核心语义：
- 事件 → span 映射
- 脱敏：参数只出摘要，不泄露密钥/完整代码/原始 prompt
- 批量导出：turn-end 触发 flush，失败不炸主流程
- 零侵入：不启用时不影响原有事件语义
- 子任务链接：subagent root span 关联 parent delegate span
"""

from __future__ import annotations

from types import SimpleNamespace

from opentelemetry.sdk.trace.export import SpanExportResult

import qi_agent.plugins  # noqa: F401  导入即触发 builtin 注册

from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.registry import get_plugin_names


def _tool_result() -> ChatResult:
    """构造一次带工具调用的 LLM 返回。"""
    return ChatResult(
        content=None,
        tool_calls=[
            ToolCall(
                id="call_1",
                name="read_file",
                arguments={
                    "path": r"C:\tmp\secret.txt",
                    "api_key": "super-secret",
                    "code": "print('hello')",
                },
            )
        ],
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": (
                            '{"path":"C:\\\\tmp\\\\secret.txt",'
                            '"api_key":"super-secret","code":"print(\'hello\')"}'
                        ),
                    },
                }
            ],
        },
        usage={
            "prompt_tokens": 111,
            "completion_tokens": 22,
            "total_tokens": 133,
        },
    )


class _FakeExporter:
    """收集 BatchSpanProcessor 导出的 span。"""

    instances: list["_FakeExporter"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.batches: list[list] = []
        self.force_flush_called = 0
        self.shutdown_called = 0
        self.__class__.instances.append(self)

    def export(self, spans) -> SpanExportResult:
        self.batches.append(list(spans))
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int | None = None) -> bool:
        self.force_flush_called += 1
        return True

    def shutdown(self) -> bool:
        self.shutdown_called += 1
        return True


class _BoomExporter(_FakeExporter):
    """导出时抛错，验证插件必须吞掉异常。"""

    def export(self, spans) -> SpanExportResult:  # type: ignore[override]
        raise RuntimeError("collector down")


def _collect_spans() -> list:
    spans: list = []
    for exporter in _FakeExporter.instances:
        for batch in exporter.batches:
            spans.extend(batch)
    return spans


def _attrs(span) -> dict:
    return dict(span.attributes)


def _span_by_name(spans: list, name: str) -> object:
    return next(span for span in spans if span.name == name)


def test_plugin_is_registered() -> None:
    """builtin/import 链路应能注册 telemetry_otel。"""
    assert "telemetry_otel" in get_plugin_names()


def test_disabled_plugin_is_ignored() -> None:
    """未启用时应完全无副作用。"""
    from qi_agent.plugins.builtin import telemetry_otel as otel

    bus = EventBus(context_id="ctx-off")
    plugin = otel.TelemetryOtelPlugin({"enabled": False})
    plugin.install(bus)
    assert bus._listeners == {}


def test_batch_export_and_redaction(monkeypatch) -> None:
    """turn-end 触发批量导出；敏感参数只保留摘要。"""
    from qi_agent.plugins.builtin import telemetry_otel as otel

    _FakeExporter.instances.clear()
    monkeypatch.setattr(otel, "OTLPSpanExporter", _FakeExporter)
    plugin = otel.TelemetryOtelPlugin(
        {"enabled": True, "endpoint": "http://127.0.0.1:4318", "model": "test-model"}
    )
    bus = EventBus(context_id="ctx-123")
    plugin.install(bus)

    bus.emit("agent/turn-start", user_input="你好")
    bus.emit(
        "agent/pre-llm",
        messages=[{"role": "user", "content": "你好"}],
        tools=[{"function": {"name": "read_file"}}],
        turn=1,
        step=0,
    )
    bus.emit("agent/post-llm", result=_tool_result(), messages=[], turn=1, step=0)
    bus.bail(
        "agent/tool-call",
        name="read_file",
        arguments={
            "path": r"C:\tmp\secret.txt",
            "api_key": "super-secret",
            "code": "print('hello')",
        },
        turn=1,
        step=0,
    )
    bus.emit(
        "agent/tool-result",
        name="read_file",
        arguments={
            "path": r"C:\tmp\secret.txt",
            "api_key": "super-secret",
            "code": "print('hello')",
        },
        output="top secret content",
        duration=0.125,
    )
    bus.emit("agent/turn-end", reason="completed")

    spans = _collect_spans()
    names = {span.name for span in spans}
    assert {"agent/run-start", "agent/turn", "agent/post-llm", "agent/tool-call"} <= names

    root = _span_by_name(spans, "agent/run-start")
    turn = _span_by_name(spans, "agent/turn")
    llm = _span_by_name(spans, "agent/post-llm")
    tool = _span_by_name(spans, "agent/tool-call")

    assert _attrs(root)["session_id"] == "ctx-123"
    assert _attrs(root)["context_id"] == "ctx-123"
    assert _attrs(root)["model"] == "test-model"

    assert _attrs(turn)["turn"] == 1
    assert _attrs(turn)["prompt_tokens"] == 111
    assert _attrs(turn)["completion_tokens"] == 22
    assert _attrs(turn)["total_tokens"] == 133

    assert _attrs(llm)["prompt_tokens"] == 111
    assert _attrs(llm)["completion_tokens"] == 22
    assert _attrs(llm)["total_tokens"] == 133
    assert _attrs(llm)["status"] == "ok"
    assert _attrs(llm)["latency_ms"] >= 0
    assert llm.end_time is not None
    assert llm.start_time is not None
    assert llm.end_time > llm.start_time

    assert turn.parent is not None
    assert turn.parent.span_id == root.context.span_id
    assert llm.parent is not None
    assert llm.parent.span_id == turn.context.span_id
    assert tool.parent is not None
    assert tool.parent.span_id == turn.context.span_id

    tool_attrs = _attrs(tool)
    assert tool_attrs["tool.name"] == "read_file"
    assert tool_attrs["status"] == "ok"
    assert "super-secret" not in str(tool_attrs)
    assert "print('hello')" not in str(tool_attrs)
    assert "secret.txt" not in str(tool_attrs)

    exporter = _FakeExporter.instances[0]
    assert exporter.batches
    assert exporter.shutdown_called == 0


def test_tool_results_use_fifo_when_result_payload_lacks_step(monkeypatch) -> None:
    """真实 tool-result 不带 turn/step 时，也要按调用顺序把 span 收齐。"""
    from qi_agent.plugins.builtin import telemetry_otel as otel

    _FakeExporter.instances.clear()
    monkeypatch.setattr(otel, "OTLPSpanExporter", _FakeExporter)
    plugin = otel.TelemetryOtelPlugin(
        {"enabled": True, "endpoint": "http://127.0.0.1:4318", "model": "test-model"}
    )
    bus = EventBus(context_id="ctx-fifo")
    plugin.install(bus)

    bus.emit("agent/turn-start", user_input="hi")
    bus.emit(
        "agent/pre-llm",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        turn=1,
        step=0,
    )
    bus.bail(
        "agent/tool-call",
        name="shell",
        arguments={"command": "echo 1"},
        turn=1,
        step=0,
    )
    bus.bail(
        "agent/tool-call",
        name="get_time",
        arguments={},
        turn=1,
        step=1,
    )
    bus.emit(
        "agent/tool-result",
        name="shell",
        arguments={"command": "echo 1"},
        output="1",
        duration=0.01,
    )
    bus.emit(
        "agent/tool-result",
        name="get_time",
        arguments={},
        output="2026-09-03T00:00:00Z",
        duration=0.01,
    )
    bus.emit("agent/turn-end", reason="completed")

    spans = _collect_spans()
    tool_spans = [span for span in spans if span.name == "agent/tool-call"]
    assert len(tool_spans) == 2
    tool_names = [_attrs(span)["tool.name"] for span in tool_spans]
    assert tool_names == ["shell", "get_time"]
    assert [_attrs(span)["status"] for span in tool_spans] == ["ok", "ok"]


def test_compress_memory_and_approval_spans(monkeypatch) -> None:
    """压缩、记忆写入、审批 span 都应被记录，且脱敏。"""
    from qi_agent.plugins.builtin import telemetry_otel as otel

    _FakeExporter.instances.clear()
    monkeypatch.setattr(otel, "OTLPSpanExporter", _FakeExporter)
    plugin = otel.TelemetryOtelPlugin(
        {"enabled": True, "endpoint": "http://127.0.0.1:4318", "model": "test-model"}
    )
    bus = EventBus(context_id="ctx-cmp")
    plugin.install(bus)

    def _compress(messages: list[dict], **_) -> list[dict]:
        return messages[:1]

    bus.on("agent/pre-step", _compress, priority=0)
    bus.on("agent/tool-approval", lambda **_: True)
    bus.emit("agent/turn-start", user_input="请记住我喜欢蓝色")
    bus.waterfall(
        "agent/pre-step",
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "请记住我喜欢蓝色"},
            {"role": "assistant", "content": "ok"},
        ],
        turn=1,
        step=0,
    )
    bus.emit(
        "agent/pre-llm",
        messages=[{"role": "user", "content": "请记住我喜欢蓝色"}],
        tools=[],
        turn=1,
        step=0,
    )
    bus.emit(
        "agent/post-llm",
        result=ChatResult(
            content="记住了",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "记住了"},
            usage={"prompt_tokens": 200, "completion_tokens": 10, "total_tokens": 210},
        ),
        messages=[{"role": "user", "content": "请记住我喜欢蓝色"}],
        turn=1,
        step=0,
    )
    bus.bail(
        "agent/tool-approval",
        name="shell",
        arguments={"command": "rm -rf C:\\tmp\\x", "api_key": "super-secret"},
        command="rm -rf C:\\tmp\\x",
        code="SEC_APPROVAL_GENERAL",
        turn=1,
        step=0,
    )
    bus.emit("agent/turn-end", reason="completed")

    spans = _collect_spans()
    names = {span.name for span in spans}
    assert "context/compress" in names
    assert "memory/write" in names
    assert "agent/tool-approval" in names

    compress = _span_by_name(spans, "context/compress")
    memory = _span_by_name(spans, "memory/write")
    approval = _span_by_name(spans, "agent/tool-approval")

    assert _attrs(compress)["before_tokens"] > _attrs(compress)["after_tokens"]
    assert _attrs(memory)["target"] == "user"
    assert _attrs(approval)["result"] == "approved"
    assert "super-secret" not in str(_attrs(approval))


def test_subagent_delegation_links_parent_span(monkeypatch) -> None:
    """子任务 root span 应链接到 parent delegate span。"""
    from qi_agent.plugins.builtin import telemetry_otel as otel

    _FakeExporter.instances.clear()
    monkeypatch.setattr(otel, "OTLPSpanExporter", _FakeExporter)

    parent_plugin = otel.TelemetryOtelPlugin(
        {"enabled": True, "endpoint": "http://127.0.0.1:4318", "model": "parent-model"}
    )
    parent_bus = EventBus(context_id="ctx-parent")
    parent_plugin.install(parent_bus)

    parent_bus.emit("agent/turn-start", user_input="委派子任务")
    parent_bus.bail(
        "agent/tool-call",
        name="delegate_task",
        arguments={"goal": "查资料"},
        turn=1,
        step=0,
    )

    child_plugin = otel.TelemetryOtelPlugin(
        {"enabled": True, "endpoint": "http://127.0.0.1:4318", "model": "child-model"}
    )
    child_bus = EventBus(context_id="ctx-child")
    child_plugin.install(child_bus)

    def _emit_child_register() -> None:
        context = SimpleNamespace(id="ctx-child", parent_id="ctx-parent")
        child_bus.emit("agent-manager/register", context_id=context.id, role="subagent")

    _emit_child_register()
    child_bus.emit("agent/turn-start", user_input="开始子任务")
    child_bus.emit(
        "agent/pre-llm",
        messages=[{"role": "user", "content": "开始子任务"}],
        tools=[],
        turn=1,
        step=0,
    )
    child_bus.emit(
        "agent/post-llm",
        result=ChatResult(
            content="done",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "done"},
            usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        ),
        messages=[{"role": "user", "content": "开始子任务"}],
        turn=1,
        step=0,
    )
    child_bus.emit("agent/turn-end", reason="completed")
    parent_bus.emit(
        "agent/tool-result",
        name="delegate_task",
        arguments={"goal": "查资料"},
        output='{"summary":"done"}',
        duration=0.25,
        turn=1,
        step=0,
    )
    parent_bus.emit("agent/turn-end", reason="completed")

    spans = _collect_spans()
    child_root = next(
        span for span in spans
        if span.name == "agent/run-start" and _attrs(span)["session_id"] == "ctx-child"
    )
    assert len(child_root.links) == 1
    assert child_root.links[0].context.span_id is not None
    assert _attrs(child_root)["model"] == "child-model"


def test_export_failure_is_swallowed(monkeypatch) -> None:
    """导出失败不能抛出到主流程。"""
    from qi_agent.plugins.builtin import telemetry_otel as otel

    _FakeExporter.instances.clear()
    monkeypatch.setattr(otel, "OTLPSpanExporter", _BoomExporter)
    plugin = otel.TelemetryOtelPlugin(
        {"enabled": True, "endpoint": "http://127.0.0.1:4318", "model": "test-model"}
    )
    bus = EventBus(context_id="ctx-broken")
    plugin.install(bus)

    bus.emit("agent/turn-start", user_input="hi")
    bus.emit(
        "agent/pre-llm",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        turn=1,
        step=0,
    )
    bus.emit(
        "agent/post-llm",
        result=ChatResult(
            content="ok",
            tool_calls=None,
            assistant_message={"role": "assistant", "content": "ok"},
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
        messages=[{"role": "user", "content": "hi"}],
        turn=1,
        step=0,
    )
    bus.emit("agent/turn-end", reason="completed")
