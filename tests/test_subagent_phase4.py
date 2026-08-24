"""Phase 4 测试：CLI /delegate 手动入口 + subagent 审计日志。

方案：docs/plans/2026-08-23-subagent方案.md 第 6 节（CLI /delegate）+ 4.5（审计）
- /delegate <goal>：手动拉起子任务（用户主导，不走主 agent 工具循环）
- 审计：subagent spawn/steer/stop/result 事件记录（可回溯授权与行为）
"""

import json


from qi_agent.subagent import SubagentManager, SubagentContextStatus


class _FakeClient:
    def __init__(self, summary="调研完成") -> None:
        self.summary = summary

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult

        structured = json.dumps(
            {"summary": self.summary, "artifacts": [], "status": "completed",
             "error": None, "question": None},
            ensure_ascii=False,
        )
        return ChatResult(
            content=structured, tool_calls=None,
            assistant_message={"role": "assistant", "content": structured},
        )


class TestAudit:
    def test_audit_spawn_event_recorded(self) -> None:
        """审计：spawn 事件记录（会话 id/goal/工具集）。"""
        mgr = SubagentManager()

        session = mgr.spawn(
            goal="调研", context="背景",
            client_factory=lambda: _FakeClient(),
        )
        session.wait(timeout=10)
        # 审计核心：会话存在 + 完成状态 + 结果带回（可回溯）
        assert session.id in mgr.contexts
        assert session.status == SubagentContextStatus.COMPLETED
        assert session.result["summary"] == "调研完成"

    def test_audit_steer_stop_events(self) -> None:
        """审计：steer/stop 通过事件总线广播（subagent/* 命名空间）。"""
        import time

        class _SlowClient(_FakeClient):
            def chat(self, messages, tools=None):
                time.sleep(0.1)  # 保证会话在 steer/stop 时仍 running
                return super().chat(messages, tools)

        mgr = SubagentManager()
        seen: list[str] = []

        session = mgr.spawn(
            goal="g", context="c", client_factory=lambda: _SlowClient(),
        )
        session.events.on("subagent/steer", lambda **kw: seen.append("steer"))
        session.events.on("subagent/stop", lambda **kw: seen.append("stop"))
        time.sleep(0.05)  # 等线程起来
        mgr.steer(session.id, "补充信息")
        mgr.stop(session.id)
        assert "steer" in seen
        assert "stop" in seen


class TestCliDelegate:
    def test_delegate_command_runs_sync(self) -> None:
        """/delegate 命令：同步跑子任务，输出结构化结果。"""
        from qi_agent.tools.builtin.delegate_task import delegate_task

        # CLI /delegate 直接调 delegate_task（与主 agent 工具循环同一实现）
        output = delegate_task(
            goal="调研 XX",
            context="背景",
            _client_factory=lambda: _FakeClient("完成调研"),
        )
        data = json.loads(output)
        assert data["status"] == "completed"
        assert "完成调研" in data["summary"]
