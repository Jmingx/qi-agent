"""子 agent 失败通知测试（v3 补充 2026-08-29）。

验证：意外崩溃（_run_subagent 装配级异常）也投 RESULT message 给父——
  失败通知统一（父不依赖 poll 就能知道子失败）。
"""

import time

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext


def test_unexpected_crash_sends_failure_message() -> None:
    """意外崩溃 → 父邮箱收到 RESULT（status=failed + error）。"""
    manager = AgentManager()
    parent = AgentContext(context_id="ctx_parent")
    manager.register(parent, role="main")

    def _crash_client_factory():
        raise RuntimeError("装配级崩溃：LLM 客户端构造失败")
    ctx = manager.spawn("测试目标", context="背景", parent_id="ctx_parent",
                        client_factory=_crash_client_factory)
    # 等待子任务结束（失败通知投递）
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if parent.mailbox.inbox.qsize() >= 1:
            break
        time.sleep(0.05)

    msgs = parent.mailbox.drain()
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.type == "result"
    assert msg.sender == ctx.id
    assert msg.target == "ctx_parent"
    data = msg.data
    assert data["status"] == "failed"
    assert "装配级崩溃" in data["error"]
    # 子 context 状态 FAILED
    assert manager.get_context(ctx.id).status.value == "failed"
