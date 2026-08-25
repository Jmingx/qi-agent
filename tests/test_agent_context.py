"""AgentContext 统一运行环境测试（方案 2026-08-24-AgentContext统一合并）。

验证：统一 Context 的状态机/控制面/数据载体职责（主/子 agent 共用）。
"""

import threading
import time

from qi_agent.context.context import AgentContext, ContextStatus


def test_initial_state() -> None:
    """初始状态：RUNNING + 空消息 + 空用量 + 0 轮。"""
    ctx = AgentContext(persist=True)
    assert ctx.status == ContextStatus.RUNNING
    assert ctx.messages == []
    assert ctx.usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert ctx.turn == 0


def test_auto_id() -> None:
    """自动生成 id（12 位 hex）。"""
    ctx1 = AgentContext()
    ctx2 = AgentContext()
    assert ctx1.id != ctx2.id
    assert len(ctx1.id) == 12


def test_messages_data_carrier() -> None:
    """消息历史是 Context 的数据载体（Agent 消费/回填）。"""
    ctx = AgentContext()
    ctx.messages.append({"role": "user", "content": "你好"})
    assert ctx.messages == [{"role": "user", "content": "你好"}]


def test_steer_drain_once() -> None:
    """steer 指令 drain 后清空（每条只消费一次）。"""
    ctx = AgentContext()
    ctx.steer("纠偏1")
    ctx.steer("纠偏2")
    assert ctx.drain_steer() == ["纠偏1", "纠偏2"]
    assert ctx.drain_steer() == []  # 二次 drain 为空（已清空）


def test_stop_flag_and_wait() -> None:
    """stop 举旗 → should_stop True + wait 立即返回。"""
    ctx = AgentContext()
    assert not ctx.should_stop()
    ctx.stop()
    assert ctx.should_stop()
    assert ctx.status == ContextStatus.STOPPED
    assert ctx.wait(timeout=0.1) is None  # 已 stop，wait 快速返回


def test_complete_fail_state_transitions() -> None:
    """complete/fail 状态转换 + _done 信号。"""
    ctx = AgentContext()
    ctx.complete({"summary": "ok"})
    assert ctx.status == ContextStatus.COMPLETED
    assert ctx.result == {"summary": "ok"}

    ctx2 = AgentContext()
    ctx2.fail("出错了")
    assert ctx2.status == ContextStatus.FAILED
    assert ctx2.error == "出错了"


def test_wait_timeout_marks_failed() -> None:
    """wait 超时 → RUNNING 标记 FAILED（超时兜底）。"""
    ctx = AgentContext()
    assert ctx.wait(timeout=0.1) is None
    assert ctx.status == ContextStatus.FAILED
    assert "超时" in (ctx.error or "")


def test_wait_blocks_until_complete() -> None:
    """wait 阻塞到 complete（跨线程信号）。"""
    ctx = AgentContext()

    def _finish_later():
        time.sleep(0.05)
        ctx.complete({"summary": "done"})

    t = threading.Thread(target=_finish_later)
    t.start()
    result = ctx.wait(timeout=2.0)
    assert result == {"summary": "done"}
    t.join()


def test_steer_emits_event() -> None:
    """steer 发 subagent/steer 事件（审计）。"""
    ctx = AgentContext()
    seen = []
    ctx.events.on("subagent/steer", lambda **kw: seen.append(kw))
    ctx.steer("x")
    assert len(seen) == 1
    assert seen[0]["message"] == "x"


def test_stop_emits_event() -> None:
    """stop 发 subagent/stop 事件（审计）。"""
    ctx = AgentContext()
    seen = []
    ctx.events.on("subagent/stop", lambda **kw: seen.append(kw))
    ctx.stop()
    assert len(seen) == 1
