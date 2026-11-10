"""提炼 worker 集成测试（bug 修复验证——2026-08-27）。

复现 bug（用户真实对话 17 轮无记忆）：提炼 worker 的 LLM 输出
不带 [USER]/[MEMORY] 前缀 → 被静默丢弃 → 记忆写不进。
且 self.events 不存在（AgentManager）→ AttributeError 吞掉。
修复：格式容错（无前缀启发式判断）+ context.events + 事件留痕。
"""

import time
import unittest.mock as mock



class _ExtractClient:
    """模拟提炼 LLM：输出不带 [USER]/[MEMORY] 前缀（真实常见）。"""

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="用户喜欢简洁回答", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def test_extraction_writes_unprefixed_output(tmp_path) -> None:
    """17 轮 → 提炼触发 → 无前缀输出 → 仍写入（启发式判断）。"""
    import qi_agent.agents.factory as factory
    import qi_agent.storage.memory_store as ms
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext

    # 临时记忆目录
    ms._DEFAULT_DIR = str(tmp_path)
    factory.load_api_key = lambda: "sk-test"

    with mock.patch.object(factory, "LLMClient",
                           lambda key: _ExtractClient()), \
            mock.patch("qi_agent.llm.LLMClient",
                       lambda *a, **kw: _ExtractClient()):
        mgr = AgentManager()
        ctx = AgentContext()
        # 监听提炼事件（验证可观测——不再静默）
        events = []
        ctx.events.on("agent/memory-extracted",
                      lambda **kw: events.append(kw))
        ctx.events.on("agent/memory-extract-failed",
                      lambda **kw: events.append(kw))
        mgr.register(ctx, role="main")

        for i in range(17):
            mgr.run(ctx.id, f"第{i+1}轮")
        time.sleep(1.0)  # 等提炼 worker 完成

    # 提炼触发（第 10 轮）
    assert ctx.last_extract_turn == 10
    # 无前缀输出写入 USER.md（含"喜欢"→ user）
    user_entries = ms.MemoryStore().list_entries("user")
    assert any("简洁回答" in e for e in user_entries), f"USER.md: {user_entries}"
    # 事件留痕（memory-extracted 被发出）
    assert any("count" in e for e in events), f"事件: {events}"
