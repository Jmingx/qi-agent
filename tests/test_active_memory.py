"""主动记忆测试（方案 2026-08-26-主动记忆系统 V1）。

验证：规则触发自动记忆（我喜欢→USER.md）+ 每 10 轮提炼触发。
"""

import unittest.mock as mock


from qi_agent.plugins.builtin.memory import _auto_remember


# ── P2: 规则触发 ─────────────────────────────────────────────────────────


def test_auto_remember_user_pref() -> None:
    """"我喜欢 X" → 自动写 USER.md（无需审批）。"""
    fake = mock.MagicMock()
    with mock.patch("qi_agent.plugins.builtin.memory.MemoryStore",
                    return_value=fake):
        _auto_remember(messages=[
            {"role": "user", "content": "我喜欢打游戏英雄联盟"},
            {"role": "assistant", "content": "好的"},
        ])
    fake.add_memory.assert_called_once()
    args = fake.add_memory.call_args
    assert "英雄联盟" in args[0][0]
    assert args[1]["target"] == "user"


def test_auto_remember_project_decision() -> None:
    """"我们决定 X" → 自动写 MEMORY.md。"""
    fake = mock.MagicMock()
    with mock.patch("qi_agent.plugins.builtin.memory.MemoryStore",
                    return_value=fake):
        _auto_remember(messages=[
            {"role": "user", "content": "我们决定用 SQLite 做持久化"},
            {"role": "assistant", "content": "好的"},
        ])
    fake.add_memory.assert_called_once()
    args = fake.add_memory.call_args
    assert "SQLite" in args[0][0]
    assert args[1]["target"] == "memory"


def test_auto_remember_no_pattern() -> None:
    """无触发模式 → 不写（零成本规则）。"""
    fake = mock.MagicMock()
    with mock.patch("qi_agent.plugins.builtin.memory.MemoryStore",
                    return_value=fake):
        _auto_remember(messages=[
            {"role": "user", "content": "今天天气不错"},
            {"role": "assistant", "content": "是的"},
        ])
    fake.add_memory.assert_not_called()


def test_auto_remember_storage_error_tolerant() -> None:
    """存储异常 → 不崩溃（容错）。"""
    with mock.patch("qi_agent.plugins.builtin.memory.MemoryStore",
                    side_effect=RuntimeError("disk full")):
        _auto_remember(messages=[
            {"role": "user", "content": "我喜欢跑步"},
        ])  # 不抛


# ── P3: 每 10 轮提炼触发 ─────────────────────────────────────────────────


def test_extract_interval_triggered() -> None:
    """turn 达到间隔 → 触发提炼（last_extract_turn 更新 + 起线程）。"""
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext

    mgr = AgentManager()
    ctx = AgentContext()
    ctx.turn = 10  # 达到间隔
    ctx.memory_extract_interval = 10
    ctx.last_extract_turn = 0

    with mock.patch("threading.Thread") as fake_thread:
        mgr._maybe_extract_memory(ctx)
        fake_thread.assert_called_once()  # 起了提炼线程
    assert ctx.last_extract_turn == 10  # 更新（防重复触发）


def test_extract_interval_not_triggered() -> None:
    """turn 未达间隔 → 不触发（不起线程）。"""
    from qi_agent.agents.agent_manager import AgentManager
    from qi_agent.context.context import AgentContext

    mgr = AgentManager()
    ctx = AgentContext()
    ctx.turn = 5
    ctx.memory_extract_interval = 10
    ctx.last_extract_turn = 0

    with mock.patch("threading.Thread") as fake_thread:
        mgr._maybe_extract_memory(ctx)
        fake_thread.assert_not_called()  # 未起提炼线程
    assert ctx.last_extract_turn == 0  # 未更新
