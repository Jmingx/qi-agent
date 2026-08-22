"""Agent 核心：维护消息历史，实现多轮对话与工具调用循环。

核心认知（回顾 principles/01）：
- LLM 本身没有记忆，多轮对话的"上下文"完全依赖客户端把全部历史
  每次重新发送给 API。
- 阶段 2 升级：agent 循环 = 调 LLM → 模型要调工具就执行 → 结果以
  role="tool" 回填 → 继续调 LLM → 直到模型直接给出最终答案。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from qi_agent.events import EventBus
from qi_agent.llm import ChatResult, ToolCall
from qi_agent.tools.registry import execute_tool, get_tool_schemas

DEFAULT_SYSTEM_PROMPT = "你是一个有用的助手。"

# 并行工具调用上限（方案 2026-08-22，用户拍板 10）：
# 模型一次返回多个 tool_calls 时线程池并发执行——最多 N 个工具同时跑
# （安全阀非常态——模型实际通常发 2-5 个；同时天然是子进程并发边界）
_MAX_PARALLEL_TOOLS = 10


def _execute_with_timing(name: str, arguments: dict) -> tuple[str, float]:
    """执行单个工具并计时（并行线程入口——只做执行，不发事件/不碰历史）。

    并行线程只允许调用本函数：事件（tool-result）与回填（messages）必须
    回主线程——监听器状态非线程安全（tool_stats _call_start 同名覆盖、
    count += 1 非原子、logger 文件写），方案 2026-08-22 决策点 4。
    """
    start = time.perf_counter()
    output = execute_tool(
        name, arguments,
        internal={"approved"} if "approved" in arguments else None,
    )
    return output, time.perf_counter() - start


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
    ) -> None:
        self.client = client
        self.max_turns = max_turns
        self.system_prompt = system_prompt  # 初始化系统提示词
        self.events = events or EventBus()  # 事件总线（默认空总线：零侵入）
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
                tools=get_tool_schemas(), turn=self._turn, step=step,
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
                # 2. 并行执行（方案 2026-08-22）：三阶段——
                #    阶段1 主线程判档+审批（串行，弹窗一次一个）
                #    阶段2 线程池并行执行（只 execute_tool）
                #    阶段3 主线程按 call 原顺序 emit + 回填（监听器状态单线程）
                pending: dict[str, tuple[ToolCall, str | None]] = {}
                for call in result.tool_calls:
                    # 事件点：tool-call（短路决策：返回非 None 则拦截，值作为结果回填）
                    decision = self.events.bail(
                        "agent/tool-call",
                        name=call.name,
                        arguments=call.arguments,
                        turn=self._turn,
                        step=step,
                    )
                    # 审批档（v0.4.18）：security_guard 返回 NEED_APPROVAL:命令
                    # → 发审批事件（bail）→ 插件同意(True)才执行；无监听器/拒绝 → 拦截
                    if isinstance(decision, str) and decision.startswith("NEED_APPROVAL:"):
                        command = decision.split(":", 1)[1]
                        approved = self.events.bail(
                            "agent/tool-approval",
                            name=call.name,
                            arguments=call.arguments,
                            command=command,
                            turn=self._turn,
                            step=step,
                        )
                        if approved is True:
                            # 内部注入 approved（模型 schema 不可见，防绕过）——
                            # execute_tool 显式声明 internal，校验才放行
                            call.arguments["approved"] = True
                            decision = None  # 放行执行
                        else:
                            decision = f"[审批拒绝] 用户不同意执行: {command}"
                    pending[call.id] = (call, decision)

                # 阶段 2：线程池并行执行待运行的 calls（拦截的已在阶段 1 定结果）
                outputs: dict[str, tuple[str, float]] = {}
                to_run = {
                    cid: (call, dec)
                    for cid, (call, dec) in pending.items() if dec is None
                }
                if to_run:
                    with ThreadPoolExecutor(
                        max_workers=_MAX_PARALLEL_TOOLS
                    ) as pool:
                        futures = {
                            cid: pool.submit(_execute_with_timing, call.name, call.arguments)
                            for cid, (call, _) in to_run.items()
                        }
                        for cid, fut in futures.items():
                            outputs[cid] = fut.result()

                # 阶段 3：主线程按 call 原顺序 emit + 回填（顺序 = 模型请求顺序，
                # tool_stats 等监听器的状态配对依赖此顺序）
                for call in result.tool_calls:
                    decision = pending[call.id][1]
                    if decision is not None:
                        output, duration = str(decision), 0.0
                    else:
                        output, duration = outputs[call.id]
                    # 事件点：tool-result（广播，统计/审计/debug_logger [TOOL]）
                    self.events.emit(
                        "agent/tool-result",
                        name=call.name,
                        arguments=call.arguments,
                        output=output,
                        duration=duration,
                    )
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
