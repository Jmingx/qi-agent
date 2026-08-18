"""Agent 核心：维护消息历史，实现多轮对话与工具调用循环。

核心认知（回顾 principles/01）：
- LLM 本身没有记忆，多轮对话的"上下文"完全依赖客户端把全部历史
  每次重新发送给 API。
- 阶段 2 升级：agent 循环 = 调 LLM → 模型要调工具就执行 → 结果以
  role="tool" 回填 → 继续调 LLM → 直到模型直接给出最终答案。
"""

import time
from typing import Protocol

from qi_agent.debugger import DebugLogger
from qi_agent.events import EventBus
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
        events: EventBus | None = None,
    ) -> None:
        self.client = client
        self.max_turns = max_turns
        self.logger = logger  # 可选调试日志器（依赖注入，None 时无日志）
        self.system_prompt = system_prompt  # 初始化系统提示词
        self.events = events or EventBus()  # 事件总线（默认空总线：零侵入）
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
        self._turn += 1  # 会话内轮数计数（事件 payload 用）
        # 事件点：turn-start（广播，通知类监听者）
        self.events.emit("agent/turn-start", user_input=user_input)
        if self.logger:
            self.logger.log_user_input(user_input)

        for step in range(self.max_turns):
            # 事件点：pre-step（瀑布改写：插件可注入/修改消息历史）
            self.messages = self.events.waterfall(
                "agent/pre-step", self.messages, turn=self._turn, step=step
            )
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
            # 事件点：post-llm（广播，如 usage/成本追踪）
            self.events.emit("agent/post-llm", result=result, turn=self._turn, step=step)

            if result.tool_calls:
                # 1. assistant 的 tool_calls 消息必须原样进历史（协议要求）
                self.messages.append(result.assistant_message)
                # 2. 逐个执行工具，结果回填（tool_call_id 一一对应）
                for call in result.tool_calls:
                    # 事件点：tool-call（短路决策：返回非 None 则拦截，值作为结果回填）
                    decision = self.events.bail(
                        "agent/tool-call",
                        name=call.name,
                        arguments=call.arguments,
                        turn=self._turn,
                        step=step,
                    )
                    if decision is not None:
                        output = str(decision)
                        duration = 0.0
                    else:
                        start = time.perf_counter()
                        output = execute_tool(call.name, call.arguments)
                        duration = time.perf_counter() - start
                    # 事件点：tool-result（广播，统计/审计用）
                    self.events.emit(
                        "agent/tool-result",
                        name=call.name,
                        arguments=call.arguments,
                        output=output,
                        duration=duration,
                    )
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
                content = result.content or ""
                # 事件点：final-answer（广播，观察/存储用）
                self.events.emit("agent/final-answer", content=content)
                self.messages.append(result.assistant_message)
                if self.logger:
                    self.logger.log_final_answer(content)
                return content

        # 4. 超限防护：防止工具死循环烧 API 额度
        # 事件点：turn-end（广播，含结束原因）
        self.events.emit("agent/turn-end", reason="max_turns")
        return "已达最大轮数，任务可能未完成。"

    def clear_context(self) -> None:
        self._reset_messages()

    def _reset_messages(self) -> None:
        self.messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
        ]
        self._turn = 0  # 会话轮数计数（clear 后重新开始）

    @property
    def history(self) -> list[dict]:
        """只读访问消息历史（测试与调试用）。"""
        return self.messages
