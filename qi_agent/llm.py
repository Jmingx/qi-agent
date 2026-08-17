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

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta=None,
    ) -> ChatResult:
        """流式调用：on_delta 回调逐块文本（打字机），返回累积完整 ChatResult。

        一次调用同时解析（修复双调用 bug，v0.4.6）：
        - delta.content → 文本：逐块回调 on_delta + 累积
        - delta.tool_calls → 工具调用：累积 id/name/arguments 分片

        Args:
            messages: 标准消息列表
            tools: 工具 JSON Schema 列表（可选）
            on_delta: 文本增量回调（打字机效果），每收到一段调用一次

        Returns:
            标准 ChatResult（content 或 tool_calls），与 chat() 同构——
            上层 log_response / agent 分支逻辑无需区分来源。
        """
        import json

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,  # 流式：返回迭代器而非完整对象
        }
        if tools:
            kwargs["tools"] = tools

        stream = self._client.chat.completions.create(**kwargs)

        # 累积器
        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}  # tool_call index -> {"id","name","args":[分片]}

        for chunk in stream:
            delta = chunk.choices[0].delta
            # 1. 文本增量：回调 + 累积
            if delta.content:
                content_parts.append(delta.content)
                if on_delta:
                    on_delta(delta.content)
            # 2. 工具调用增量：分段到达，需要累积拼接
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": []})
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            acc["name"] = tc.function.name
                        if tc.function.arguments:
                            acc["args"].append(tc.function.arguments)

        # 工具调用分支
        if tool_acc:
            tool_calls = []
            for _, a in sorted(tool_acc.items()):
                try:
                    args = json.loads("".join(a["args"]) or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=a["id"], name=a["name"], arguments=args))

            assistant_message: dict = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            return ChatResult(
                content=None,
                tool_calls=tool_calls,
                assistant_message=assistant_message,
            )

        # 文本分支
        full_text = "".join(content_parts)
        return ChatResult(
            content=full_text,
            tool_calls=None,
            assistant_message={"role": "assistant", "content": full_text},
        )
