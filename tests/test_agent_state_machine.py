"""AgentContext 两级状态机测试（方案 2026-08-24-AgentManager统一控制台 §4.5）。

验证：会话级（ContextStatus）+ 循环级（ChatPhase）状态转移。
"""

from qi_agent.context.context import (
    AgentContext,
    ChatPhase,
    ContextStatus,
)


def test_context_status_includes_idle() -> None:
    """会话级状态枚举包含 IDLE（新建未开始）。"""
    assert ContextStatus.IDLE.value == "idle"


def test_chat_phase_enum_complete() -> None:
    """循环级状态枚举完整（IDLE→TURN_START→LLM_CALL→TOOL_EXEC→ANSWERING→DONE）。"""
    assert ChatPhase.IDLE.value == "idle"
    assert ChatPhase.TURN_START.value == "turn_start"
    assert ChatPhase.LLM_CALL.value == "llm_call"
    assert ChatPhase.TOOL_EXEC.value == "tool_exec"
    assert ChatPhase.ANSWERING.value == "answering"
    assert ChatPhase.DONE.value == "done"


def test_initial_states() -> None:
    """初始：会话级 IDLE + 循环级 IDLE。"""
    ctx = AgentContext()
    assert ctx.status == ContextStatus.IDLE
    assert ctx.phase == ChatPhase.IDLE


def test_running_transition() -> None:
    """chat 入口：IDLE → RUNNING（会话级）+ TURN_START（循环级）。"""
    ctx = AgentContext()
    ctx.begin_chat()  # chat() 入口调用
    assert ctx.status == ContextStatus.RUNNING
    assert ctx.phase == ChatPhase.TURN_START


def test_llm_call_transition() -> None:
    """循环每步：TURN_START → LLM_CALL。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.enter_llm_call()
    assert ctx.phase == ChatPhase.LLM_CALL


def test_tool_exec_transition() -> None:
    """模型要调工具：LLM_CALL → TOOL_EXEC。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.enter_llm_call()
    ctx.enter_tool_exec()
    assert ctx.phase == ChatPhase.TOOL_EXEC


def test_answering_transition() -> None:
    """模型直接回答：LLM_CALL → ANSWERING。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.enter_llm_call()
    ctx.enter_answering()
    assert ctx.phase == ChatPhase.ANSWERING


def test_complete_transition() -> None:
    """正常结束：RUNNING → COMPLETED + phase DONE。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.complete_chat({"status": "ok"})
    assert ctx.status == ContextStatus.COMPLETED
    assert ctx.phase == ChatPhase.DONE


def test_failed_transition() -> None:
    """异常：RUNNING → FAILED + phase DONE。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.fail_chat("出错了")
    assert ctx.status == ContextStatus.FAILED
    assert ctx.phase == ChatPhase.DONE


def test_stopped_transition() -> None:
    """stop：RUNNING → STOPPED + phase DONE。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.stop()
    assert ctx.status == ContextStatus.STOPPED
    assert ctx.phase == ChatPhase.DONE


def test_reset_back_to_idle() -> None:
    """reset：任意终态 → IDLE + phase IDLE（clear 后复用）。"""
    ctx = AgentContext()
    ctx.begin_chat()
    ctx.complete_chat({"status": "ok"})
    ctx.reset()
    assert ctx.status == ContextStatus.IDLE
    assert ctx.phase == ChatPhase.IDLE
