"""Agent 运行时/执行者分离测试（方案 2026-08-24-AgentPool D1=B）。

验证：build_runtime（运行时，不建 agent）+ make_agent（执行者工厂，可插拔）。
"""

import pytest


@pytest.fixture()
def fake_key(monkeypatch):
    """mock API key（测试不读真实 .env）。"""
    import qi_agent.agents.factory as factory

    monkeypatch.setattr(factory, "load_api_key", lambda: "sk-test")


def test_build_runtime_no_agent(fake_key) -> None:
    """build_runtime 不创建 agent（运行时只含 manager/context/插件）。"""
    from qi_agent.agents.factory import RuntimeBundle, build_runtime

    runtime = build_runtime(interactive=False)
    assert isinstance(runtime, RuntimeBundle)
    assert runtime.manager is not None
    assert runtime.context_id.startswith("ctx_")  # ID 规范化：ctx_ 前缀
    assert not hasattr(runtime, "agent")  # 运行时不含执行者


def test_build_runtime_registers_main(fake_key) -> None:
    """build_runtime 注册主 agent 的 context（控制台可查）。"""
    from qi_agent.agents.factory import build_runtime

    runtime = build_runtime(interactive=False)
    ctx = runtime.get_context()  # RuntimeBundle.get_context（数据访问入口）
    assert ctx is not None
    assert runtime.manager.poll(runtime.context_id) is not None


def test_make_agent_creates_executor(fake_key) -> None:
    """make_agent 创建执行者（绑定 context 数据载体）。"""
    from qi_agent.agents.factory import build_runtime, make_agent

    runtime = build_runtime(interactive=False)
    ctx = runtime.get_context()
    agent = make_agent(ctx)
    assert agent is not None
    assert agent.context is ctx  # 执行者绑定数据载体
    assert hasattr(agent, "chat")  # 行为入口


def test_make_agent_uses_prod_prompt(fake_key) -> None:
    """make_agent 用 PROD_SYSTEM_PROMPT（subagent 能力引导）。"""
    from qi_agent.agents.factory import PROD_SYSTEM_PROMPT, build_runtime, make_agent

    runtime = build_runtime(interactive=False)
    ctx = runtime.get_context()
    agent = make_agent(ctx)
    assert agent.system_prompt == PROD_SYSTEM_PROMPT


def test_runtime_then_make_agent_full_chain(fake_key) -> None:
    """完整链路：build_runtime → make_agent → chat（运行时 + 执行者协作）。"""
    from qi_agent.agents.factory import build_runtime, make_agent
    from qi_agent.llm import ChatResult

    class Fake:
        def chat(self, messages, tools=None):
            return ChatResult(
                content="ok", tool_calls=None,
                assistant_message={"role": "assistant", "content": "ok"},
                usage={"prompt_tokens": 1, "completion_tokens": 1,
                       "total_tokens": 2},
            )

        def chat_stream(self, messages, tools=None, on_delta=None):
            return self.chat(messages, tools)

    runtime = build_runtime(interactive=False)
    ctx = runtime.get_context()
    agent = make_agent(ctx)
    agent.client = Fake()  # 替换真实 client（不走网络）
    reply = agent.chat("测试")
    assert reply == "ok"
    # 数据在 context（CLI 经 manager.get_context 读）
    assert ctx.messages[-1]["role"] == "assistant"
    assert ctx.usage["total_tokens"] == 2
