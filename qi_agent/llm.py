"""LLM 客户端：封装 DeepSeek API（OpenAI 兼容协议）。

本模块只负责"通信"：发消息列表 + 工具定义，拿回复（文本或工具调用）。
不关心对话逻辑（那是 agent.py 的事）、不关心界面（那是 cli.py 的事）。
"""

from dataclasses import dataclass, field

from openai import OpenAI


@dataclass
class ToolCall:
    """模型发起的工具调用请求。"""

    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    """一次 LLM 调用的结构化结果。

    - 模型直接回答：content 非空，tool_calls 为 None
    - 模型要调工具：content 为 None，tool_calls 非空
    - assistant_message 是完整的 assistant 消息（含 tool_calls 原样），
      必须原样追加进历史（协议要求，模型依赖它理解上下文）
    """

    content: str | None
    tool_calls: list[ToolCall] | None
    assistant_message: dict = field(default_factory=dict)


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

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        """发送消息列表（可选带工具定义），返回结构化结果。

        Args:
            messages: 标准消息列表 [{"role": ..., "content": ...}, ...]
            tools: 工具 JSON Schema 列表（阶段 2 起传入）

        Returns:
            ChatResult：包含文本回复或工具调用请求。

        Raises:
            OpenAIError: API 调用失败（网络、鉴权、额度等）。
        """
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools

        response = self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message

        # 解析工具调用（deepseek 兼容 OpenAI 的 tool_calls 格式）
        tool_calls: list[ToolCall] | None = None
        if message.tool_calls:
            tool_calls = []
            for call in message.tool_calls:
                import json

                # arguments 是 JSON 字符串，解析成 dict
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=args))

        # 构造完整的 assistant 消息（含 tool_calls 原样保留，供历史回填）
        # 注意：协议要求 function.arguments 必须是 JSON 字符串（模型返回时
        # 是字符串，解析成 dict 执行后，回填必须还原成字符串）
        assistant_message: dict = {"role": "assistant", "content": message.content}
        if tool_calls:
            import json

            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]

        return ChatResult(
            content=message.content,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
        )

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None):
        """流式调用：逐块产出文本增量（生成器）。

        仅用于"纯文本回答"轮次（工具调用轮次用 chat()——需要完整
        的 tool_calls 结构才能执行工具，流式无意义）。

        Args:
            messages: 标准消息列表
            tools: 工具 JSON Schema 列表（可选）

        Yields:
            一段文本增量（delta.content），调用方逐块消费
        """
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,  # 流式：返回迭代器而非完整对象
        }
        if tools:
            kwargs["tools"] = tools

        stream = self._client.chat.completions.create(**kwargs)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
