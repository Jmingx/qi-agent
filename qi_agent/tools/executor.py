"""工具执行闭环（方案 2026-08-23）：审批分发 → 并发执行 → 结果封装。

职责划分（ToolExecutor 执行闭环方案）：
- 编排层（agent.py）：只保留 agent/tool-call 短路决策事件点 + 消息回填
- 执行层（本模块）：决策后的完整闭环——
    阶段1 审批分发（NEED_APPROVAL/ESCALATION → agent/tool-approval bail）
    阶段2 线程池并发执行（只 execute_tool，事件回主线程）
    阶段3 结果封装（BLOCK/WARN/成功/审批拒绝 → 回填文本）+ tool-result 事件

事件语义二分（方案 D2）：
- agent/tool-call：编排语义"要不要做"（bail，留 agent）
- tool/start、agent/tool-result：执行生命周期"做得怎么样"（本模块发）
- agent/tool-approval：审批决策（本模块发起 bail，事件名不变——D4 插件兼容）
"""

import time
from concurrent.futures import ThreadPoolExecutor

from qi_agent.events import EventBus
from qi_agent.llm import ToolCall
from qi_agent.tools.decision import ToolAction, ToolDecision
from qi_agent.tools.registry import execute_tool

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


class ToolExecutor:
    """工具执行闭环：审批分发 → 并发执行 → 结果封装。

    编排层（agent）发完 agent/tool-call 事件得到决策后，
    把 calls + decisions 交给本类，返回 call.id → (output, duration)。
    """

    def __init__(self, events: EventBus | None = None) -> None:
        self.events = events or EventBus()

    def execute(
        self,
        calls: list[ToolCall],
        decisions: dict[str, ToolDecision | None],
        turn: int,
        step: int,
    ) -> dict[str, tuple[str, float]]:
        """执行一批工具调用（三阶段闭环），返回 call.id → (output, duration)。

        Args:
            calls: 模型本轮请求的全部工具调用（顺序 = 模型请求顺序）
            decisions: agent/tool-call 事件点的判档结果（call.id → 决策）：
                None = 放行；ToolDecision = 按 action 分发
            turn / step: 事件 payload 用（对齐 agent 循环上下文）

        三阶段（方案 2026-08-22 并行工具调用）：
            阶段1 审批分发（主线程，弹窗一次一个）
            阶段2 线程池并行执行（只 execute_tool）
            阶段3 主线程按 call 原顺序发 tool-result + 组装回填文本
        """
        # 阶段 1：审批分发（NEED_APPROVAL/ESCALATION → bail agent/tool-approval）
        pending: dict[str, tuple[ToolCall, ToolDecision | None]] = {}
        for call in calls:
            decision = decisions.get(call.id)
            if (
                isinstance(decision, ToolDecision)
                and decision.action in (ToolAction.NEED_APPROVAL, ToolAction.ESCALATION)
            ):
                # 审批档（v0.4.18）→ 发审批事件（bail）→
                # 插件同意(True)才执行；无监听器/拒绝 → 拦截（fail-closed）
                approved = self.events.bail(
                    "agent/tool-approval",
                    name=call.name,
                    arguments=call.arguments,
                    command=decision.command,
                    code=decision.code,
                    turn=turn,
                    step=step,
                )
                if approved is True:
                    # 内部注入 approved（模型 schema 不可见，防绕过）——
                    # execute_tool 显式声明 internal，校验才放行
                    call.arguments["approved"] = True
                    decision = None  # 放行执行
                else:
                    decision = ToolDecision(
                        ToolAction.BLOCK,
                        reason=f"用户不同意执行: {decision.command}",
                        code="SEC_APPROVAL_DENIED",
                        command=decision.command,
                    )
            pending[call.id] = (call, decision)

        # 阶段 2：线程池并行执行待运行的 calls（拦截的已在阶段 1 定结果；
        # WARN 档 = 警告放行——也要执行，输出阶段 3 附警告后缀）
        outputs: dict[str, tuple[str, float]] = {}
        to_run = {
            cid: (call, dec)
            for cid, (call, dec) in pending.items()
            if dec is None or (
                isinstance(dec, ToolDecision)
                and dec.action == ToolAction.WARN
            )
        }
        if to_run:
            # tool/start 事件：执行生命周期起点（观测用，不短路）——
            # 主线程发（监听器非线程安全，方案 2026-08-22 决策点 4）
            for cid, (call, _) in to_run.items():
                self.events.emit(
                    "tool/start",
                    name=call.name,
                    arguments=call.arguments,
                    turn=turn,
                    step=step,
                )
            with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_TOOLS) as pool:
                futures = {
                    cid: pool.submit(_execute_with_timing, call.name, call.arguments)
                    for cid, (call, _) in to_run.items()
                }
                for cid, fut in futures.items():
                    outputs[cid] = fut.result()

        # 阶段 3：主线程按 call 原顺序发 tool-result + 组装回填文本
        # （顺序 = 模型请求顺序，tool_stats 等监听器的状态配对依赖此顺序）
        results: dict[str, tuple[str, float]] = {}
        for call in calls:
            decision = pending[call.id][1]
            if decision is not None:
                if isinstance(decision, ToolDecision):
                    if decision.action == ToolAction.BLOCK:
                        # 硬拒回填：审批拒绝 vs 安全拦截（文本保持
                        # 改造前语义——方案承诺 CLI 体验不变）
                        if decision.code == "SEC_APPROVAL_DENIED":
                            output, duration = (
                                f"[审批拒绝] 用户不同意执行: "
                                f"{decision.command}", 0.0,
                            )
                        else:
                            output, duration = (
                                f"[安全拦截] {decision.reason}", 0.0,
                            )
                    elif decision.action == ToolAction.WARN:
                        # 警告放行：执行结果 + 警告后缀
                        output, duration = outputs[call.id]
                        output = f"{output}\n[警告] {decision.reason}"
                    else:  # 未知档位（防御）
                        output, duration = str(decision), 0.0
                else:  # 旧字符串决策（过渡防御）
                    output, duration = str(decision), 0.0
            else:
                output, duration = outputs[call.id]
            # 事件点：agent/tool-result（广播，统计/审计/debug_logger [TOOL]）——
            # 事件名与载荷不变（D4 插件兼容），发出位置从 agent 移到执行闭环
            self.events.emit(
                "agent/tool-result",
                name=call.name,
                arguments=call.arguments,
                output=output,
                duration=duration,
            )
            results[call.id] = (output, duration)
        return results
