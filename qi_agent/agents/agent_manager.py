"""AgentManager：统一控制台（主/子 agent 共用控制面，方案 2026-08-24）。

核心架构（用户拍板："CLI 控制主 agent = 主 agent 控制 subagent"）：
  同一个 AgentManager，两种控制者：
  - 子 agent：父 agent（delegate_task / manager.spawn）控制
  - 主 agent：用户/CLI（manager.stop/steer）控制——控制面统一

AgentManager（= SubagentManager 构建升级）：
  register(context, role)   # 任何 agent 注册（主 role="main"，子 role="subagent"）
  spawn()                   # 子任务（原 SubagentManager.spawn，接口不变）
  steer/stop/poll(id)       # 控制面（按 id 寻址，接口不变）
  unregister(id)            # 任务结束清理

与 SubagentContext 的关系：
  SubagentContext（subagent.py）= 子专属配置（write_paths/timeout/context_text）
  AgentManager = 控制台（管理所有 agent 的运行环境，含主 agent）
"""

import threading
import uuid

from typing import Any, Callable

from qi_agent.context.context import AgentContext, ContextStatus
# 注意：不模块级 import SubagentContext——subagent.py 又 import 本模块
# （SubagentManager 继承 AgentManager）→ 循环导入。SubagentContext 在
# spawn() 内延迟 import（见下）。


class AgentManager:
    """统一控制台：register / spawn / steer / stop / poll / unregister。"""

    def __init__(self, max_concurrent: int = 3) -> None:
        self.contexts: dict[str, AgentContext] = {}
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()

    # ── 注册（主/子 agent 通用）──────────────────────────────────────────
    def register(self, context: AgentContext, role: str = "subagent") -> str:
        """注册任何 agent（主/子）到控制台，返回 agent id。

        受信控制：只允许受信调用方注册（防任意 agent 混入控制台——
        build_agent 内部调用；外部模块需显式持 manager 引用）。
        """
        with self._lock:
            self.contexts[context.id] = context
        context.events.emit("agent-manager/register",
                            agent_id=context.id, role=role)
        return context.id

    def unregister(self, agent_id: str) -> None:
        """注销 agent（任务结束清理）。"""
        with self._lock:
            self.contexts.pop(agent_id, None)

    def get_context(self, agent_id: str) -> AgentContext | None:
        """按 id 取数据载体（CLI/调用方数据访问的唯一入口）。

        设计（用户拍板 2026-08-24）：context 的所有权在 manager——
        CLI 不直接持有 context 对象，通过 manager 寻址获取。
        换 agent 执行者时，数据访问路径不变（都走 manager.get_context）。
        """
        return self.contexts.get(agent_id)

    # ── 子任务（原 SubagentManager.spawn，接口不变）─────────────────────
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
    ) -> Any:
        """拉起一个子任务（后台线程跑，父不阻塞）。

        返回 SubagentContext（延迟 import 防循环，注解用 Any）。

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
        from qi_agent.agents.subagent import SubagentContext  # 延迟 import（防循环）

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

    def _run(self, context: Any, **kwargs) -> None:
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

    # ── 控制面（任何控制者：父 agent / 用户 / CLI）───────────────────────
    def steer(self, agent_id: str, message: str) -> bool:
        """注入补充指令（agent 下轮生效）。返回是否找到运行环境。

        不要求 RUNNING——IDLE 也能排队（用户先说"改方向"，agent 启动后
        下轮生效）。指令只是入队，运行中才消费。
        """
        context = self.contexts.get(agent_id)
        if context is None:
            return False
        context.steer(message)  # 统一 Context 控制面
        return True

    def stop(self, agent_id: str) -> bool:
        """强制终止（agent 下轮检查标志退出）。返回是否找到运行环境。"""
        context = self.contexts.get(agent_id)
        if context is None:
            return False
        context.stop()  # 统一 Context 控制面
        return True

    def poll(self, agent_id: str) -> ContextStatus | None:
        """查询状态（探活）。"""
        context = self.contexts.get(agent_id)
        return context.poll() if context else None
