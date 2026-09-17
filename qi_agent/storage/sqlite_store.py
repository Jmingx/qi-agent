"""SQLite 持久化实现。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from qi_agent.storage.base import SessionPersistenceError, Storage


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
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    canonical_path TEXT NOT NULL UNIQUE,
                    created_at REAL DEFAULT 0,
                    updated_at REAL DEFAULT 0,
                    removed_at REAL DEFAULT NULL
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
        session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "workspace_id" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN workspace_id TEXT DEFAULT NULL")

    def create_session(
        self, session_id: str, title: str = "", workspace_id: str | None = None
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions"
                " (id, title, workspace_id, snapshot_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, title, workspace_id, now, now, now),
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
            self._insert_message(conn, session_id, seq, message, now)
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (now, session_id),
            )

    @staticmethod
    def _message_json(message: dict) -> str:
        """稳定编码完整消息，用于幂等对齐而非只比较 content。"""
        return json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _insert_message(
        cls,
        conn: sqlite3.Connection,
        session_id: str,
        seq: int,
        message: dict,
        created_at: float,
    ) -> None:
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
                cls._message_json(message),
                created_at,
            ),
        )

    @staticmethod
    def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
        text = str(exc).lower()
        return "database is locked" in text or "database is busy" in text

    def save_context(
        self,
        session_id: str,
        title: str,
        messages: list[dict],
        turn: int,
        usage: dict | None = None,
        status: str = "",
        phase: str = "",
    ) -> None:
        """在一个事务中保存可恢复的 Context。

        数据库消息是内存消息前缀时仅追加；压缩或 clear 改写了工作上下文时，
        事务内重建该会话消息，避免旧的 `_persisted_count` 把两种状态拼接。
        """
        try:
            encoded_messages = [self._message_json(message) for message in messages]
            usage_json = json.dumps(usage or {}, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SessionPersistenceError(f"会话保存序列化失败: {exc}") from exc

        with self._lock:
            for attempt in range(3):
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    now = time.time()
                    session = conn.execute(
                        "SELECT id FROM sessions WHERE id=?", (session_id,)
                    ).fetchone()
                    if session is None:
                        conn.execute(
                            "INSERT INTO sessions"
                            " (id, title, snapshot_at, created_at, updated_at)"
                            " VALUES (?, ?, ?, ?, ?)",
                            (session_id, title, now, now, now),
                        )

                    rows = conn.execute(
                        "SELECT data FROM messages WHERE session_id=? ORDER BY seq",
                        (session_id,),
                    ).fetchall()
                    persisted = [row["data"] or "" for row in rows]

                    if persisted == encoded_messages:
                        pass
                    elif len(persisted) < len(encoded_messages) and (
                        persisted == encoded_messages[:len(persisted)]
                    ):
                        new_messages = messages[len(persisted):]
                        for seq, message in enumerate(new_messages, len(persisted) + 1):
                            self._insert_message(conn, session_id, seq, message, now)
                    else:
                        conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
                        for seq, message in enumerate(messages, 1):
                            self._insert_message(conn, session_id, seq, message, now)

                    conn.execute(
                        "UPDATE sessions SET title=CASE WHEN ? <> '' THEN ? ELSE title END,"
                        " turn=?, usage=?, status=?, phase=?, snapshot_at=?, updated_at=?"
                        " WHERE id=?",
                        (title, title, turn, usage_json, status, phase, now, now, session_id),
                    )
                    conn.commit()
                    return
                except sqlite3.OperationalError as exc:
                    conn.rollback()
                    if self._is_busy_error(exc) and attempt < 2:
                        time.sleep(0.02 * (2**attempt))
                        continue
                    raise SessionPersistenceError(
                        f"SQLite 会话保存失败: {exc}"
                    ) from exc
                except Exception as exc:
                    conn.rollback()
                    raise SessionPersistenceError(f"会话保存失败: {exc}") from exc
                finally:
                    conn.close()

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
            "workspace_id": sess["workspace_id"] if "workspace_id" in sess.keys() else None,
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
        """列出会话（附带消息数，供 Web 侧折叠空壳会话）。

        message_count 用 LEFT JOIN 一次查出——UI 需要用它区分「有内容的会话」与
        「打开页面时惰性创建的空壳会话」（空壳会淹没列表，见 UI v3 方案 §4.4）。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.id, s.title, s.updated_at, s.turn, s.workspace_id,"
                " w.label AS workspace_label, w.removed_at,"
                " COUNT(m.id) AS message_count"
                " FROM sessions s LEFT JOIN messages m ON m.session_id = s.id"
                " LEFT JOIN workspaces w ON w.id = s.workspace_id"
                " GROUP BY s.id, s.title, s.updated_at, s.turn, s.workspace_id,"
                " w.label, w.removed_at"
                " ORDER BY s.updated_at DESC",
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "updated_at": row["updated_at"],
                "turn": row["turn"],
                "message_count": row["message_count"],
                "workspace_id": row["workspace_id"],
                "workspace_label": row["workspace_label"],
                "workspace_available": bool(
                    row["workspace_id"] and row["workspace_label"] and row["removed_at"] is None
                ),
            }
            for row in rows
        ]

    def add_workspace(self, workspace_id: str, label: str, canonical_path: str) -> dict:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO workspaces (id, label, canonical_path, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(canonical_path) DO UPDATE"
                " SET label=excluded.label, removed_at=NULL, updated_at=excluded.updated_at",
                (workspace_id, label, canonical_path, now, now),
            )
            row = conn.execute(
                "SELECT * FROM workspaces WHERE canonical_path=?", (canonical_path,)
            ).fetchone()
        return dict(row)

    def list_workspaces(self, include_removed: bool = False) -> list[dict]:
        query = (
            "SELECT * FROM workspaces"
            + ("" if include_removed else " WHERE removed_at IS NULL")
            + " ORDER BY label, canonical_path"
        )
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id=? AND removed_at IS NULL", (workspace_id,)
            ).fetchone()
        return dict(row) if row else None

    def remove_workspace(self, workspace_id: str) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "UPDATE workspaces SET removed_at=?, updated_at=?"
                " WHERE id=? AND removed_at IS NULL",
                (time.time(), time.time(), workspace_id),
            )
        return result.rowcount > 0

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
