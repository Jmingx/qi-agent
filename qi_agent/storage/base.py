"""存储抽象基类（基础设施——业务只依赖抽象，不依赖具体实现）。

方案 2026-08-26-会话持久化与记忆系统：
- 持久化是基础设施（用户拍板独立包）——可复用/可替换/职责清晰
- 业务层（context/cli）只依赖本抽象 → 换存储 = 换实现类（配置驱动）

双模型（用户拍板快照 + append 日志）：
- append_message：追加日志（主写入，Event Sourcing 事件流）
- snapshot：打快照（状态字段存档，加速恢复）
- load_session：快照 + 增量重放（恢复 = 最近快照 + 快照后的日志）
"""

from abc import ABC, abstractmethod


class Storage(ABC):
    """存储抽象接口。"""

    @abstractmethod
    def create_session(self, session_id: str, title: str = "") -> None:
        """创建会话记录。"""

    @abstractmethod
    def append_message(self, session_id: str, message: dict) -> None:
        """追加一条消息到日志（主写入——write-behind 异步调用）。"""

    @abstractmethod
    def snapshot(self, session_id: str, turn: int,
                 usage: dict | None = None,
                 status: str = "", phase: str = "") -> None:
        """打快照：更新会话状态字段（O(1)——只 UPDATE 一行）。"""

    @abstractmethod
    def load_session(self, session_id: str) -> dict | None:
        """恢复会话：快照状态 + 增量重放（Event Sourcing 标准）。

        Returns:
            {"id", "title", "turn", "usage", "status", "phase",
             "messages": [...]} 或 None（不存在）
        """

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """会话列表（/resume 用：id/title/updated_at）。"""

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """删除会话（含消息）。"""
    # 注：记忆（MEMORY.md/USER.md）不在 Storage——Markdown 分层存储
    # （MemoryStore 独立——SQLite 只存会话消息日志）
