"""Agent 核心：维护消息历史，实现多轮对话与工具调用循环。

核心认知（回顾 principles/01）：
- LLM 本身没有记忆，多轮对话的"上下文"完全依赖客户端把全部历史
  每次重新发送给 API。
- 阶段 2 升级：agent 循环 = 调 LLM → 模型要调工具就执行 → 结果以
  role="tool" 回填 → 继续调 LLM → 直到模型直接给出最终答案。
"""

from typing import Protocol

from qi_agent.events import EventBus
from qi_agent.llm import ChatResult
from qi_agent.tools.decision import ToolDecision
from qi_agent.tools.executor import ToolExecutor
from qi_agent.tools.registry import get_tool_schemas

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
        events: EventBus | None = None,
        tool_executor: ToolExecutor | None = None,
        tools: list[str] | None = None,
    ) -> None:
        self.client = client
        self.max_turns = max_turns
        self.system_prompt = system_prompt  # 初始化系统提示词
        self.events = events or EventBus()  # 事件总线（默认空总线：零侵入）
        # 工具白名单（subagent 受限子集，方案 2026-08-23）：
        # None = 全部工具（默认）；非空列表 = 受限子集——LLM 只见白名单
        # schema（层 1），executor 执行前硬校验（层 2，防绕过）
        self.tools: list[str] | None = tools
        # 工具执行闭环（方案 2026-08-23）：审批分发/并发执行/结果封装
        # 全在 ToolExecutor——agent 只保留事件点与消息回填（编排层瘦身）
        self.tool_executor = tool_executor or ToolExecutor(self.events)
        # 注：日志/上下文管理等横切关注点全部插件化（监听 agent/* 事件），
        # agent 核心保持零侵入——2026-08-22 用户架构修正
        # API usage 累计（阶段 A2，方案 2026-08-22）：每轮 result.usage
        # 累加 prompt/completion/total——会话结束 /stats 汇总打印。
        # 这是纯观测（不改发送内容）；改写逻辑走插件（context_manager）
        self._usage: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
        self._reset_messages()

    def get_usage(self) -> dict[str, int]:
        """累计 API usage（阶段 A2：prompt/completion/total tokens）。"""
        return dict(self._usage)

    def usage_report(self) -> str:
        """人类可读汇总（/stats 或会话退出打印）。"""
        u = self._usage
        return (
            f"[用量] 累计 {u['total_tokens']} tokens"
            f"（prompt {u['prompt_tokens']} + completion {u['completion_tokens']}）"
        )

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
        # 事件点：turn-start（广播，通知类监听者——debug_logger 打印 [USER]）
        self.events.emit("agent/turn-start", user_input=user_input)

        for step in range(self.max_turns):
            # 事件点：pre-step（瀑布改写：插件可注入/修改消息历史——
            # context_manager 插件在此做滑动裁剪，agent 零侵入）
            self.messages = self.events.waterfall(
                "agent/pre-step", self.messages, turn=self._turn, step=step
            )
            # 事件点：pre-llm（广播，请求前——debug_logger 打印 [CTX]/[REQ]，
            # 未来压缩预检等插件也在此挂）
            self.events.emit(
                "agent/pre-llm", messages=self.messages,
                tools=get_tool_schemas(self.tools), turn=self._turn, step=step,
            )
            if stream_callback is not None:
                # 流式模式：一次调用（on_delta 打字机 + 累积完整结果）
                # 修复双调用 bug：日志 [RESP] 与输出来自同一个 result
                result = self.client.chat_stream(
                    self.messages, tools=get_tool_schemas(self.tools),
                    on_delta=stream_callback,
                )
            else:
                # 普通模式：chat()（向后兼容）
                result = self.client.chat(self.messages, tools=get_tool_schemas(self.tools))
            # 事件点：post-llm（广播，如 usage/成本追踪/debug_logger [RESP]）
            # messages 一并传出（2026-08-21 数据源修正）：resource_monitor
            # 在流式 usage 缺失时用消息列表估算（DSH 式混合兜底）
            self.events.emit(
                "agent/post-llm", result=result, messages=self.messages,
                turn=self._turn, step=step,
            )
            # usage 累计（阶段 A2）：流式/非流式 result.usage 都可能为
            # None（旧 API/流式缺 usage）——容错跳过
            if result.usage:
                for key in self._usage:
                    self._usage[key] += int(result.usage.get(key, 0) or 0)

            if result.tool_calls:
                # 1. assistant 的 tool_calls 消息必须原样进历史（协议要求）
                self.messages.append(result.assistant_message)
                # 2. 事件点：tool-call（短路决策：返回非 None 则拦截，
                #    值作为结果回填）——编排层只负责"要不要做"的决策；
                #    审批分发/并发执行/结果封装全在 ToolExecutor
                #    （执行闭环，方案 2026-08-23）
                decisions: dict[str, ToolDecision | None] = {}
                for call in result.tool_calls:
                    decision = self.events.bail(
                        "agent/tool-call",
                        name=call.name,
                        arguments=call.arguments,
                        turn=self._turn,
                        step=step,
                    )
                    decisions[call.id] = decision
                # 3. 执行闭环：审批 → 并发执行 → 结果封装 → tool-result 事件
                #    （ToolExecutor 内部完成，agent 不碰执行策略）
                #    allowlist：受限子集硬校验（层 2，subagent 方案）——
                #    白名单外工具即使模型幻觉请求，执行层直接拒绝
                outcomes = self.tool_executor.execute(
                    result.tool_calls,
                    decisions,
                    turn=self._turn,
                    step=step,
                    allowlist=self.tools,
                )
                # 4. 回填（消息历史归 agent 管理）
                for call in result.tool_calls:
                    output, _ = outcomes[call.id]
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
                # 事件点：final-answer（广播，观察/存储/debug_logger [ANSWER]）
                self.events.emit("agent/final-answer", content=content)
                self.messages.append(result.assistant_message)
                return content

        # 4. 超限防护：防止工具死循环烧 API 额度
        # 事件点：turn-end（广播，含结束原因）
        self.events.emit("agent/turn-end", reason="max_turns")
        return "已达最大轮数，任务可能未完成。"

    def clear_context(self) -> None:
        self._reset_messages()

    def _reset_messages(self) -> None:
        # 注意：sticky 挂载由 context_manager 插件在 pre-step 幂等注入
        # （agent 保持零侵入——system 组装不在此处做上下文管理逻辑）
        self.messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
        ]
        self._turn = 0  # 会话轮数计数（clear 后重新开始）

    @property
    def history(self) -> list[dict]:
        """只读访问消息历史（测试与调试用）。"""
        return self.messages
