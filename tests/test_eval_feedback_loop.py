from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qi_agent.events import EventBus
from qi_agent.gateway.gateway import Gateway
from qi_agent.serve import ServeTransport
from evaluation.case_models import CaseResult, EvalCase, validate_case


class StubManager:
    def __init__(self) -> None:
        self.contexts: dict[str, Any] = {}
        self.storage = None

    def register(self, context: Any, role: str = "subagent") -> str:
        self.contexts[context.id] = context
        return context.id


class StubContext:
    def __init__(self, session_id: str, prompt: str) -> None:
        self.id = session_id
        self.parent_id = None
        self.goal = prompt
        self.turn = 1
        self.usage = {"total_tokens": 42}
        self.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": prompt},
        ]
        self.events = EventBus(context_id=session_id)


def _make_transport(monkeypatch, tmp_path: Path) -> ServeTransport:
    fake_logger = type(
        "FakeLogger",
        (),
        {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None},
    )()
    monkeypatch.setattr("qi_agent.events.get_events_logger", lambda: fake_logger)
    monkeypatch.setattr("qi_agent.serve.load_plugin_config", lambda: {})
    monkeypatch.setattr("qi_agent.serve.load_plugins", lambda *args, **kwargs: [])
    monkeypatch.setattr("qi_agent.serve._eval_feed_path", lambda: tmp_path / "feed.jsonl")
    gateway = Gateway(manager=StubManager())
    gateway.shell_callback = lambda payload: None
    return ServeTransport(gateway)


def _drive_dialog(
    transport: ServeTransport,
    session_id: str,
    prompt: str,
    reply: str,
    *,
    tool_output: str | None = None,
    tool_name: str = "search",
    llm_calls: int = 1,
) -> StubContext:
    context = StubContext(session_id, prompt)
    context.events._qi_telemetry_trace_id = f"trace-{session_id}"
    transport._make_feed_turn_start_handler(context)()
    for _ in range(llm_calls):
        transport._make_feed_pre_llm_handler(context)()
    if tool_output is not None:
        transport._make_tool_result_handler(context)(
            name=tool_name,
            arguments={"query": prompt},
            output=tool_output,
            duration=0.125,
        )
    transport._make_final_answer_handler(context)(content=reply)
    return context


def test_feed_logging_writes_desensitized_rows(monkeypatch, tmp_path: Path) -> None:
    transport = _make_transport(monkeypatch, tmp_path)

    _drive_dialog(transport, "ok", "查天气", "今天天气晴朗")
    _drive_dialog(transport, "err", "查订单", "继续排查", tool_output="[工具错误] timeout")
    _drive_dialog(transport, "flag", "问答", "抱歉，我不确定")

    feed_path = tmp_path / "feed.jsonl"
    rows = [json.loads(line) for line in feed_path.read_text(encoding="utf-8").splitlines()]

    assert [row["session_id"] for row in rows] == ["ok", "err", "flag"]
    assert rows[0]["verdict"] == "ok"
    assert rows[1]["verdict"] == "error"
    assert rows[2]["verdict"] == "flagged"
    assert rows[1]["tool_errors"] == 1
    assert rows[1]["signals"]["auto_error"] is True
    assert rows[0]["prompt"] == "查天气"
    assert rows[0]["reply_excerpt"] == "今天天气晴朗"
    assert rows[0]["tools_used"] == []
    assert rows[1]["tools_used"] == ["search"]
    assert rows[0]["trace_id"] == "trace-ok"


def test_validate_case_supports_no_tool_and_expected_calls() -> None:
    case = EvalCase(
        case_id="case-1",
        prompt="prompt",
        must_use_tools=(),
        must_not_use_tools=(),
        reply_contains_any=(),
        reply_regex="",
        max_turns=2,
        expect_tool_calls=(
            [{"name": "search", "arguments": {"query": "abc"}}],
        ),
        no_tool=False,
    )

    assert validate_case(
        case,
        reply="ok",
        turns=1,
        tools_used=["search"],
        tool_calls=[{"name": "search", "arguments": {"query": "abc", "limit": 1}}],
    ) == []

    no_tool_case = EvalCase(
        case_id="case-2",
        prompt="prompt",
        must_use_tools=(),
        must_not_use_tools=(),
        reply_contains_any=(),
        reply_regex="",
        max_turns=2,
        expect_tool_calls=(),
        no_tool=True,
    )

    assert validate_case(no_tool_case, reply="ok", turns=1, tools_used=[], tool_calls=[]) == []
    failures = validate_case(
        no_tool_case,
        reply="ok",
        turns=1,
        tools_used=["search"],
        tool_calls=[{"name": "search", "arguments": {}}],
    )
    assert any("不应调用工具" in item for item in failures)


def test_case_result_contains_latency_and_trace_links() -> None:
    result = CaseResult(
        case_id="time_tool",
        session_id="ctx-1",
        reply="现在是 12 点。",
        turns=1,
        prompt_tokens=10,
        completion_tokens=4,
        total_tokens=14,
        elapsed_s=1.234,
        estimated_cost_rmb=0.01,
        tools_used=["get_time"],
        passed=True,
        failures=[],
        latency_ms=1234,
        jaeger_trace_id="jaeger-1",
        opik_trace_id="opik-1",
    )

    payload = result.to_experiment_payload()
    assert payload["latency_ms"] == 1234
    assert payload["jaeger_trace_id"] == "jaeger-1"
    assert payload["opik_trace_id"] == "opik-1"
