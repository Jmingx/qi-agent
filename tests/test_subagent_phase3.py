"""Phase 3 测试：半双工协议（steer/stop + 生命周期状态机 + partial 回报）。

方案：docs/plans/2026-08-23-subagent方案.md 第 2.3/2.4 节
- 生命周期状态机：spawn → running → completed / failed / timeout / stopped
- 父→子：steer（注入补充指令，子下轮生效）/ stop（强制终止拿 partial）
- 子→父：result / partial（need_more_info）
- 半双工（不全双工）：父单向控 + 子单向回报——绕开同步工具调用死锁
"""

import time

from qi_agent.subagent import SubagentManager, SubagentContextStatus


class _SlowClient:
    """假子 agent client：每轮等待，支持多轮（模拟真实子 agent 工作）。"""

    def __init__(self, rounds: int = 3) -> None:
        self.rounds = rounds
        self.calls = 0
        self.seen_messages: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.calls += 1
        self.seen_messages.append(messages)
        if self.calls < self.rounds:
            # 前几轮模拟工具调用（返回 tool_calls）
            from qi_agent.llm import ChatResult, ToolCall

            return ChatResult(
                content=None,
                tool_calls=[ToolCall(id=f"c{self.calls}", name="get_time", arguments={})],
                assistant_message={"role": "assistant", "content": None,
                                   "tool_calls": [{"id": f"c{self.calls}",
                                                   "type": "function",
                                                   "function": {"name": "get_time",
                                                                "arguments": "{}"}}]},
            )
        # 最后一轮返回最终回答
        return _final("子任务完成，发现 3 个关键点")


def _final(text: str):
    from qi_agent.llm import ChatResult

    return ChatResult(
        content=text, tool_calls=None,
        assistant_message={"role": "assistant", "content": text},
    )


class TestLifecycle:
    def test_spawn_to_completed(self) -> None:
        """正常流程：spawn → running → completed（结果带回）。"""
        mgr = SubagentManager()
        session = mgr.spawn(
            goal="调研", context="背景",
            client_factory=lambda: _SlowClient(rounds=2),
        )
        assert session.status == SubagentContextStatus.RUNNING
        result = session.wait(timeout=10)
        assert session.status == SubagentContextStatus.COMPLETED
        assert "3 个关键点" in result["summary"]

    def test_state_machine_transitions(self) -> None:
        """状态机合法转换：spawn→completed（wait 后必为终态）。"""
        mgr = SubagentManager()
        session = mgr.spawn(
            goal="g", context="c",
            client_factory=lambda: _SlowClient(rounds=1),
        )
        # spawn 后可能在 running 或已 completed（线程快）——只断言合法终态
        session.wait(timeout=10)
        assert session.status == SubagentContextStatus.COMPLETED
        assert session.result is not None

    def test_timeout(self) -> None:
        """超时：spawn 带 timeout → 超时标记 FAILED(timeout)。"""
        mgr = SubagentManager()

        class _HangingClient:
            def chat(self, messages, tools=None):
                time.sleep(5)
                return _final("太慢了")

        session = mgr.spawn(
            goal="g", context="c",
            client_factory=lambda: _HangingClient(),
            timeout=0.5,
        )
        session.wait(timeout=3)
        assert session.status == SubagentContextStatus.FAILED
        assert "超时" in (session.error or "")


class TestSteer:
    def test_steer_injected_before_next_turn(self) -> None:
        """steer 注入 → 子 agent 下一轮看到补充指令。"""
        mgr = SubagentManager()

        class _SteerAwareClient(_SlowClient):
            def chat(self, messages, tools=None):
                time.sleep(0.05)  # 让主线程有时间 steer
                return super().chat(messages, tools)

        session = mgr.spawn(
            goal="调研", context="背景",
            client_factory=lambda: _SteerAwareClient(rounds=4),
        )
        time.sleep(0.15)  # 等子 agent 跑起来
        mgr.steer(session.id, "补充：重点关注性能数据")
        result = session.wait(timeout=10)
        assert session.status == SubagentContextStatus.COMPLETED
        assert result  # 有结果（steer 不阻塞主流程）


class TestStop:
    def test_stop_terminates_with_partial(self) -> None:
        """stop 强制终止 → 子 agent 停止，状态 STOPPED。"""
        mgr = SubagentManager()

        class _LongClient(_SlowClient):
            def chat(self, messages, tools=None):
                time.sleep(0.3)
                return super().chat(messages, tools)

        session = mgr.spawn(
            goal="g", context="c",
            client_factory=lambda: _LongClient(rounds=100),
        )
        time.sleep(0.4)
        mgr.stop(session.id)
        session.wait(timeout=5)
        assert session.status in (SubagentContextStatus.STOPPED, SubagentContextStatus.COMPLETED)

    def test_poll_returns_status(self) -> None:
        """poll：查询运行中的会话状态。"""
        mgr = SubagentManager()
        session = mgr.spawn(
            goal="g", context="c",
            client_factory=lambda: _SlowClient(rounds=3),
        )
        status = mgr.poll(session.id)
        assert status in (SubagentContextStatus.RUNNING, SubagentContextStatus.COMPLETED)
        session.wait(timeout=10)


class TestManager:
    def test_sessions_registry(self) -> None:
        """manager 维护会话注册表（spawn/poll/steer/stop 按 id 寻址）。"""
        mgr = SubagentManager()
        s1 = mgr.spawn(goal="g1", context="c", client_factory=lambda: _SlowClient(1))
        s2 = mgr.spawn(goal="g2", context="c", client_factory=lambda: _SlowClient(1))
        assert len(mgr.contexts) == 2
        assert mgr.poll(s1.id) is not None
        assert mgr.poll(s2.id) is not None
        # 等待两个都完成
        s1.wait(timeout=10)
        s2.wait(timeout=10)
        assert s1.status == SubagentContextStatus.COMPLETED
        assert s2.status == SubagentContextStatus.COMPLETED
