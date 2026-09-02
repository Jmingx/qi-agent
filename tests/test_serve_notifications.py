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


def test_subtask_progress_is_forwarded_with_parent_session(monkeypatch) -> None:
    """子上下文事件应转成父会话的 item/subtaskProgress 通知。"""
    fake_logger = type(
        "FakeLogger",
        (),
        {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None},
    )()
    monkeypatch.setattr("qi_agent.events.get_events_logger", lambda: fake_logger)
    gateway = Gateway(manager=StubManager())
    _transport = ServeTransport(gateway)
    captured: list[dict] = []
    gateway.shell_callback = lambda payload: captured.append(json.loads(payload))

    parent = _make_context("ctx-parent")
    gateway.manager.register(parent, role="main")
    _transport._attach_context(parent)

    child = _make_context("agt-child")
    child.parent_id = parent.id
    gateway.manager.register(child, role="subagent")

    child.events.emit("agent/turn-start", user_input="goal")
    child.events.emit("agent/pre-llm", messages=[], tools=[], turn=1, step=0)
    child.events.bail("agent/tool-call", name="search", arguments={"query": "abc"})
    child.events.emit(
        "agent/tool-result",
        name="search",
        arguments={"query": "abc"},
        output="done",
        duration=0.125,
    )
    child.events.emit("agent/final-answer", content="finished")
    child.events.emit("agent/turn-end", reason="completed")

    assert [item["method"] for item in captured] == [
        "item/subtaskProgress",
        "item/subtaskProgress",
        "item/subtaskProgress",
        "item/subtaskProgress",
        "item/subtaskProgress",
        "item/subtaskProgress",
    ]
    assert all(item["params"]["session_id"] == parent.id for item in captured)
    assert all(item["params"]["sub_id"] == child.id for item in captured)
    assert captured[0]["params"]["event"] == "turn-start"
    assert captured[2]["params"]["event"] == "tool-call"
    assert captured[4]["params"]["event"] == "final-answer"


def test_new_context_with_same_session_id_still_attaches(monkeypatch) -> None:
    """同一个 session_id 的新 context 也必须重新挂监听器。"""
    fake_logger = type(
        "FakeLogger",
        (),
        {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None},
    )()
    monkeypatch.setattr("qi_agent.events.get_events_logger", lambda: fake_logger)
    gateway = Gateway(manager=StubManager())
    _transport = ServeTransport(gateway)
    captured: list[dict] = []
    gateway.shell_callback = lambda payload: captured.append(json.loads(payload))

    session_id = "ctx-shared"
    first = _make_context(session_id)
    gateway.manager.register(first, role="main")

    second = _make_context(session_id)
    gateway.manager.register(second, role="main")

    first.events.bail("agent/tool-call", name="search", arguments={"query": "one"})
    second.events.bail("agent/tool-call", name="search", arguments={"query": "two"})

    assert [item["params"]["arguments"]["query"] for item in captured] == ["one", "two"]


def test_attach_context_loads_plugins(monkeypatch) -> None:
    """serve 路径应在 context attach 时装配插件。"""

    fake_logger = type(
        "FakeLogger",
        (object,),
        {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None},
    )()
    monkeypatch.setattr("qi_agent.events.get_events_logger", lambda: fake_logger)

    plugin_config = {"telemetry_otel": {"enabled": True}}
    calls: list[tuple[str, dict]] = []
    seen: list[str] = []

    def _fake_load_plugin_config() -> dict:
        return plugin_config

    def _fake_load_plugins(bus, config):
        calls.append((bus.context_id, config))
        bus.on("agent/turn-start", lambda **_: seen.append(bus.context_id))
        return []

    monkeypatch.setattr("qi_agent.serve.load_plugin_config", _fake_load_plugin_config)
    monkeypatch.setattr("qi_agent.serve.load_plugins", _fake_load_plugins)

    gateway = Gateway(manager=StubManager())
    _transport = ServeTransport(gateway)
    context = _make_context("ctx-otel")
    gateway.manager.register(context, role="main")

    context.events.emit("agent/turn-start", user_input="hi")

    assert calls == [("ctx-otel", plugin_config)]
    assert seen == ["ctx-otel"]
