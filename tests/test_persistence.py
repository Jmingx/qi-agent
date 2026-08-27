"""持久化集成测试（方案 2026-08-26）：context + manager + storage 全链路。

验证：persist=True 的 context 经 manager.run 落盘（write-behind）
      → 新 manager + 新 context 恢复（load_session）→ 数据完整。
"""

import time


from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext
from qi_agent.storage.sqlite_store import SQLiteStore


class _FastClient:
    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage={"prompt_tokens": 3, "completion_tokens": 1,
                                 "total_tokens": 4})

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def _make_manager(store: SQLiteStore) -> AgentManager:
    import unittest.mock as mock

    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient", lambda key: _FastClient()).start()
    return AgentManager(storage=store)


def test_chat_persists_and_recovers(tmp_path) -> None:
    """chat 后落盘 → 新 manager 恢复（会话数据完整）。"""
    store = SQLiteStore(db_path=str(tmp_path / "qi.db"))
    mgr = _make_manager(store)

    ctx = AgentContext(persist=True)
    mgr.register(ctx, role="main")
    mgr.run(ctx.id, "你好")

    # 等 write-behind 异步落盘
    time.sleep(0.3)

    # 模拟进程重启：新 manager + 新 context（同 id）
    mgr2 = _make_manager(store)
    ctx2 = AgentContext(persist=True, context_id=ctx.id)
    mgr2.register(ctx2, role="main")

    loaded = store.load_session(ctx.id)
    assert loaded is not None
    assert loaded["turn"] == 1  # 快照状态
    assert loaded["usage"]["total_tokens"] == 4
    # 消息完整（system + user + assistant）
    roles = [m["role"] for m in loaded["messages"]]
    assert "user" in roles
    assert "assistant" in roles
    # 新 context 能继续（数据无缝）
    reply = mgr2.run(ctx2.id, "继续")
    assert reply == "ok"


def test_persist_false_does_not_write(tmp_path) -> None:
    """persist=False 不落盘（默认瞬态）。"""
    store = SQLiteStore(db_path=str(tmp_path / "qi.db"))
    mgr = _make_manager(store)

    ctx = AgentContext(persist=False)
    mgr.register(ctx, role="main")
    mgr.run(ctx.id, "你好")
    time.sleep(0.3)

    assert store.load_session(ctx.id) is None


def test_incremental_append_no_duplicate(tmp_path) -> None:
    """多次 run 增量 append（不重复写历史消息）。"""
    store = SQLiteStore(db_path=str(tmp_path / "qi.db"))
    mgr = _make_manager(store)

    ctx = AgentContext(persist=True)
    mgr.register(ctx, role="main")
    mgr.run(ctx.id, "第一句")
    time.sleep(0.3)
    mgr.run(ctx.id, "第二句")
    time.sleep(0.3)

    loaded = store.load_session(ctx.id)
    # 消息数 = 初始 system 1 + user 2 + assistant 2 = 5（无重复）
    contents = [m.get("content") for m in loaded["messages"]]
    assert contents.count("第一句") == 1
    assert contents.count("第二句") == 1
