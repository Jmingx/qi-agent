import json

from qi_agent.context.context import AgentContext
from qi_agent.gateway.gateway import Gateway
from qi_agent.tools.decision import ToolAction, ToolDecision
from qi_agent.serve import ServeTransport


class StubManager:
    def __init__(self) -> None:
        self.contexts: dict[str, AgentContext] = {}
        self.storage = None

    def register(self, context: AgentContext, role: str = "subagent") -> str:
        self.contexts[context.id] = context
        return context.id


def _make_context(session_id: str) -> AgentContext:
    context = AgentContext(persist=False, context_id=session_id)
    context.messages = [{"role": "system", "content": "system"}]
    return context


def test_tool_notifications_and_turn_end_are_forwarded(monkeypatch) -> None:
    fake_logger = type(
        "FakeLogger",
        (),
        {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None},
    )()
    monkeypatch.setattr("qi_agent.events.get_events_logger", lambda: fake_logger)
    gateway = Gateway(manager=StubManager())
    transport = ServeTransport(gateway)
    captured: list[dict] = []
    gateway.shell_callback = lambda payload: captured.append(json.loads(payload))

    context = _make_context("ctx-events")
    gateway.manager.register(context, role="main")
    transport._attach_context(context)

    context.events.bail("agent/tool-call", name="search", arguments={"query": "abc"})
    context.events.emit(
        "agent/tool-result",
        name="search",
        arguments={"query": "abc"},
        output="done",
        duration=0.125,
    )
    context.events.emit("agent/turn-end", reason="error", error="boom")

    assert captured[0]["method"] == "item/toolCall"
    assert captured[0]["params"]["status"] == "running"
    assert captured[0]["params"]["name"] == "search"
    assert captured[1]["method"] == "item/toolResult"
    assert captured[1]["params"]["ok"] is True
    assert captured[1]["params"]["summary"] == "done"
    assert captured[1]["params"]["duration_ms"] == 125
    assert captured[2]["method"] == "turn/end"
    assert captured[2]["params"]["reason"] == "error"
    assert captured[2]["params"]["error"] == "boom"


def test_blocked_tool_call_sets_blocked_status(monkeypatch) -> None:
    fake_logger = type(
        "FakeLogger",
        (),
        {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None},
    )()
    monkeypatch.setattr("qi_agent.events.get_events_logger", lambda: fake_logger)
    gateway = Gateway(manager=StubManager())
    transport = ServeTransport(gateway)
    captured: list[dict] = []
    gateway.shell_callback = lambda payload: captured.append(json.loads(payload))

    context = _make_context("ctx-blocked")
    gateway.manager.register(context, role="main")
    transport._attach_context(context)

    context.events.on(
        "agent/tool-call",
        lambda **_: ToolDecision(ToolAction.BLOCK, reason="nope"),
        priority=10,
    )
    context.events.bail("agent/tool-call", name="write_file", arguments={"path": "x"})

    assert captured[0]["method"] == "item/toolCall"
    assert captured[0]["params"]["status"] == "blocked"
    assert captured[0]["params"]["reason"] == "nope"
