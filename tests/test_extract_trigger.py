"""提炼触发修复测试：context.turn 跨 chat 递增 → 每 10 轮触发提炼。

复现 bug：agent.chat 只递增 self._turn（agent 私有），context.turn 永不
递增 → _maybe_extract_memory 里 0-0<10 永远不触发提炼（用户手动聊 10 轮
无记忆——实测）。
"""

import threading
import unittest.mock as mock


class _FastClient:
    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def test_turn_increments_across_chats() -> None:
    """多次 chat 后 context.turn 递增（跨 chat 累计）。"""
    from qi_agent.agents.agent import Agent
    from qi_agent.context.context import AgentContext

    ctx = AgentContext()
    agent = Agent(_FastClient(), context=ctx)
    for _ in range(3):
        agent.chat("你好")
    assert ctx.turn == 3  # 修复前是 0（bug）


def test_extraction_triggers_at_10_turns() -> None:
    """10 轮对话 → 触发提炼（起后台线程）；9 轮内不触发。"""
    import qi_agent.agents.factory as factory
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient",
                      lambda key: _FastClient()).start()
    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    # 只拦截提炼线程（target 是 _extract_worker）——不拦截 run 的 worker
    extract_starts = []
    orig_thread = threading.Thread

    class _SelectiveThread(orig_thread):
        def start(self):
            # 提炼 worker（闭包函数名 _extract_worker）→ 记录不启动
            target_name = getattr(self._target, "__name__", "")
            if "_extract" in target_name:
                extract_starts.append(target_name)
                return  # 不启动（防真实 LLM 调用）
            return super().start()

    with mock.patch("threading.Thread", _SelectiveThread):
        for _ in range(9):
            mgr.run(ctx.id, f"问{_}")
        assert extract_starts == []  # 9 轮内未触发提炼
        # 第 10 轮：触发提炼（起提炼线程）
        mgr.run(ctx.id, "问10")
        assert len(extract_starts) >= 1  # 触发了提炼
    assert ctx.last_extract_turn == 10  # 防重复触发标记更新
