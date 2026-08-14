"""Agent 核心：维护消息历史，实现多轮对话。

核心认知：LLM 本身没有记忆，多轮对话的"上下文"完全依赖客户端
把全部历史消息每次重新发送给 API。因此本类最重要的职责就是
正确维护 messages 列表（role 交替、追加顺序）。
"""

from typing import Protocol

DEFAULT_SYSTEM_PROMPT = "你是一个有用的助手。"


class ChatClient(Protocol):
    """LLM 客户端接口（协议类，便于测试替身替换）。"""

    def chat(self, messages: list[dict]) -> str:
        """发送消息列表，返回模型回复文本。"""
        ...


class Agent:
    """对话 Agent：持有消息历史，提供多轮对话能力。"""

    def __init__(
        self,
        client: ChatClient,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.messages: list[dict] = [
            {"role": "system", "content": system_prompt},
        ]

    def chat(self, user_input: str) -> str:
        """接收用户输入，返回模型回复，并同步更新消息历史。

        消息历史必须严格按 user → assistant 交替追加，
        不能出现连续两条 user 或连续两条 assistant。
        """
        # 1. 追加用户消息
        self.messages.append({"role": "user", "content": user_input})

        # 2. 调用 LLM，携带完整历史（记忆的载体）
        reply = self.client.chat(self.messages)

        # 3. 追加模型回复
        self.messages.append({"role": "assistant", "content": reply})

        return reply

    @property
    def history(self) -> list[dict]:
        """只读访问消息历史（测试与调试用）。"""
        return self.messages
