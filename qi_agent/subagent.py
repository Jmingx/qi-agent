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

AgentContext 统一合并（方案 2026-08-24）：
  SubagentContext = AgentContext（统一数据载体）+ 子专属配置
  （write_paths 授权清单 / timeout）。控制面（steer/stop/poll/wait）
  由统一 AgentContext 提供——任何控制者都能用（父 agent / 用户 / CLI）。
  Manager 操作统一 Context（通用控制台，为未来主 agent 控制铺路）。
"""

import threading
import uuid

from typing import Callable

from qi_agent.context.context import AgentContext, ContextStatus


class SubagentContext(AgentContext):
    """子 agent 运行环境 = 统一 AgentContext + 子专属配置。

    父 agent（manager.spawn）创建时传 parent=主 agent context；
    write_paths = 授权清单（子 agent 只写这些前缀内的路径）；
    timeout = 子任务超时（超时 → FAILED）。
    """

    def __init__(
        self,
        session_id: str,
        goal: str,
        context: str = "",
        timeout: float = 120.0,
        max_turns: int = 8,
        events=None,
        parent: AgentContext | None = None,
        write_paths: list[str] | None = None,
    ) -> None:
        super().__init__(
            agent_id=session_id, goal=goal, parent=parent,
            persist=False,  # 子 agent 默认瞬态（审计可显式开）
            max_turns=max_turns, events=events,
        )
        self.context_text = context  # 背景信息（父提炼，注入子 system prompt）
        self.timeout = timeout
        self.write_paths = write_paths or []


class SubagentManager:
    """子任务管理器：spawn / steer / poll / stop + 运行环境注册表。

    半双工控制面全部按 context_id 寻址（对齐 Hermes delegate_task
    action=spawn/steer/stop/list 的形态）。操作统一 AgentContext
    （通用控制台——未来主 agent 也能注册被控制）。
    """

    def __init__(self, max_concurrent: int = 3) -> None:
        self.contexts: dict[str, AgentContext] = {}
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
        ctx = SubagentContext(session_id, goal, context, timeout, max_turns,
                              write_paths=write_paths)
        with self._lock:
            self.contexts[session_id] = ctx

        thread = threading.Thread(
            target=self._run, args=(ctx,),
            kwargs={
                "client_factory": client_factory,
                "tool_executor_factory": tool_executor_factory,
                "tools": tools,
                "write_paths": write_paths,
            },
            daemon=True,
        )
        thread.start()
        return ctx

    def _run(self, context: SubagentContext, **kwargs) -> None:
        """worker 线程：装配子 agent 并运行（结果写回 context）。"""
        from qi_agent.tools.builtin.delegate_task import _run_subagent

        try:
            result = _run_subagent(context, kwargs.get("client_factory"),
                                   kwargs.get("tool_executor_factory"),
                                   kwargs.get("tools"),
                                   kwargs.get("write_paths"))
            if context.status == ContextStatus.RUNNING:
                context.complete(result)
        except Exception as exc:
            if context.status == ContextStatus.RUNNING:
                context.fail(f"子任务执行异常: {exc}")

    # ── 控制面（父侧）───────────────────────────────────────────────────
    def steer(self, session_id: str, message: str) -> bool:
        """注入补充指令（子 agent 下轮生效）。返回是否找到运行环境。"""
        context = self.contexts.get(session_id)
        if context is None or context.status != ContextStatus.RUNNING:
            return False
        context.steer(message)  # 统一 Context 控制面
        return True

    def stop(self, session_id: str) -> bool:
        """强制终止子任务（子 agent 下轮检查标志退出）。返回是否找到运行环境。"""
        context = self.contexts.get(session_id)
        if context is None:
            return False
        context.stop()  # 统一 Context 控制面
        return True

    def poll(self, session_id: str) -> ContextStatus | None:
        """查询任务状态（探活）。"""
        context = self.contexts.get(session_id)
        return context.poll() if context else None
