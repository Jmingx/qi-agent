"""LLM 客户端：封装 DeepSeek API（OpenAI 兼容协议）。

本模块只负责"通信"：发消息列表、拿回复文本。
不关心对话逻辑（那是 agent.py 的事）、不关心界面（那是 cli.py 的事）。
"""

from openai import OpenAI


class LLMClient:
    """DeepSeek 对话客户端（通过 OpenAI SDK 调用兼容协议）。"""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        temperature: float = 0.7,
    ) -> None:
        self.model = model
        self.temperature = temperature
        # OpenAI SDK 兼容 DeepSeek：只需替换 base_url 与 api_key
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def chat(self, messages: list[dict]) -> str:
        """发送消息列表，返回模型回复文本。

        Args:
            messages: 标准消息列表 [{"role": ..., "content": ...}, ...]

        Returns:
            模型回复的文本内容。

        Raises:
            OpenAIError: API 调用失败（网络、鉴权、额度等）。
        """
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        # 取第一条 choice 的回复文本（通常只有一条）
        return response.choices[0].message.content or ""
