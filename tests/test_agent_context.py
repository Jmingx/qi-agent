"""AgentContext 统一运行环境测试（方案 2026-08-24-AgentContext统一合并）。

验证：统一 Context 的状态机/控制面/数据载体职责（主/子 agent 共用）。
"""

import threading
import time

from qi_agent.context.context import (
    AgentContext,
    ChatPhase,
    ContextStatus,
)


def _wait_steer(ctx: AgentContext, n: int, timeout: float = 2.0) -> list[str]:
    """等待异步投递（v3 Dispatcher 搬运）——轮询直到收满 n 条。"""
    deadline = time.monotonic() + timeout
    acc: list[str] = []
    while time.monotonic() < deadline:
        acc.extend(ctx.drain_steer())  # 累计（2026-08-30 修复：分批到达不丢）
        if len(acc) >= n:
            return acc
        time.sleep(0.01)
    return acc


def test_initial_state() -> None:
    """初始状态：IDLE + 空消息 + 空用量 + 0 轮。"""
    ctx = AgentContext(persist=True)
    assert ctx.status == ContextStatus.IDLE
    assert ctx.phase == ChatPhase.IDLE
    assert ctx.messages == []
    assert ctx.usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert ctx.turn == 0


def test_auto_id() -> None:
    """自动生成 id（ctx_ 前缀 + 时间戳——ID 规范化 + 可读性）。"""
    ctx1 = AgentContext()
    ctx2 = AgentContext()
    assert ctx1.id != ctx2.id
    assert ctx1.id.startswith("ctx_")
    # 时间戳后缀（用户拍板 2026-08-27）：ctx_<YYYYMMDD>_<HHMMSS>_<随机>
    parts = ctx1.id.split("_")
    assert parts[0] == "ctx"
    assert len(parts[1]) == 8   # YYYYMMDD
    assert len(parts[2]) == 6   # HHMMSS
    assert len(parts[3]) == 6   # 随机位


def test_messages_data_carrier() -> None:
    """消息历史是 Context 的数据载体（Agent 消费/回填）。"""
    ctx = AgentContext()
    ctx.messages.append({"role": "user", "content": "你好"})
    assert ctx.messages == [{"role": "user", "content": "你好"}]


def test_steer_drain_once() -> None:
    """steer 指令 drain 后清空（每条只消费一次）——走邮局（v3）。

    2026-08-29 收敛：steer 唯一入口 = manager.steer（context 不再有同名
    方法——消息构造在 manager 一处完成）。
    """
    from qi_agent.agents.agent_manager import AgentManager

    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")
    mgr.steer(ctx.id, "纠偏1")
    mgr.steer(ctx.id, "纠偏2")
    assert _wait_steer(ctx, 2) == ["纠偏1", "纠偏2"]
    assert ctx.drain_steer() == []  # 二次 drain 为空（已清空）


def test_stop_flag_and_wait() -> None:
    """stop 举旗 → should_stop True + wait 立即返回。"""
    ctx = AgentContext()
    ctx.begin_chat()  # IDLE → RUNNING（stop 只作用于 RUNNING）
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
    ctx.begin_chat()  # IDLE → RUNNING（wait 超时兜底只作用于 RUNNING）
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
    """steer 发 subagent/steer 事件（审计）——走邮局（v3）。

    2026-08-29 收敛：steer 唯一入口 = manager.steer（context 无同名方法）。
    """
    from qi_agent.agents.agent_manager import AgentManager

    mgr = AgentManager()
    ctx = AgentContext()
    mgr.register(ctx, role="main")
    seen = []
    ctx.events.on("subagent/steer", lambda **kw: seen.append(kw))
    mgr.steer(ctx.id, "x")
    assert len(seen) == 1
    assert seen[0]["message"] == "x"


def test_stop_emits_event() -> None:
    """stop 发 subagent/stop 事件（审计）。"""
    ctx = AgentContext()
    seen = []
    ctx.events.on("subagent/stop", lambda **kw: seen.append(kw))
    ctx.stop()
    assert len(seen) == 1
