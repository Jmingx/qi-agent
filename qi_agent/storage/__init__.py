"""storage 包：持久化基础设施（方案 2026-08-26）。

导出统一接口（配置驱动选实现——业务只依赖抽象）：
  get_storage() -> Storage      默认 SQLite（会话消息日志）
  MemoryStore                    记忆（Markdown 分层：MEMORY.md + USER.md）
"""

from qi_agent.storage.base import Storage
from qi_agent.storage.memory_store import MemoryStore
from qi_agent.storage.sqlite_store import SQLiteStore

__all__ = ["Storage", "SQLiteStore", "MemoryStore", "get_storage"]

_default: Storage | None = None


def get_storage() -> Storage:
    """获取默认存储实例（单例——进程内复用连接配置）。

    配置驱动：未来可换实现（如 jsonl_store）——此处改返回即可。
    """
    global _default
    if _default is None:
        _default = SQLiteStore()
    return _default
