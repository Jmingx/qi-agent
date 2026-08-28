"""主 agent 控制面测试（方案 2026-08-24-AgentManager统一控制台 Phase 2）。

验证：chat 循环 should_stop 中断 + 状态机更新。
"""

from qi_agent.agents.agent import Agent
from qi_agent.context.context import ContextStatus
from qi_agent.llm import ChatResult


class _SlowNeverDoneClient:
    """每轮慢速返回工具调用（stop 可在轮间生效——下轮生效降级验证）。"""

    def __init__(self, delay: float = 0.2):
        self.delay = delay
        self.calls = 0

    def chat(self, messages, tools=None):
        import time
        time.sleep(self.delay)  # 模拟 LLM 调用耗时
        self.calls += 1
        return ChatResult(
            content="",
            tool_calls=[type("TC", (), {"id": f"c{self.calls}", "name": "get_time",
                                         "arguments": {}})()],
            assistant_message={"role": "assistant", "content": "",
                               "tool_calls": []},
            usage=None,
        )


def test_stop_interrupts_chat() -> None:
    """chat 运行中 stop → 下轮中断返回"已按指令中断"。"""
    agent = Agent(_SlowNeverDoneClient(), max_turns=10)
    # 后台线程跑 chat（模拟长任务），主线程 stop
    import threading

    result_holder = {}

    def _run():
        result_holder["r"] = agent.chat("任务")

    t = threading.Thread(target=_run)
    t.start()
    # 等第一轮 LLM 调用后 stop
    import time
    time.sleep(0.3)
    agent.context.stop()
    t.join(timeout=5)
    assert result_holder.get("r") == "已按指令中断当前任务。"
    assert agent.context.status == ContextStatus.STOPPED


def test_chat_completes_state() -> None:
    """chat 正常结束 → status COMPLETED。"""

    class _DoneClient:
        def chat(self, messages, tools=None):
            return ChatResult(content="你好！", tool_calls=[],
                              assistant_message={"role": "assistant",
                                                 "content": "你好！"},
                              usage=None)

    agent = Agent(_DoneClient())
    r = agent.chat("hi")
    assert r == "你好！"
    assert agent.context.status == ContextStatus.COMPLETED


def test_agent_has_id() -> None:
    """执行者身份：agt_ 前缀（ID 规范化——区别于 context 的 ctx_）。"""

    class _DoneClient:
        def chat(self, messages, tools=None):
            return ChatResult(content="ok", tool_calls=[],
                              assistant_message={"role": "assistant",
                                                 "content": "ok"},
                              usage=None)

    agent = Agent(_DoneClient())
    assert agent.id.startswith("agt_")
    # 时间戳后缀：agt_<YYYYMMDD>_<HHMMSS>_<随机>
    parts = agent.id.split("_")
    assert parts[0] == "agt"
    assert len(parts[1]) == 8
    assert len(parts[2]) == 6
    assert len(parts[3]) == 6
    # 两个执行者 id 不同（瞬态身份）
    agent2 = Agent(_DoneClient())
    assert agent.id != agent2.id
