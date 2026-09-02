"""SQLite 持久化实现。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from qi_agent.storage.base import Storage


def _default_db_path() -> str:
    """默认数据库位置。"""
    home = os.path.expanduser("~")
    data_dir = os.path.join(home, ".qi-agent")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "qi.db")


class SQLiteStore(Storage):
    """使用 SQLite 保存会话快照和消息日志。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
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
                    data TEXT DEFAULT NULL,
                    created_at REAL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, seq);
                """
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "data" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN data TEXT DEFAULT NULL")

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
        """追加一条完整消息。"""
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            seq = row[0] + 1
            conn.execute(
                "INSERT INTO messages"
                " (session_id, seq, role, content, tool_calls, data, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    seq,
                    message.get("role", ""),
                    message.get("content"),
                    json.dumps(message.get("tool_calls"), ensure_ascii=False)
                    if message.get("tool_calls")
                    else None,
                    json.dumps(message, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (now, session_id),
            )

    def snapshot(
        self,
        session_id: str,
        turn: int,
        usage: dict | None = None,
        status: str = "",
        phase: str = "",
    ) -> None:
        now = time.time()
        usage_json = json.dumps(usage or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET turn=?, usage=?, status=?, phase=?,"
                " snapshot_at=?, updated_at=? WHERE id=?",
                (turn, usage_json, status, phase, now, now, session_id),
            )

    def load_session(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            sess = conn.execute(
                "SELECT * FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if sess is None:
                return None
            rows = conn.execute(
                "SELECT role, content, tool_calls, data FROM messages"
                " WHERE session_id=? ORDER BY seq",
                (session_id,),
            ).fetchall()

        messages = []
        for row in rows:
            if row["data"]:
                try:
                    messages.append(json.loads(row["data"]))
                    continue
                except (json.JSONDecodeError, TypeError):
                    pass
            msg: dict = {"role": row["role"]}
            if row["content"] is not None:
                msg["content"] = row["content"]
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            messages.append(msg)

        self._repair_tool_call_ids(messages)
        return {
            "id": sess["id"],
            "title": sess["title"],
            "turn": sess["turn"],
            "usage": json.loads(sess["usage"] or "{}"),
            "status": sess["status"],
            "phase": sess["phase"],
            "messages": messages,
        }

    @staticmethod
    def _repair_tool_call_ids(messages: list[dict]) -> None:
        """补齐老数据里的 tool_call_id。"""
        pending_ids: list[str] = []
        for msg in messages:
            if msg.get("role") == "assistant":
                pending_ids = [
                    tc.get("id")
                    for tc in (msg.get("tool_calls") or [])
                    if isinstance(tc, dict) and tc.get("id")
                ]
            elif msg.get("role") == "tool" and "tool_call_id" not in msg:
                if pending_ids:
                    msg["tool_call_id"] = pending_ids.pop(0)
                else:
                    msg["tool_call_id"] = f"call_repair_{len(messages)}"

    def list_sessions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, updated_at, snapshot_at FROM sessions"
                " ORDER BY updated_at DESC",
            ).fetchall()
        return [
            {"id": row["id"], "title": row["title"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    @staticmethod
    def _escape_like(text: str) -> str:
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def search_messages(self, query: str) -> list[dict]:
        pattern = f"%{self._escape_like(query)}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.session_id, s.title, m.role, m.content, m.created_at
                FROM messages AS m
                JOIN sessions AS s ON s.id = m.session_id
                WHERE COALESCE(m.content, '') LIKE ? ESCAPE '\\'
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT 50
                """,
                (pattern,),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "title": row["title"],
                "role": row["role"],
                "content": row["content"],
                "time": row["created_at"],
            }
            for row in rows
        ]
