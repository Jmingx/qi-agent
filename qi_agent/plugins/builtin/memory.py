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
    """长期记忆插件：pre-step 注入记忆 + 规则触发自动记忆。"""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    def install(self, bus: EventBus) -> None:
        """挂 pre-step 瀑布（注入）+ post-llm（规则触发自动记忆）。"""
        bus.on("agent/pre-step", _inject_memories, priority=50)
        bus.on("agent/post-llm", _auto_remember, priority=50)


# ── 规则触发自动记忆（方案 2026-08-26-主动记忆 V1）────────────────────────
# 检测"值得记"的模式（用户偏好/项目决策）——无需审批直接写记忆文件

# 用户偏好模式："我喜欢 X" / "我习惯 X" / "请记住 X" → USER.md
_USER_PREF_PATTERNS = (
    ("我喜欢", "user"),
    ("我习惯", "user"),
    ("请记住", "user"),
    ("我的爱好", "user"),
    ("我最爱", "user"),
)
# 项目决策模式："我们决定 X" / "以后用 X" → MEMORY.md
_PROJECT_PATTERNS = (
    ("我们决定", "memory"),
    ("我们约定", "memory"),
    ("以后用", "memory"),
    ("以后都用", "memory"),
)


def _auto_remember(messages: list[dict], **extra) -> None:
    """post-llm 监听：检测用户输入中的"值得记"模式 → 自动写记忆。

    用户输入从 messages 最后一条 user 消息提取（post-llm payload）。
    无需审批（用户明说"记住"/表达偏好 = 授权——方案 D3）。
    失败容错：写失败不影响对话。
    """
    if not messages:
        return
    # 取最后一条 user 消息作为用户输入
    user_input = ""
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            user_input = msg["content"]
            break
    if not user_input:
        return
    try:
        store = MemoryStore()
        for pattern, target in _USER_PREF_PATTERNS + _PROJECT_PATTERNS:
            if pattern in user_input:
                # 提取模式后的内容作为记忆（截断到合理长度）
                idx = user_input.find(pattern)
                text = user_input[idx:].strip()[:200]
                store.add_memory(text, target=target)
                return  # 一条输入只触发一次（防多写）
    except Exception:
        pass  # 记忆写失败不影响对话（容错）


# 注册（函数调用形式——对齐 debug_logger 等内置插件）
register_plugin("memory", MemoryPlugin, "长期记忆注入（跨会话知识）")
