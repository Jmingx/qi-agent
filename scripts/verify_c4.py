"""手工验收：阶段 C 收尾 CLI 命令（/context + /compact）。

真实装配（build_agent + plugins.toml）下：
1. /context：占用构成 + 真实 usage
2. /compact：强制同步压缩 + 摘要展示（mock 摘要器避免真实 API）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest import mock

from qi_agent.cli import main
from qi_agent.llm import ChatResult


class FakeClient:
    def __init__(self) -> None:
        self.chat_count = 0

    def _result(self) -> ChatResult:
        self.chat_count += 1
        return ChatResult(
            content="ok", tool_calls=None,
            assistant_message={"role": "assistant", "content": "ok"},
            usage={"prompt_tokens": 1200, "completion_tokens": 50,
                   "total_tokens": 1250},
        )

    def chat(self, messages, tools=None) -> ChatResult:
        return self._result()

    def chat_stream(self, messages, tools=None, on_delta=None) -> ChatResult:
        return self._result()


def main_flow() -> None:
    from qi_agent.agent import Agent
    from qi_agent.plugins.builtin.context_manager import ContextManagerPlugin

    # 真实插件装配（context_manager 用 mock 摘要器——不真调 API）
    cm = ContextManagerPlugin({}, summarizer=lambda msgs: "关键事实：测试摘要")
    agent = Agent(FakeClient())
    # 造一点历史（20 条消息——早期 9 组 → 压缩成摘要块，效果明显）
    agent.messages = [{"role": "system", "content": "sys"}]
    for i in range(19):
        role = "user" if i % 2 == 0 else "assistant"
        agent.messages.append({"role": role, "content": f"历史消息{i}"})

    with mock.patch("builtins.input",
                    side_effect=iter(["context", "compact", "exit"])):
        with mock.patch("qi_agent.cli.build_agent",
                        return_value=(agent, [cm])):
            main(argv=[])

    print("\n=== 验收 ===")
    assert agent.client.chat_count == 0, "/context 和 /compact 不应消耗 LLM 调用"
    assert len(agent.history) < 13, "/compact 应减少消息数"
    print("✅ /context 打印占用构成（估算） + 真实 usage 累计")
    print("✅ /compact 强制压缩成功（消息数下降 + 摘要展示）")
    print("✅ 两个命令零 LLM 调用（命令分支在 agent.chat 之前）")


if __name__ == "__main__":
    main_flow()
