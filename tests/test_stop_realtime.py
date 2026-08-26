"""/stop 实时中断测试（方案 2026-08-24-stop实时中断 Phase A）。

验证：wait_stop_or_done 双事件等待 + manager.run 后台线程实时中断
      + stop 后 pool 替换 agent（新 agent 接管同一 context，无状态替换）。
"""

import threading
import time


from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext


class _SlowClient:
    """慢 LLM 客户端：每轮 sleep（模拟长调用——stop 应实时打断）。"""

    def __init__(self, delay: float = 5.0):
        self.delay = delay
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        time.sleep(self.delay)  # 模拟慢 LLM（stop 前不会返回）
        return self._result()

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)

    def _result(self):
        from qi_agent.llm import ChatResult

        return ChatResult(content="done", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "done"},
                          usage=None)


class _FastClient:
    """快 LLM 客户端：立即返回（正常路径——stop 前已完成）。"""

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


# ── Phase A-1: wait_stop_or_done 双事件等待 ──────────────────────────────


def test_wait_returns_done_on_complete() -> None:
    """chat 完成 → wait_stop_or_done 返回 done。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.complete_chat()
    assert ctx.wait_stop_or_done(timeout=0.5) == "done"


def test_wait_returns_stopped_on_stop() -> None:
    """stop 触发 → wait_stop_or_done 返回 stopped（即使 done 也 set——stop 优先）。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.stop()  # stop 内部 set _done——wait 立即返回
    assert ctx.wait_stop_or_done(timeout=0.5) == "stopped"


def test_wait_returns_timeout() -> None:
    """既没完成也没 stop → 超时返回 timeout。"""
    ctx = AgentContext()
    ctx.begin_chat()
    assert ctx.wait_stop_or_done(timeout=0.1) == "timeout"


# ── Phase A-2: manager.run 实时中断 + pool 替换 ──────────────────────────


def _make_manager(client) -> AgentManager:
    """构造 manager（mock LLMClient——不走网络）。"""
    import unittest.mock as mock

    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")
    mock.patch.object(factory, "LLMClient", lambda key: client).start()
    return mgr, ctx


def test_run_stop_interrupts_realtime() -> None:
    """stop 后 manager.run 立即返回"已中断"（不等慢 LLM 5s）。"""
    mgr, ctx = _make_manager(_SlowClient(delay=5.0))

    # 后台触发 stop（模拟用户 /stop）——1s 后
    def _do_stop():
        time.sleep(0.3)
        mgr.stop(ctx.id)

    threading.Thread(target=_do_stop, daemon=True).start()
    start = time.perf_counter()
    reply = mgr.run(ctx.id, "你好")
    elapsed = time.perf_counter() - start

    assert reply == "已按指令中断当前任务。"
    assert elapsed < 2.0  # 实时（不等 5s 慢 LLM）
    assert mgr.pool.active_count == 0  # 旧 agent 已 release


def test_run_normal_completes() -> None:
    """正常路径：LLM 先完成 → 返回正常回复（零回归）。"""
    mgr, ctx = _make_manager(_FastClient())
    reply = mgr.run(ctx.id, "你好")
    assert reply == "ok"
    assert mgr.pool.active_count == 0


def test_run_stop_then_new_agent_takes_over() -> None:
    """stop 后新请求 → 新 agent 接管同一 context（无状态替换，数据无缝）。"""
    mgr, ctx = _make_manager(_SlowClient(delay=5.0))

    # 第一次：stop 中断
    def _do_stop():
        time.sleep(0.3)
        mgr.stop(ctx.id)

    threading.Thread(target=_do_stop, daemon=True).start()
    reply1 = mgr.run(ctx.id, "第一句")
    assert reply1 == "已按指令中断当前任务。"

    # 第二次：换快 client？不——同一 manager 的 pool 用同一个 factory。
    # 验证：context 数据保留（第一句已写入）+ 新 run 正常执行
    assert any(m["content"] == "第一句" for m in ctx.messages)
    # 新 run（快 client 替换——模拟换执行者）
    import unittest.mock as mock

    import qi_agent.agents.factory as factory

    mock.patch.object(factory, "LLMClient", lambda key: _FastClient()).start()
    reply2 = mgr.run(ctx.id, "第二句")
    assert reply2 == "ok"
    # 同一 context 数据连贯（两句话都在）
    contents = [m.get("content") for m in ctx.messages]
    assert "第一句" in contents
    assert "第二句" in contents
