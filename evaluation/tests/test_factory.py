"""Agent 工厂测试：build_agent 真实形态装配（cli 与 eval 共用）。

方案：docs/plans/2026-08-20-测评系统阶段A方案.md（决策点 1：eval/prod parity）
"""

import pytest


@pytest.fixture()
def fake_key(monkeypatch):
    """mock API key（测试不读真实 .env）。"""
    import qi_agent.agents.factory as factory

    monkeypatch.setattr(factory, "load_api_key", lambda: "sk-test")


def test_build_agent_plugins_mounted(fake_key) -> None:
    """build_agent 应装配插件（env_info/security_guard 默认开）——真实形态。"""
    from qi_agent.agents.factory import build_agent

    bundle = build_agent()
    agent, installed = bundle.agent, bundle.installed
    # 默认装配：env_info + security_guard（默认启用），tool_stats（配置 false）
    names = [type(p).__name__ for p in installed]
    assert "EnvInfoPlugin" in names
    assert "SecurityGuardPlugin" in names
    # 事件总线有监听器（插件注册成功）
    assert agent.events._listeners


def test_build_agent_stats_shortcut(fake_key) -> None:
    """stats=True 应快捷装配 tool_stats（不改配置文件）。"""
    from qi_agent.agents.factory import build_agent

    bundle = build_agent(stats=True)
    installed = bundle.installed
    names = [type(p).__name__ for p in installed]
    assert "ToolStatsPlugin" in names


def test_build_agent_debug_logger(fake_key) -> None:
    """debug=True 应装配 debug_logger 插件（2026-08-22 插件化）。"""
    from qi_agent.agents.factory import build_agent

    bundle = build_agent(debug=True)
    installed = bundle.installed
    names = [type(p).__name__ for p in installed]
    assert "DebugLoggerPlugin" in names


def test_build_agent_smoke_run(fake_key) -> None:
    """真实装配冒烟（2026-08-23 排查补充）：装配后跑一轮 chat——
    全部插件事件链不炸 + 内部依赖自足（防"装配成功但功能挂"类回归，
     如 context_manager 的 summarizer 未接线）。"""
    from qi_agent.agents.factory import build_agent
    from qi_agent.llm import ChatResult

    class Fake:
        def chat(self, messages, tools=None) -> ChatResult:
            return ChatResult(
                content="ok", tool_calls=None,
                assistant_message={"role": "assistant", "content": "ok"},
                usage={"prompt_tokens": 100, "completion_tokens": 10,
                       "total_tokens": 110},
            )

        def chat_stream(self, messages, tools=None, on_delta=None) -> ChatResult:
            return self.chat(messages, tools)

    bundle = build_agent(interactive=False)  # 评测形态（无审批弹窗）
    agent, installed = bundle.agent, bundle.installed
    agent.client = Fake()  # 替换真实 client（冒烟不走网络）
    reply = agent.chat("冒烟测试")
    assert reply == "ok"
    # 内部依赖自足检查：context_manager 摘要器/异步压缩已接线（默认兜底）
    cm = next((p for p in installed
               if type(p).__name__ == "ContextManagerPlugin"), None)
    if cm is not None:
        assert cm._summarizer is not None  # 默认惰性实现（load_plugins 只传 config）
        assert cm._async_compressor is not None  # 异步压缩可用
