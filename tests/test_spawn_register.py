"""spawn 统一注册测试（v3 修正 2026-08-29——走 register() 统一路径）。

验证：spawn 生成的子 context 通过 register() 注册——
  ① manager.contexts 可见
  ② 邮局路由注册（mailbox 可路由）
  ③ 事件上报（agent-manager/register——可观测统一）
"""

import unittest.mock as mock

from qi_agent.agents.agent_manager import AgentManager


def _spawn(manager: AgentManager) -> object:
    """拉起子任务（Fake LLM 快速返回——不真跑）。"""
    import qi_agent.agents.factory as factory

    factory.load_api_key = lambda: "sk-test"
    with mock.patch.object(factory, "LLMClient",
                           lambda key: _FastClient()):
        return manager.spawn("测试目标", context="背景", parent_id="ctx_parent")


class _FastClient:
    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        return ChatResult(content="ok", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "ok"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        return self.chat(messages, tools)


def test_spawn_registers_via_register() -> None:
    """spawn 统一走 register()：contexts 可见 + 路由注册 + 事件上报。"""
    manager = AgentManager()
    events: list[str] = []
    # 监听 register 事件（验证统一上报）
    orig_register = manager.register

    def spy_register(context, role="subagent"):
        events.append(role)
        return orig_register(context, role=role)

    manager.register = spy_register
    ctx = _spawn(manager)
    assert ctx.id in manager.contexts          # ① manager.contexts 可见
    assert manager.dispatcher.get_mailbox(ctx.id) is ctx.mailbox  # ② 路由
    assert "subagent" in events                # ③ register() 被调（统一路径）
    assert ctx.parent_id == "ctx_parent"
