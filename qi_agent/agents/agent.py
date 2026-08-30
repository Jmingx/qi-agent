"""Agent 核心：维护消息历史，实现多轮对话与工具调用循环。

核心认知（回顾 principles/01）：
- LLM 本身没有记忆，多轮对话的"上下文"完全依赖客户端把全部历史
  每次重新发送给 API。
- 阶段 2 升级：agent 循环 = 调 LLM → 模型要调工具就执行 → 结果以
  role="tool" 回填 → 继续调 LLM → 直到模型直接给出最终答案。

AgentContext 统一合并（方案 2026-08-24，用户拍板 D2/D3）：
  Agent = 【无状态执行者】——消费/回填 Context 的消息，只跑循环。
  Context = 【数据载体】——消息历史 + 会话轮数 + 用量累计 + 状态机
    + 控制面 + 事件总线（可持久化/可恢复/可归档——session 系统接入点）。
  薄委托：messages/_turn/get_usage/history 保留方法名（外部读取方
  cli/runner/delegate_task 零改动）。
"""


from typing import Protocol

from qi_agent.context.context import AgentContext, generate_id
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
    """对话 Agent：无状态执行者——消费/回填 AgentContext，提供多轮对话。

    数据（消息/轮数/用量）全部在 context 上（数据载体）；Agent 只负责
    循环逻辑。同一 context 可被新 Agent 实例接管继续跑（会话恢复基础）。
    """

    def __init__(
        self,
        client: ChatClient,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_turns: int = 8,
        events: EventBus | None = None,
        tool_executor: ToolExecutor | None = None,
        tools: list[str] | None = None,
        context: AgentContext | None = None,
    ) -> None:
        self.client = client
        self.id = generate_id("agt")
        # 执行者身份（方案 2026-08-24-ID规范化）：agt_ 前缀区别于
        # context（ctx_）——agent 是瞬态执行者（可观测/审计），context 是
        # 会话身份（持久化键）。无状态：数据全在 context，agent 即用即弃。
        self.system_prompt = system_prompt  # 初始化系统提示词
        # 统一运行环境（数据载体）：默认创建主 agent context（persist=True）。
        # 兼容：显式传入 events 时，context 复用该总线（外部监听者必须收到
        # 事件——向后兼容）；未传 events 时 context 自建总线。
        if context is None:
            context = AgentContext(persist=True, max_turns=max_turns,
                                   events=events)
        self.context = context
        # system_prompt 写入数据载体（reset/clear 由 context 负责重建 system）
        self.context.system_prompt = system_prompt
        self.events = self.context.events  # 事件总线从 context 取（同一来源）
        self.max_turns = max_turns
        # 工具白名单（subagent 受限子集，方案 2026-08-23）：
        # None = 全部工具（默认）；非空列表 = 受限子集——LLM 只见白名单
        # schema（层 1），executor 执行前硬校验（层 2，防绕过）
        self.tools: list[str] | None = tools
        # 工具执行闭环（方案 2026-08-23）：审批分发/并发执行/结果封装
        # 全在 ToolExecutor——agent 只保留事件点与消息回填（编排层瘦身）
        self.tool_executor = tool_executor or ToolExecutor(self.events)
        # 注：日志/上下文管理等横切关注点全部插件化（监听 agent/* 事件），
        # agent 核心保持零侵入——2026-08-22 用户架构修正
        if not self.context.messages:
            self._init_messages()
        # 邮局消费钩子（2026-08-30 收敛：主/子统一在 Agent 构造挂 pre-step——
        # 之前分散两处：子 agent 在 delegate_task._steer_watcher（每轮感知），
        # 主 agent 在 chat 开头（仅一次）——时机不对称。收敛到一处：
        # 所有 Agent（主/子）每轮 pre-step 消费邮箱，时机一致。
        # 直接挂 context.consume_mailbox（签名兼容事件 handler——无薄桥）
        self.events.on("agent/pre-step", self.context.consume_mailbox,
                       priority=100)

    # ── 薄委托（外部读取方零改动：cli/runner/delegate_task）────────────
    @property
    def messages(self) -> list[dict]:
        """消息历史（委托 context——数据载体）。"""
        return self.context.messages

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        self.context.messages = value

    @property
    def _turn(self) -> int:
        """会话轮数（委托 context，兼容 runner._turn 读取）。"""
        return self.context.turn

    @_turn.setter
    def _turn(self, value: int) -> None:
        self.context.turn = value

    @property
    def history(self) -> list[dict]:
        """只读访问消息历史（测试与调试用）。"""
        return self.context.messages

    def get_usage(self) -> dict[str, int]:
        """累计 API usage（阶段 A2：prompt/completion/total tokens）。"""
        return dict(self.context.usage)

    def usage_report(self) -> str:
        """人类可读汇总（/stats 或会话退出打印）。"""
        u = self.context.usage
        return (
            f"[用量] 累计 {u['total_tokens']} tokens"
            f"（prompt {u['prompt_tokens']} + completion {u['completion_tokens']}）"
        )

    def _init_messages(self) -> None:
        # 注意：sticky 挂载由 context_manager 插件在 pre-step 幂等注入
        # （agent 保持零侵入——system 组装不在此处做上下文管理逻辑）
        self.context.messages = [
            {"role": "system", "content": self.system_prompt},
        ]
        self.context.turn = 0  # 会话轮数计数（clear 后重新开始）

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
        # 状态机（方案 2026-08-24 §4.5）：会话级 RUNNING + 循环级 TURN_START
        self.context.begin_chat()
        # 邮局消费已收敛到 pre-step 钩子（_consume_mailbox——Agent 构造时
        # 挂载，主/子统一每轮消费）。此处不再硬编码（2026-08-30）。
        self.messages.append({"role": "user", "content": user_input})
        # 会话轮数累计（数据载体——跨 chat 累计；主动记忆提炼触发用）。
        # 注：_turn 是 property 委托 context.turn——只此一处递增（防双计）
        self._turn += 1
        # 事件点：turn-start（广播，通知类监听者——debug_logger 打印 [USER]）
        self.events.emit("agent/turn-start", user_input=user_input)

        try:
            result = self._run_tool_loop(user_input, stream_callback)
        except Exception as exc:
            # 状态机：RUNNING → FAILED（异常）
            self.context.fail_chat(f"chat 异常: {exc}")
            raise
        # 状态机出口：中断（stop）已由 _run_tool_loop 置 STOPPED——不再覆盖；
        # 只有未中断的正常路径才 COMPLETED
        if not self.context.should_stop():
            self.context.complete_chat()
        return result

    def _run_tool_loop(self, user_input: str, stream_callback=None) -> str:
        """工具循环（chat 内部——状态机在循环内更新 LLM_CALL/TOOL_EXEC）。"""
        for step in range(self.max_turns):
            # 中断检查（方案 2026-08-24 §4.5 + stop实时中断 Phase A）：
            # 协作式 checkpoint（Pi 模式）——工具批次间检查（工具内不掐断，
            # 由 executor 保证）；实时中断由 manager.run 主线程 wait_stop_or_done
            # 负责（stop 触发立即返回，不等本循环）
            if self.context.should_stop():
                self.events.emit("agent/turn-end", reason="stopped")
                return "已按指令中断当前任务。"
            # 状态机：循环级 LLM_CALL（调 LLM 前）
            self.context.enter_llm_call()
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
                for key in self.context.usage:
                    self.context.usage[key] += int(result.usage.get(key, 0) or 0)

            # 竞态防护（方案 2026-08-24-stop实时中断）：LLM 返回后、回填前
            # 检查 stop——被中断的旧线程不得回填 assistant/tool 消息
            # （否则污染后续对话——旧线程完成时 run#2 可能已开始）
            if self.context.should_stop():
                self.events.emit("agent/turn-end", reason="stopped")
                return "已按指令中断当前任务。"

            if result.tool_calls:
                # 1. assistant 的 tool_calls 消息必须原样进历史（协议要求）
                self.messages.append(result.assistant_message)
                # 状态机：循环级 TOOL_EXEC（工具执行前）
                self.context.enter_tool_exec()
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
                # 竞态防护：工具执行中 stop → 回填前检查（tool 消息不污染）
                if self.context.should_stop():
                    self.events.emit("agent/turn-end", reason="stopped")
                    return "已按指令中断当前任务。"
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
                # 状态机：循环级 ANSWERING（最终回答前）
                self.context.enter_answering()
                # 事件点：final-answer（广播，观察/存储/debug_logger [ANSWER]）
                self.events.emit("agent/final-answer", content=content)
                self.messages.append(result.assistant_message)
                return content

        # 4. 超限防护：防止工具死循环烧 API 额度
        # 事件点：turn-end（广播，含结束原因）
        self.events.emit("agent/turn-end", reason="max_turns")
        return "已达最大轮数，任务可能未完成。"
