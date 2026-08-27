"""/resume 后 run 用新 context_id 回归测试（bug 修复验证）。

复现 bug：CLI 的 run 用局部变量 context_id（初始快照），/resume 只更新
runtime.context_id → run 用旧 id（已 unregister）→ "context 不存在"。
修复：run/poll/stop 全部直接用 runtime.context_id（resume 会更新）。
"""

import unittest.mock as mock


def test_resume_then_run_uses_new_id() -> None:
    """/resume 后 run 用新 context_id（不是旧快照）。"""
    from qi_agent.cli import main
    from qi_agent.agents.agent import Agent

    agent = Agent(mock.MagicMock())
    run_calls: list[str] = []

    # mock runtime：context_id 可变属性（模拟 resume 更新它）
    class _Runtime:
        context_id = "ctx_main"
        installed = []
        manager = mock.MagicMock()
        manager.run = lambda cid, text, stream_callback=None: (
            run_calls.append(cid) or "ok")
        manager.get_context = lambda self, cid=None: agent.context
        manager.poll = lambda cid: None
        manager.stop = lambda cid: True
        manager.register = lambda ctx, role="main": ctx.id
        manager.unregister = lambda cid: None

        def get_context(self):
            return agent.context

    fake_store = mock.MagicMock()
    fake_store.list_sessions.return_value = [
        {"id": "ctx_old", "title": "旧会话", "updated_at": 0}]
    fake_store.load_session.return_value = {
        "id": "ctx_old", "title": "旧会话", "turn": 1,
        "usage": {}, "status": "completed", "phase": "done",
        "messages": [{"role": "system", "content": "sys"},
                     {"role": "user", "content": "你好"},
                     {"role": "assistant", "content": "你好！"}],
    }

    inputs = iter(["/resume ctx_old", "你好", "exit"])
    with mock.patch("builtins.input",
                    side_effect=lambda prompt="": next(inputs)), \
            mock.patch("qi_agent.cli.build_runtime", return_value=_Runtime()), \
            mock.patch("qi_agent.storage.get_storage", return_value=fake_store):
        main(argv=[])

    # run 必须用 resume 后的新 id（ctx_old）——不是初始 ctx_main
    assert run_calls, "run 应该被调用"
    assert run_calls[-1] == "ctx_old", f"run 用了旧 id: {run_calls}"
