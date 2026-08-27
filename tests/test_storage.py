"""存储层测试（方案 2026-08-26-会话持久化与记忆系统）。

验证：SQLite 双模型（append 日志 + 快照 + 增量重放恢复）
      + 记忆 CRUD + 崩溃恢复。
"""


import pytest

from qi_agent.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def store(tmp_path) -> SQLiteStore:
    """临时 SQLite 存储（测试隔离，不写真实数据目录）。"""
    return SQLiteStore(db_path=str(tmp_path / "test.db"))


# ── 会话 CRUD ─────────────────────────────────────────────────────────────


def test_create_and_list_session(store: SQLiteStore) -> None:
    """创建会话 + 列表查询。"""
    store.create_session("ctx_abc", title="测试会话")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "ctx_abc"
    assert sessions[0]["title"] == "测试会话"


def test_append_and_load_messages(store: SQLiteStore) -> None:
    """追加消息日志 + 全量加载（无快照时重放全部）。"""
    store.create_session("ctx_1", title="对话")
    store.append_message("ctx_1", {"role": "user", "content": "你好"})
    store.append_message("ctx_1", {"role": "assistant", "content": "你好！"})

    msgs = store.load_session("ctx_1")["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_snapshot_then_incremental_replay(store: SQLiteStore) -> None:
    """快照 + 增量重放：快照后只重放新增消息（Event Sourcing 标准）。"""
    store.create_session("ctx_2", title="长对话")
    store.append_message("ctx_2", {"role": "user", "content": "第一句"})
    store.append_message("ctx_2", {"role": "assistant", "content": "回复1"})

    # 打快照（记录 turn + 状态）
    store.snapshot("ctx_2", turn=1, usage={"total_tokens": 100},
                   status="completed", phase="done")

    # 快照后追加（增量）
    store.append_message("ctx_2", {"role": "user", "content": "第二句"})
    store.append_message("ctx_2", {"role": "assistant", "content": "回复2"})

    loaded = store.load_session("ctx_2")
    assert loaded["turn"] == 1  # 快照状态
    assert loaded["usage"]["total_tokens"] == 100
    assert len(loaded["messages"]) == 4  # 快照前 2 + 增量 2（全量恢复）
    assert loaded["messages"][-1]["content"] == "回复2"  # 增量在


def test_crash_recovery(store: SQLiteStore) -> None:
    """崩溃恢复：模拟进程崩溃（不调 close）→ 新连接加载数据仍在。"""
    store.create_session("ctx_3", title="崩溃测试")
    store.append_message("ctx_3", {"role": "user", "content": "崩溃前的消息"})

    # 模拟崩溃：直接丢弃 store（不 close）→ 新实例打开同一文件
    store2 = SQLiteStore(db_path=store.db_path)
    msgs = store2.load_session("ctx_3")["messages"]
    assert len(msgs) == 1
    assert msgs[0]["content"] == "崩溃前的消息"


def test_message_order_preserved(store: SQLiteStore) -> None:
    """消息顺序保持（seq 序号）。"""
    store.create_session("ctx_4")
    for i in range(10):
        store.append_message("ctx_4", {"role": "user", "content": f"msg{i}"})
    msgs = store.load_session("ctx_4")["messages"]
    assert [m["content"] for m in msgs] == [f"msg{i}" for i in range(10)]


def test_delete_session(store: SQLiteStore) -> None:
    """删除会话（含消息）。"""
    store.create_session("ctx_5")
    store.append_message("ctx_5", {"role": "user", "content": "x"})
    store.delete_session("ctx_5")
    assert store.load_session("ctx_5") is None
    assert len(store.list_sessions()) == 0
    # 注：记忆 CRUD 测试移到 test_memory_store.py（Markdown 分层）
