"""Subagent 管理器：任务运行环境（Context）+ 半双工协议（方案 2026-08-23）。

半双工协议（第 2.3 节）——父单向控 + 子单向回报：
  父 → 子：steer（注入补充指令，子下轮生效）/ poll（查状态）/ stop（强制终止）
  子 → 父：result（最终结果，结构化）/ partial（need_more_info 回报）
  协商落地 = partial 回报 + 重新 spawn（不是实时对话，绕开同步工具调用死锁）

生命周期状态机（第 2.4 节）：
  spawn → running → completed（result）
                  → failed（error，含 timeout）
                  → stopped（父 stop 强制终止）

为什么不全双工：父 agent 正阻塞在 delegate_task 工具调用里，子问父答
  → 死锁。业界主流（Hermes steer / DSH direction）都是单向。

职责边界（v0.4.27 改名：SubagentSession → SubagentContext）：
  SubagentContext = 子任务【运行环境】（瞬态，任务结束即消失）：
    任务定义（goal/context/max_turns）+ 状态机（status/result/error）
    + 控制面（steer_queue/_stop_flag）+ 线程信号（_done）+ 独立事件总线
  它【不持有】对话历史（messages 在子 Agent 实例上）、【不负责】持久化
  ——那是未来 agent session 系统（对话存档，可恢复/搜索）的职责。
  两者是组合关系（子任务装配时各挂各的），不是继承/合并。
"""

import threading
import uuid
from enum import Enum
from typing import Callable

from qi_agent.events import EventBus


class SubagentContextStatus(str, Enum):
    """子任务生命周期状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class SubagentContext:
    """一个子任务的运行环境：状态机 + 结果 + 控制面（steer/stop）。

    职责边界（v0.4.27）：只承担【运行环境】——任务定义/状态/控制面/信号。
    不持有对话历史（在子 Agent 实例上）、不负责持久化（未来 agent session 的
    职责）。瞬态对象：任务结束即消失（组合未来 session 系统，不合并）。
    """

    def __init__(
        self,
        session_id: str,
        goal: str,
        context: str,
        timeout: float = 120.0,
        max_turns: int = 8,
        events: EventBus | None = None,
    ) -> None:
        self.id = session_id
        self.goal = goal
        self.context = context
        self.timeout = timeout
        self.max_turns = max_turns
        self.events = events or EventBus()
        self.status = SubagentContextStatus.RUNNING
        self.result: dict | None = None
        self.error: str | None = None
        self.steer_queue: list[str] = []  # 父注入的补充指令（子下轮消费）
        self._stop_flag = threading.Event()
        self._done = threading.Event()

    # ── 子 agent 侧（worker 线程）────────────────────────────────────────
    def drain_steer(self) -> list[str]:
        """取走待处理的补充指令（子 agent 每轮检查——下轮生效）。"""
        msgs = list(self.steer_queue)
        self.steer_queue.clear()
        return msgs

    def should_stop(self) -> bool:
        """子 agent 是否被父要求终止（每轮检查）。"""
        return self._stop_flag.is_set()

    def complete(self, result: dict) -> None:
        """子 agent 正常完成（状态 COMPLETED，结果带回）。"""
        self.result = result
        self.status = SubagentContextStatus.COMPLETED
        self._done.set()

    def fail(self, error: str) -> None:
        """子 agent 失败（异常/超时，状态 FAILED）。"""
        self.error = error
        self.status = SubagentContextStatus.FAILED
        self._done.set()

    # ── 父侧（manager 调用）─────────────────────────────────────────────
    def wait(self, timeout: float | None = None) -> dict | None:
        """阻塞等待子任务结束，返回结构化结果（超时返回 None）。"""
        self._done.wait(timeout=timeout)
        if self._done.is_set():
            return self.result
        # 等待超时：如果子 agent 还在跑，标记失败（超时兜底）
        if self.status == SubagentContextStatus.RUNNING:
            self.fail(f"子任务超时（>{self.timeout}s）")
        return self.result


class SubagentManager:
    """子任务管理器：spawn / steer / poll / stop + 运行环境注册表。

    半双工控制面全部按 context_id 寻址（对齐 Hermes delegate_task
    action=spawn/steer/stop/list 的形态）。
    """

    def __init__(self, max_concurrent: int = 3) -> None:
        self.contexts: dict[str, SubagentContext] = {}
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()

    def spawn(
        self,
        goal: str,
        context: str = "",
        client_factory: Callable | None = None,
        timeout: float = 120.0,
        max_turns: int = 8,
        tool_executor_factory: Callable | None = None,
        tools: list[str] | None = None,
        write_paths: list[str] | None = None,
    ) -> SubagentContext:
        """拉起一个子任务（后台线程跑，父不阻塞）。

        Args:
            goal: 任务目标
            context: 父提炼的背景
            client_factory: 子 agent 的 LLM 客户端工厂（测试注入；生产默认）
            timeout: 超时秒数（超时 → FAILED）
            max_turns: 子 agent 最大对话轮数（预算兜底）
            tool_executor_factory: 子 agent 执行器工厂（测试注入）
            tools: 子 agent 工具白名单（None = 默认只读子集）
            write_paths: 可写路径白名单（授权清单）
        """
        session_id = uuid.uuid4().hex[:12]
        context = SubagentContext(session_id, goal, context, timeout, max_turns)
        with self._lock:
            self.contexts[session_id] = context

        thread = threading.Thread(
            target=self._run, args=(context,),
            kwargs={
                "client_factory": client_factory,
                "tool_executor_factory": tool_executor_factory,
                "tools": tools,
                "write_paths": write_paths,
            },
            daemon=True,
        )
        thread.start()
        return context

    def _run(self, context: SubagentContext, **kwargs) -> None:
        """worker 线程：装配子 agent 并运行（结果写回 context）。"""
        from qi_agent.tools.builtin.delegate_task import _run_subagent

        try:
            result = _run_subagent(context, kwargs.get("client_factory"),
                                   kwargs.get("tool_executor_factory"),
                                   kwargs.get("tools"),
                                   kwargs.get("write_paths"))
            if context.status == SubagentContextStatus.RUNNING:
                context.complete(result)
        except Exception as exc:
            if context.status == SubagentContextStatus.RUNNING:
                context.fail(f"子任务执行异常: {exc}")

    # ── 控制面（父侧）───────────────────────────────────────────────────
    def steer(self, session_id: str, message: str) -> bool:
        """注入补充指令（子 agent 下轮生效）。返回是否找到运行环境。"""
        context = self.contexts.get(session_id)
        if context is None or context.status != SubagentContextStatus.RUNNING:
            return False
        context.steer_queue.append(message)
        context.events.emit("subagent/steer", session_id=session_id, message=message)
        return True

    def stop(self, session_id: str) -> bool:
        """强制终止子任务（子 agent 下轮检查标志退出）。返回是否找到运行环境。"""
        context = self.contexts.get(session_id)
        if context is None:
            return False
        context._stop_flag.set()
        context.events.emit("subagent/stop", session_id=session_id)
        if context.status == SubagentContextStatus.RUNNING:
            context.status = SubagentContextStatus.STOPPED
            context.error = "父代理强制终止"
        context._done.set()  # 释放等待者（wait 立即返回 partial）
        return True

    def poll(self, session_id: str) -> SubagentContextStatus | None:
        """查询任务状态（探活）。"""
        context = self.contexts.get(session_id)
        return context.status if context else None
