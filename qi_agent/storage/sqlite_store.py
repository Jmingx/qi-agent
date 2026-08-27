"""SQLite 存储实现（默认——stdlib sqlite3，零依赖）。

方案 2026-08-26-会话持久化与记忆系统：
- 双模型（快照 + append 日志）在 SQLite 里的落地：
  快照 = sessions 表【状态字段】+ snapshot_at 时间点（O(1) UPDATE）
  日志 = messages 表【逐条追加】（INSERT 天然 append）
- 恢复 = 读快照状态 + 重放快照之后的增量消息（Event Sourcing 标准）
- SQLite 事务保证：写一半崩溃 → 回滚不半写（对比 JSONL 追加损坏）

线程安全：每操作独立连接（SQLite 单写多读；check_same_thread=False
不需要——每次操作短连接，避免跨线程共享连接）。
"""

import json
import os
import sqlite3
import threading
import time

from qi_agent.storage.base import Storage


def _default_db_path() -> str:
    """默认数据目录：~/.qi-agent/qi.db（对齐 Hermes ~/.hermes）。"""
    home = os.path.expanduser("~")
    data_dir = os.path.join(home, ".qi-agent")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "qi.db")


class SQLiteStore(Storage):
    """SQLite 实现（双模型：状态字段快照 + 消息追加日志）。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        self._lock = threading.Lock()  # 写锁（SQLite 单写）
        self._init_schema()

    # ── 内部 ─────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """建表（幂等——IF NOT EXISTS）。"""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT DEFAULT '',
                    turn INTEGER DEFAULT 0,
                    usage TEXT DEFAULT '{}',
                    status TEXT DEFAULT '',
                    phase TEXT DEFAULT '',
                    snapshot_at REAL DEFAULT 0,
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    created_at REAL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, seq);
                """
            )

    # ── 会话 ─────────────────────────────────────────────────────────────
    def create_session(self, session_id: str, title: str = "") -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions"
                " (id, title, snapshot_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (session_id, title, now, now, now),
            )

    def append_message(self, session_id: str, message: dict) -> None:
        """追加日志（主写入）。message: {role, content, tool_calls?}"""
        now = time.time()
        with self._lock, self._connect() as conn:
            # 序号 = 当前最大 seq + 1（保证重放顺序）
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            seq = row[0] + 1
            conn.execute(
                "INSERT INTO messages"
                " (session_id, seq, role, content, tool_calls, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id, seq, message.get("role", ""),
                    message.get("content"),
                    json.dumps(message.get("tool_calls"), ensure_ascii=False)
                    if message.get("tool_calls") else None,
                    now,
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (now, session_id),
            )

    def snapshot(self, session_id: str, turn: int,
                 usage: dict | None = None,
                 status: str = "", phase: str = "") -> None:
        """打快照：更新会话状态字段（O(1)）+ 记录时间点。"""
        now = time.time()
        usage_json = json.dumps(usage or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET turn=?, usage=?, status=?, phase=?,"
                " snapshot_at=?, updated_at=? WHERE id=?",
                (turn, usage_json, status, phase, now, now, session_id),
            )

    def load_session(self, session_id: str) -> dict | None:
        """恢复：快照状态 + 增量重放（快照后的所有消息）。"""
        with self._connect() as conn:
            sess = conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if sess is None:
                return None
            # 快照时间点之后的消息（seq 全量——快照时 seq 已存，
            # 简化：快照状态字段已含 turn/usage，消息全量重放）
            rows = conn.execute(
                "SELECT role, content, tool_calls FROM messages"
                " WHERE session_id=? ORDER BY seq",
                (session_id,),
            ).fetchall()
        messages = []
        for row in rows:
            msg: dict = {"role": row["role"]}
            if row["content"] is not None:
                msg["content"] = row["content"]
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            messages.append(msg)
        return {
            "id": sess["id"],
            "title": sess["title"],
            "turn": sess["turn"],
            "usage": json.loads(sess["usage"] or "{}"),
            "status": sess["status"],
            "phase": sess["phase"],
            "messages": messages,
        }

    def list_sessions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, updated_at, snapshot_at FROM sessions"
                " ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {"id": r["id"], "title": r["title"],
             "updated_at": r["updated_at"]}
            for r in rows
        ]

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    # 注：记忆（MEMORY.md/USER.md）在 MemoryStore（Markdown 分层）——
    # SQLite 只存会话消息日志（方案 2026-08-26 修正）
