import uuid
from pathlib import Path
from types import SimpleNamespace

from qi_agent.context.context import AgentContext, ContextStatus
from qi_agent.gateway.gateway import Gateway
from qi_agent.storage.sqlite_store import SQLiteStore


def _db_path(name: str) -> Path:
    root = Path.home() / "AppData" / "Local" / "Temp" / "qi-agent-tests"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{name}-{uuid.uuid4().hex}.db"


class StubManager:
    def __init__(self, storage: SQLiteStore | None = None) -> None:
        self.contexts: dict[str, AgentContext] = {}
        self.storage = storage

    def register(self, context: AgentContext, role: str = "subagent") -> str:
        self.contexts[context.id] = context
        return context.id

    def unregister(self, context_id: str) -> None:
        self.contexts.pop(context_id, None)

    def get_context(self, context_id: str) -> AgentContext | None:
        return self.contexts.get(context_id)

    def spawn(self, goal: str, parent_id: str = "") -> SimpleNamespace:
        return SimpleNamespace(id="sub-1", goal=goal, parent_id=parent_id)


def _build_context(session_id: str, system_prompt: str = "system prompt") -> AgentContext:
    context = AgentContext(persist=True, context_id=session_id)
    context.system_prompt = system_prompt
    context.messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "follow up"},
    ]
    context.turn = 3
    context.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return context


def test_delegate_async_returns_immediately() -> None:
    manager = StubManager()
    gateway = Gateway(manager=manager)
    context = _build_context("ctx-delegate")
    manager.register(context, role="main")

    result = gateway._delegate_async(session_id=context.id, goal="do work")

    assert result == {"sub_id": "sub-1", "status": "spawned"}


def test_context_history_paginates() -> None:
    manager = StubManager()
    gateway = Gateway(manager=manager)
    context = _build_context("ctx-history")
    manager.register(context, role="main")

    result = gateway._context_history(session_id=context.id, offset=1, limit=2)

    assert result["total"] == 4
    assert [message["content"] for message in result["messages"]] == [
        "hello world",
        "answer one",
    ]


def test_context_usage_prefers_real_usage_and_falls_back_to_estimate() -> None:
    manager = StubManager()
    gateway = Gateway(manager=manager)
    context = _build_context("ctx-usage")
    manager.register(context, role="main")

    context.usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    actual = gateway._context_usage(session_id=context.id)
    assert actual["prompt_tokens"] == 11
    assert actual["completion_tokens"] == 7
    assert actual["total_tokens"] == 18

    context.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    estimated = gateway._context_usage(session_id=context.id)
    assert estimated["prompt_tokens"] > 0
    assert estimated["completion_tokens"] == 0
    assert estimated["total_tokens"] == estimated["prompt_tokens"]
    assert estimated["est_ratio"] > 0
    assert estimated["context_limit"] > 0


def test_session_delete_clears_manager_and_storage(monkeypatch) -> None:
    fake_logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("qi_agent.events.get_events_logger", lambda: fake_logger)
    store = SQLiteStore(db_path=str(_db_path("delete")))
    manager = StubManager(storage=store)
    gateway = Gateway(manager=manager)
    context = _build_context("ctx-delete")
    context.status = ContextStatus.RUNNING
    manager.register(context, role="main")
    store.create_session(context.id, title="Delete me")
    store.append_message(context.id, {"role": "user", "content": "persisted"})
    store.snapshot(context.id, turn=1, usage={}, status="running", phase="turn_start")

    result = gateway._session_delete(session_id=context.id)

    assert result == {"ok": True}
    assert context.status == ContextStatus.STOPPED
    assert context.id not in manager.contexts
    assert store.load_session(context.id) is None


def test_context_clear_rebuilds_storage_sync() -> None:
    store = SQLiteStore(db_path=str(_db_path("clear")))
    manager = StubManager(storage=store)
    gateway = Gateway(manager=manager)
    context = _build_context("ctx-clear", system_prompt="keep this")
    manager.register(context, role="main")
    store.create_session(context.id, title="Before clear")
    store.append_message(context.id, {"role": "user", "content": "old content"})
    store.snapshot(context.id, turn=3, usage={}, status="running", phase="llm_call")

    result = gateway._context_clear(session_id=context.id)
    loaded = store.load_session(context.id)

    assert result == {"ok": True, "messages": 1}
    assert context.messages == [{"role": "system", "content": "keep this"}]
    assert context.turn == 0
    assert loaded is not None
    assert loaded["turn"] == 0
    assert len(loaded["messages"]) == 1
    assert loaded["messages"][0]["content"] == "keep this"


def test_session_search_uses_like_escape_and_limit() -> None:
    store = SQLiteStore(db_path=str(_db_path("search")))
    manager = StubManager(storage=store)
    gateway = Gateway(manager=manager)

    store.create_session("sess-a", "A")
    store.append_message("sess-a", {"role": "user", "content": "needle 1"})
    store.create_session("sess-b", "B")
    store.append_message("sess-b", {"role": "user", "content": "needle 2"})
    store.create_session("sess-percent", "Percent")
    store.append_message("sess-percent", {"role": "user", "content": "100% literal"})
    store.create_session("sess-percent-2", "Percent 2")
    store.append_message("sess-percent-2", {"role": "user", "content": "100X literal"})

    results = gateway._session_search(query="100%")
    assert len(results["results"]) == 1
    assert results["results"][0]["session_id"] == "sess-percent"

    for index in range(60):
        session_id = f"sess-limit-{index}"
        store.create_session(session_id, f"Limit {index}")
        store.append_message(session_id, {"role": "user", "content": f"needle {index}"})
    capped = gateway._session_search(query="needle")
    assert len(capped["results"]) == 50


def test_memory_remove_proxies_to_memory_store(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeMemoryStore:
        def remove_memory(self, text: str, target: str = "memory") -> None:
            calls.append((text, target))

    monkeypatch.setattr("qi_agent.storage.memory_store.MemoryStore", FakeMemoryStore)
    gateway = Gateway(manager=StubManager())

    result = gateway._memory_remove(text="old note", target="user")

    assert result == {"ok": True}
    assert calls == [("old note", "user")]
