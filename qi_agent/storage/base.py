"""Storage 抽象层。

业务层只依赖这里，不直接依赖 SQLite 或文件实现。
"""

from abc import ABC, abstractmethod


class Storage(ABC):
    """持久化存储接口。"""

    @abstractmethod
    def create_session(self, session_id: str, title: str = "") -> None:
        """创建会话记录。"""

    @abstractmethod
    def append_message(self, session_id: str, message: dict) -> None:
        """追加一条消息。"""

    @abstractmethod
    def snapshot(
        self,
        session_id: str,
        turn: int,
        usage: dict | None = None,
        status: str = "",
        phase: str = "",
    ) -> None:
        """更新会话快照字段。"""

    @abstractmethod
    def load_session(self, session_id: str) -> dict | None:
        """恢复会话完整数据。"""

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """列出所有会话。"""

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """删除会话及其消息。"""

    @abstractmethod
    def search_messages(self, query: str) -> list[dict]:
        """按内容模糊搜索消息。"""
