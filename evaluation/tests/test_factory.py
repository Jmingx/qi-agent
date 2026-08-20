"""Agent 工厂测试：build_agent 真实形态装配（cli 与 eval 共用）。

方案：docs/plans/2026-08-20-测评系统阶段A方案.md（决策点 1：eval/prod parity）
"""

import pytest


@pytest.fixture()
def fake_key(monkeypatch):
    """mock API key（测试不读真实 .env）。"""
    import qi_agent.agent_factory as factory

    monkeypatch.setattr(factory, "load_api_key", lambda: "sk-test")


def test_build_agent_plugins_mounted(fake_key) -> None:
    """build_agent 应装配插件（env_info/security_guard 默认开）——真实形态。"""
    from qi_agent.agent_factory import build_agent

    agent, installed = build_agent()
    # 默认装配：env_info + security_guard（默认启用），tool_stats（配置 false）
    names = [type(p).__name__ for p in installed]
    assert "EnvInfoPlugin" in names
    assert "SecurityGuardPlugin" in names
    # 事件总线有监听器（插件注册成功）
    assert agent.events._listeners


def test_build_agent_stats_shortcut(fake_key) -> None:
    """stats=True 应快捷装配 tool_stats（不改配置文件）。"""
    from qi_agent.agent_factory import build_agent

    _, installed = build_agent(stats=True)
    names = [type(p).__name__ for p in installed]
    assert "ToolStatsPlugin" in names


def test_build_agent_debug_logger(fake_key) -> None:
    """debug=True 应注入 DebugLogger。"""
    from qi_agent.agent_factory import build_agent

    agent, _ = build_agent(debug=True)
    assert agent.logger is not None
