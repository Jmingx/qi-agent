"""记忆注入测试（方案 2026-08-26-会话持久化与记忆系统）。

验证：长期记忆在 pre-step 注入 system prompt 副本（不污染原消息）。
"""

import unittest.mock as mock

from qi_agent.events import EventBus
from qi_agent.plugins.builtin.memory import _inject_memories


def test_inject_memories_into_system() -> None:
    """记忆注入 system 消息末尾（副本——原列表不变）。"""
    with mock.patch("qi_agent.plugins.builtin.memory.MemoryStore") as FakeStore:
        FakeStore.return_value.read_memory.return_value = (
            "## 用户偏好\n- user_name: 小明\n## 项目\n- qi-agent")
        original = [{"role": "system", "content": "你是一个助手"},
                    {"role": "user", "content": "你好"}]
        result = _inject_memories(original)

    # 副本注入（原列表不变）
    assert original[0]["content"] == "你是一个助手"
    # 注入后 system 带记忆
    assert "小明" in result[0]["content"]
    assert "qi-agent" in result[0]["content"]
    # 用户消息不变
    assert result[1] == {"role": "user", "content": "你好"}


def test_no_memories_no_inject() -> None:
    """无记忆 → 不注入（原样返回）。"""
    with mock.patch("qi_agent.plugins.builtin.memory.MemoryStore") as FakeStore:
        FakeStore.return_value.read_memory.return_value = ""
        original = [{"role": "system", "content": "sys"}]
        result = _inject_memories(original)
    assert result == original


def test_storage_error_tolerant() -> None:
    """存储异常 → 不注入（容错不崩溃）。"""
    with mock.patch("qi_agent.plugins.builtin.memory.MemoryStore",
                    side_effect=RuntimeError("db down")):
        original = [{"role": "system", "content": "sys"}]
        result = _inject_memories(original)
    assert result == original


def test_memory_plugin_registers() -> None:
    """MemoryPlugin 注册 + install 挂监听生效。"""
    from qi_agent.plugins.builtin.memory import MemoryPlugin

    bus = EventBus()
    plugin = MemoryPlugin()
    plugin.install(bus)
    assert plugin.config == {}
