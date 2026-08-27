"""tool 消息持久化完整性测试（bug 修复验证）。

复现 bug：append_message 只存 role/content/tool_calls 白名单 →
tool 消息的 tool_call_id 丢失 → 恢复后 DeepSeek API 400
（missing field 'tool_call_id'）。
修复：data 列全量序列化完整消息 dict，恢复优先用 data。
"""

from qi_agent.storage.sqlite_store import SQLiteStore


def test_tool_message_roundtrip_preserves_tool_call_id(tmp_path) -> None:
    """tool 消息持久化 → 恢复 → tool_call_id 保留。"""
    store = SQLiteStore(db_path=str(tmp_path / "qi.db"))
    store.create_session("ctx_1")
    tool_msg = {
        "role": "tool",
        "tool_call_id": "call_abc123",
        "content": "文件内容",
        "name": "read_file",
    }
    store.append_message("ctx_1", tool_msg)

    loaded = store.load_session("ctx_1")
    assert loaded is not None
    recovered = loaded["messages"][0]
    # 全字段保留（修复前 tool_call_id/name 丢失）
    assert recovered["role"] == "tool"
    assert recovered["tool_call_id"] == "call_abc123"
    assert recovered["name"] == "read_file"
    assert recovered["content"] == "文件内容"


def test_assistant_message_with_tool_calls_roundtrip(tmp_path) -> None:
    """assistant 消息（含 tool_calls）持久化 → 恢复 → 完整。"""
    store = SQLiteStore(db_path=str(tmp_path / "qi.db"))
    store.create_session("ctx_2")
    assistant_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_x",
            "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }],
    }
    store.append_message("ctx_2", assistant_msg)

    loaded = store.load_session("ctx_2")
    recovered = loaded["messages"][0]
    assert recovered["tool_calls"][0]["id"] == "call_x"
    assert recovered["tool_calls"][0]["function"]["name"] == "read_file"


def test_old_format_fallback(tmp_path) -> None:
    """旧格式（无 data 列）→ 回退 role/content/tool_calls 列。"""
    import sqlite3

    db_path = str(tmp_path / "qi.db")
    store = SQLiteStore(db_path=db_path)
    store.create_session("ctx_3")
    # 模拟旧数据：data 列为 NULL，只有分列
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO messages (session_id, seq, role, content, data)"
        " VALUES ('ctx_3', 1, 'user', '旧消息', NULL)")
    conn.commit()
    conn.close()

    loaded = store.load_session("ctx_3")
    assert loaded["messages"][0]["role"] == "user"
    assert loaded["messages"][0]["content"] == "旧消息"


def test_old_schema_migrates_data_column(tmp_path) -> None:
    """旧库（无 data 列）→ 打开后自动迁移加列（不炸）。"""
    import sqlite3

    db_path = str(tmp_path / "qi.db")
    # 建旧 schema（无 data 列）
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, title TEXT DEFAULT '', turn INTEGER DEFAULT 0,
            usage TEXT DEFAULT '{}', status TEXT DEFAULT '', phase TEXT DEFAULT '',
            snapshot_at REAL DEFAULT 0, created_at REAL DEFAULT 0,
            updated_at REAL DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, seq INTEGER NOT NULL,
            role TEXT NOT NULL, content TEXT, tool_calls TEXT,
            created_at REAL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id) VALUES ('ctx_old')")
    conn.execute(
        "INSERT INTO messages (session_id, seq, role, content)"
        " VALUES ('ctx_old', 1, 'user', '旧数据')")
    conn.commit()
    conn.close()

    # 打开（触发 _init_schema + _migrate）→ 不炸 + 能读旧数据
    store = SQLiteStore(db_path=db_path)
    loaded = store.load_session("ctx_old")
    assert loaded is not None
    assert loaded["messages"][0]["content"] == "旧数据"
    # data 列已迁移（新消息能全量序列化）
    store.append_message("ctx_old", {"role": "tool",
                                     "tool_call_id": "call_new",
                                     "content": "新"})
    loaded2 = store.load_session("ctx_old")
    tool_msg = [m for m in loaded2["messages"] if m["role"] == "tool"][0]
    assert tool_msg["tool_call_id"] == "call_new"


def test_repair_tool_call_id_from_assistant() -> None:
    """旧格式 tool 消息缺 tool_call_id → 从 assistant tool_calls 补。"""
    store = SQLiteStore(db_path=":memory:")
    messages = [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "get_time"}}]},
        {"role": "tool", "content": "10:00"},  # 缺 tool_call_id（旧格式）
    ]
    store._repair_tool_call_ids(messages)
    assert messages[1]["tool_call_id"] == "call_1"


def test_repair_placeholder_when_no_match() -> None:
    """无匹配 assistant → 生成占位 id（防 API 400）。"""
    store = SQLiteStore(db_path=":memory:")
    messages = [
        {"role": "tool", "content": "孤儿 tool 消息"},  # 前面无 assistant
    ]
    store._repair_tool_call_ids(messages)
    assert messages[0]["tool_call_id"].startswith("call_repair_")
