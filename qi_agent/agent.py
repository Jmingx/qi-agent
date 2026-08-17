"""Agent 核心：维护消息历史，实现多轮对话与工具调用循环。

核心认知（回顾 principles/01）：
- LLM 本身没有记忆，多轮对话的"上下文"完全依赖客户端把全部历史
  每次重新发送给 API。
- 阶段 2 升级：agent 循环 = 调 LLM → 模型要调工具就执行 → 结果以
  role="tool" 回填 → 继续调 LLM → 直到模型直接给出最终答案。
"""

from typing import Protocol

from qi_agent.debugger import DebugLogger
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
        logger: DebugLogger | None = None,
    ) -> None:
        self.client = client
        self.max_turns = max_turns
        self.logger = logger  # 可选调试日志器（依赖注入，None 时无日志）
        self.system_prompt = system_prompt # 初始化系统提示词
        self._reset_messages()

    def chat(
        self,
        user_input: str,
        stream_callback=None,
    ) -> str:
        """接收用户输入，运行工具调用循环，返回最终答案。

        Args:
            user_input: 用户输入
            stream_callback: 可选流式回调（接收每段文本增量）。
                非 None 时，最终回答轮次改用流式（打字机效果）；
                None 时行为与普通模式完全一致（向后兼容）。

        循环逻辑：
        1. 调 LLM（带工具清单）
        2. 模型要调工具 → 逐个执行 → 结果以 role="tool" 回填 → 继续
        3. 模型直接回答 → 这就是最终答案（可流式）
        4. 超过 max_turns 轮仍未结束 → 停止并提示
        """
        self.messages.append({"role": "user", "content": user_input})
        if self.logger:
            self.logger.log_user_input(user_input)

        for _ in range(self.max_turns):
            if stream_callback is not None:
                # 流式模式：一次调用（on_delta 打字机 + 累积完整结果）
                # 修复双调用 bug：日志 [RESP] 与输出来自同一个 result
                result = self.client.chat_stream(
                    self.messages, tools=get_tool_schemas(), on_delta=stream_callback
                )
            else:
                # 普通模式：chat()（向后兼容）
                result = self.client.chat(self.messages, tools=get_tool_schemas())
            if self.logger:
                self.logger.log_request(self.messages, get_tool_schemas())
                self.logger.log_response(result)

            if result.tool_calls:
                # 1. assistant 的 tool_calls 消息必须原样进历史（协议要求）
                self.messages.append(result.assistant_message)
                # 2. 逐个执行工具，结果回填（tool_call_id 一一对应）
                for call in result.tool_calls:
                    output = execute_tool(call.name, call.arguments)
                    if self.logger:
                        self.logger.log_tool_call(call.name, call.arguments, output)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": output,
                        }
                    )
            else:
                # 3. 没有工具调用 → 这就是最终答案
                # （流式/普通模式的 result 都是标准 ChatResult，统一处理）
                self.messages.append(result.assistant_message)
                if self.logger:
                    self.logger.log_final_answer(result.content or "")
                return result.content or ""

        # 4. 超限防护：防止工具死循环烧 API 额度
        return "已达最大轮数，任务可能未完成。"

    def clear_context(self) -> None:
        self._reset_messages()

    def _reset_messages(self) -> None:
        self.messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
        ]

    @property
    def history(self) -> list[dict]:
        """只读访问消息历史（测试与调试用）。"""
        return self.messages
