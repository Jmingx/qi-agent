"""记忆插件（方案 2026-08-26-会话持久化与记忆系统，Markdown 分层修正）。

设计：
- 长期记忆（跨会话知识）在 LLM 调用前注入 system prompt 副本
- 注入走 pre-step waterfall（副本改写——不污染 self.messages，
  对齐 todo 注入模式；Hermes format_for_injection 同款）
- 存储：Markdown 文件（MEMORY.md + USER.md——可读可编辑 + LLM 友好），
  不用 SQLite（业界实证：Hermes MEMORY.md/USER.md、CC CLAUDE.md）
- 来源：/remember 命令写 MEMORY.md 分节（+ sticky 会话内）

与 sticky 的区别：
  sticky = 当前会话内关键信息（永不裁剪）
  memory = 跨会话长期知识（重启后仍在，Markdown 文件）

配置（plugins.toml）：[memory] enabled = true（默认开）
"""

from qi_agent.events import EventBus
from qi_agent.plugins.registry import register_plugin
from qi_agent.storage.memory_store import MemoryStore

# 注入标记（system prompt 里的记忆区块头）
_MEMORY_HEADER = "以下是跨会话记住的信息（长期记忆）："


def _inject_memories(messages: list[dict], **extra) -> list[dict]:
    """pre-step 瀑布：把长期记忆注入消息副本（不污染原列表）。

    记忆注入位置：system 消息末尾（前缀稳定区——prompt caching 友好）。
    """
    try:
        memory_text = MemoryStore().read_memory()
    except Exception:
        return messages  # 存储不可用 → 不注入（容错）
    if not memory_text:
        return messages

    memory_block = f"{_MEMORY_HEADER}\n{memory_text}"

    # 副本操作（不污染 self.messages——浅拷贝再改）
    result = list(messages)
    for i, msg in enumerate(result):
        if msg.get("role") == "system":
            # system 消息末尾追加记忆块（保持前缀稳定）
            new_content = f"{msg.get('content', '')}\n\n{memory_block}"
            result[i] = {**msg, "content": new_content}
            return result
    # 无 system 消息 → 头部插入
    result.insert(0, {"role": "system", "content": memory_block})
    return result


class MemoryPlugin:
    """长期记忆插件：pre-step 注入记忆。"""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    def install(self, bus: EventBus) -> None:
        """挂 pre-step 瀑布（优先级 50——记忆注入在裁剪之后、LLM 之前）。"""
        bus.on("agent/pre-step", _inject_memories, priority=50)


# 注册（函数调用形式——对齐 debug_logger 等内置插件）
register_plugin("memory", MemoryPlugin, "长期记忆注入（跨会话知识）")
