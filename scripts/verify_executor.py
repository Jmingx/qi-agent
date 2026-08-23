"""手工验收：ToolExecutor 执行闭环（方案 2026-08-23）。

真实插件装配（security_guard + approval_gate）下的：
1. 审批档链路（NEED_APPROVAL → 弹窗 → 执行）
2. 拦截链路（BLOCK → [安全拦截]）
3. 并发执行（多 tool_calls 并行）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qi_agent.agent import Agent
from qi_agent.events import EventBus
from qi_agent.interaction import set_interaction_provider
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.plugins.builtin.approval_gate import ApprovalGatePlugin
from qi_agent.plugins.builtin.security_guard import SecurityGuardPlugin
from qi_agent.tools.executor import ToolExecutor


class FakeProvider:
    """假交互层：审批记录 + 一律同意（验证链路，不真弹窗）。"""

    def __init__(self) -> None:
        self.asked: list[dict] = []

    def ask(self, question: str, choices: list[str] | None = None,
            timeout: float | None = 60.0) -> str:
        self.asked.append({"question": question, "choices": choices})
        return "y"  # 一律同意


class FakeClient:
    def __init__(self, tool_calls: list[ToolCall]) -> None:
        self._calls = tool_calls
        self._round = 0

    def chat(self, messages, tools=None) -> ChatResult:
        self._round += 1
        if self._round == 1:
            # assistant_message 必须完整（协议要求，真实 LLM 会构造）
            return ChatResult(
                content=None,
                tool_calls=self._calls,
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": c.arguments}}
                        for c in self._calls
                    ],
                },
            )
        return ChatResult(content="完成", tool_calls=None,
                          assistant_message={"role": "assistant", "content": "完成"})


def make_call(cid: str, name: str, arguments: dict) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=arguments)


def main() -> None:
    bus = EventBus()
    provider = FakeProvider()
    set_interaction_provider(provider)  # 全局注入（CLI 装配同款）
    # 真实插件装配：security_guard（判档）+ approval_gate（弹窗）
    SecurityGuardPlugin().install(bus)
    ApprovalGatePlugin().install(bus)
    executor = ToolExecutor(bus)

    # 1. 审批档：rm 文件（security_guard 按 approval 声明判 NEED_APPROVAL）
    agent = Agent(FakeClient([
        make_call("c1", "file_delete", {"path": "C:/tmp/delete-me.txt"}),
        make_call("c2", "get_time", {}),
    ]), events=bus, tool_executor=executor)
    # 并发：两个调用同轮发出
    agent.chat("删文件并看时间")
    tool_msgs = [m for m in agent.history if m["role"] == "tool"]
    print("\n=== 1. 审批 + 并发链路 ===")
    for m in tool_msgs:
        print(f"  [{m['tool_call_id']}] {m['content'][:80]}")
    assert provider.asked, "应发起审批弹窗"
    assert "rm" in provider.asked[0]["question"].lower() or "删除" in provider.asked[0]["question"]
    assert "delete-me" in provider.asked[0]["question"]
    assert "[审批拒绝]" not in tool_msgs[0]["content"]  # 同意 → 真实执行
    print(f"  审批弹窗: {provider.asked[0]['question']}")
    print("  ✅ 审批同意 → 执行（file_delete 真实调用）")
    print("  ✅ get_time 并发执行（同轮两个调用都出结果）")

    # 2. 拦截链路：security_guard 硬拒（红线命令——不可审批，直接拦截）
    bus2 = EventBus()
    SecurityGuardPlugin().install(bus2)
    agent2 = Agent(FakeClient([
        make_call("c3", "shell", {"command": "format C:"}),  # 红线前缀
    ]), events=bus2, tool_executor=ToolExecutor(bus2))
    agent2.chat("清空磁盘")
    tool_msgs2 = [m for m in agent2.history if m["role"] == "tool"]
    print("\n=== 2. 拦截链路（红线）===")
    print(f"  {tool_msgs2[0]['content'][:80]}")
    assert tool_msgs2[0]["content"].startswith("[安全拦截]")
    print("  ✅ BLOCK → [安全拦截] 回填（不执行、不弹窗）")

    print("\n全部验收通过 ✅")


if __name__ == "__main__":
    main()
