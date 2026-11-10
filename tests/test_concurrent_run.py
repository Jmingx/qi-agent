"""并发 run 防护测试（2026-08-28 真实 bug：审批弹窗 → 同 context 并发 run）。

复现场景：审批（worker 线程交互）期间用户输入被主线程抢走 →
第二次 manager.run（同一 context）→ 消息交错 + 400 + 'value' KeyError。
修复：run 开始前检查 status==RUNNING → 拒绝并发。
"""

import threading
import time
import unittest.mock as mock

import pytest

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext


class _SlowClient:
    """慢 LLM（模拟审批等待——chat 内部 sleep）。"""

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        time.sleep(0.5)  # 模拟慢调用（审批/长任务）
        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def test_concurrent_run_rejected(tmp_path) -> None:
    """同 context 正在运行 → 第二次 run 被拒绝。"""
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient",
                      lambda key: _SlowClient()).start()
    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    # 第一个 run 在后台线程跑（模拟审批等待）
    t = threading.Thread(target=lambda: mgr.run(ctx.id, "任务1"))
    t.start()
    time.sleep(0.2)  # 确保第一个 run 已 RUNNING

    # 第二个 run（同 context）→ 应被拒绝
    with pytest.raises(RuntimeError, match="正在运行"):
        mgr.run(ctx.id, "任务2")

    t.join(timeout=5)


def test_run_after_complete_allowed(tmp_path) -> None:
    """第一个 run 完成后 → 第二次 run 正常（不误拒）。"""
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mock.patch.object(factory, "LLMClient",
                      lambda key: _SlowClient()).start()
    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")

    reply1 = mgr.run(ctx.id, "任务1")  # 同步完成（0.5s）
    assert reply1 == "ok"
    # 完成后 status 不是 RUNNING → 第二次允许
    reply2 = mgr.run(ctx.id, "任务2")
    assert reply2 == "ok"
