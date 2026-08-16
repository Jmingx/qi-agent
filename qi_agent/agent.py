"""Agent 核心：维护消息历史，实现多轮对话与工具调用循环。

核心认知（回顾 principles/01）：
- LLM 本身没有记忆，多轮对话的"上下文"完全依赖客户端把全部历史
  每次重新发送给 API。
- 阶段 2 升级：agent 循环 = 调 LLM → 模型要调工具就执行 → 结果以
  role="tool" 回填 → 继续调 LLM → 直到模型直接给出最终答案。
"""

from typing import Protocol

from qi_agent.llm import ChatResult
from qi_agent.tools.registry import execute_tool, get_tool_schemas

DEFAULT_SYSTEM_PROMPT = "你是一个有用的助手。"


class ChatClient(Protocol):
    """LLM 客户端接口（协议类，便于测试替身替换）。"""

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        """发送消息列表（可选带工具），返回结构化结果。"""
        ...


class Agent:
    """对话 Agent：持有消息历史，提供多轮对话与工具调用能力。"""

    def __init__(
        self,
        client: ChatClient,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_turns: int = 8,
    ) -> None:
        self.client = client
        self.max_turns = max_turns
        self.messages: list[dict] = [
            {"role": "system", "content": system_prompt},
        ]

    def chat(self, user_input: str) -> str:
        """接收用户输入，运行工具调用循环，返回最终答案。

        循环逻辑：
        1. 调 LLM（带工具清单）
        2. 模型要调工具 → 逐个执行 → 结果以 role="tool" 回填 → 继续
        3. 模型直接回答 → 这就是最终答案
        4. 超过 max_turns 轮仍未结束 → 停止并提示
        """
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(self.max_turns):
            result = self.client.chat(self.messages, tools=get_tool_schemas())

            if result.tool_calls:
                # 1. assistant 的 tool_calls 消息必须原样进历史（协议要求）
                self.messages.append(result.assistant_message)
                # 2. 逐个执行工具，结果回填（tool_call_id 一一对应）
                for call in result.tool_calls:
                    output = execute_tool(call.name, call.arguments)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": output,
                        }
                    )
            else:
                # 3. 没有工具调用 → 这就是最终答案
                self.messages.append(result.assistant_message)
                return result.content or ""

        # 4. 超限防护：防止工具死循环烧 API 额度
        return "已达最大轮数，任务可能未完成。"

    @property
    def history(self) -> list[dict]:
        """只读访问消息历史（测试与调试用）。"""
        return self.messages
