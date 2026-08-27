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

from qi_agent.agents.pool import AgentPool
from qi_agent.context.context import AgentContext, ContextStatus
from qi_agent.storage.base import Storage
# 注意：不模块级 import SubagentContext——subagent.py 又 import 本模块
# （SubagentManager 继承 AgentManager）→ 循环导入。SubagentContext 在
# spawn() 内延迟 import（见下）。


class AgentManager:
    """统一控制台：register / spawn / steer / stop / poll / unregister。"""

    def __init__(self, max_concurrent: int = 3,
                 storage: Storage | None = None) -> None:
        self.contexts: dict[str, AgentContext] = {}
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()
        # 存储（方案 2026-08-26 会话持久化）：基础设施注入——persist=True
        # 的 context 在 run 完成后落盘（write-behind 异步）。None = 不持久化。
        self.storage = storage
        # AgentPool（方案 2026-08-24）：spawn 用池治理并发（max_concurrent
        # 真正生效——此前只是存着没用）。subagent 执行者仍由 _run_subagent
        # 特殊装配（工具子集/授权清单），pool 只提供并发额度。
        self.pool = AgentPool(max_workers=max_concurrent)

    # ── 注册（主/子 agent 通用）──────────────────────────────────────────
    def register(self, context: AgentContext, role: str = "subagent") -> str:
        """注册任何 agent（主/子）到控制台，返回 context_id。

        受信控制：只允许受信调用方注册（防任意 agent 混入控制台——
        build_runtime 内部调用；外部模块需显式持 manager 引用）。
        ID 规范化（方案 2026-08-24）：注册的是 context（数据载体），
        返回 id = context.id（ctx_ 前缀）——不是 agent_id。
        """
        with self._lock:
            self.contexts[context.id] = context
        context.events.emit("agent-manager/register",
                            context_id=context.id, role=role)
        return context.id

    def unregister(self, context_id: str) -> None:
        """注销（任务结束清理）。"""
        with self._lock:
            self.contexts.pop(context_id, None)

    def get_context(self, context_id: str) -> AgentContext | None:
        """按 id 取数据载体（CLI/调用方数据访问的唯一入口）。

        设计（用户拍板 2026-08-24）：context 的所有权在 manager——
        CLI 不直接持有 context 对象，通过 manager 寻址获取。
        换 agent 执行者时，数据访问路径不变（都走 manager.get_context）。
        """
        return self.contexts.get(context_id)

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
        """worker 线程：装配子 agent 并运行（结果写回 context）。

        并发治理（方案 2026-08-24）：acquire 额度（超限等待）→ 跑任务
        → release（try/finally 保证不泄漏）。subagent 执行者由
        _run_subagent 特殊装配（工具子集/授权清单），pool 只限并发。
        """
        from qi_agent.tools.builtin.delegate_task import _run_subagent

        self.pool.acquire(None)  # 并发额度（等待直到有位置）
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
        finally:
            self.pool.release(None)  # 回收额度（异常也不泄漏）

    # ── 控制面（任何控制者：父 agent / 用户 / CLI）───────────────────────
    def steer(self, context_id: str, message: str) -> bool:
        """注入补充指令（agent 下轮生效）。返回是否找到运行环境。

        不要求 RUNNING——IDLE 也能排队（用户先说"改方向"，agent 启动后
        下轮生效）。指令只是入队，运行中才消费。
        """
        context = self.contexts.get(context_id)
        if context is None:
            return False
        context.steer(message)  # 统一 Context 控制面
        return True

    def stop(self, context_id: str) -> bool:
        """强制终止（agent 下轮检查标志退出）。返回是否找到运行环境。"""
        context = self.contexts.get(context_id)
        if context is None:
            return False
        context.stop()  # 统一 Context 控制面
        return True

    def poll(self, context_id: str) -> ContextStatus | None:
        """查询状态（探活）。"""
        context = self.contexts.get(context_id)
        return context.poll() if context else None

    # ── 执行入口（方案 2026-08-24-执行权归还Manager）────────────────────
    def run(self, context_id: str, user_input: str,
            stream_callback=None) -> str:
        """执行一次对话（执行权归还 Manager——CLI 不持有 agent）。

        用户拍板：agent 生命周期比 manager 短得多，CLI 不该持有执行者。
        agent 在 pool 内即用即弃（acquire → chat → release），
        manager 不感知具体 agent 类型（可插拔）。

        实时中断（方案 2026-08-24-stop实时中断 Phase A）：
        - 整个 chat（LLM + 工具循环）放后台线程（daemon）——主线程不等 LLM
        - 主线程 wait_stop_or_done 双事件等待（stop/done）
        - stop 触发 → 立即返回"已按指令中断当前任务"（不等慢 LLM）
          + pool.release 旧 agent（线程自然回收，timeout 兜底）
        - 新请求 → 新 agent 接管同一 context（无状态替换，数据无缝）

        Args:
            context_id: 数据载体 id（ctx_ 前缀——会话身份）
            user_input: 用户输入
            stream_callback: 流式回调（透传 agent.chat）

        Returns:
            最终回复（agent.chat 结果）；被中断时返回中断提示
        """
        context = self.contexts.get(context_id)
        if context is None:
            raise KeyError(f"context 不存在: {context_id}")
        # 每次 run 是新的会话轮次——清除上次的 stop 标志（防残留中断）
        # （方案 2026-08-24-stop实时中断：stop 是一次性的，run 重新开始）
        context._stop_flag.clear()
        agent = self.pool.acquire(context)  # 从 pool 取执行者（绑定 context）

        # 后台线程跑整个 chat（LLM + 工具循环）——主线程可响应 stop
        result_box: dict = {}

        def _worker() -> None:
            try:
                result_box["value"] = agent.chat(
                    user_input, stream_callback=stream_callback)
            except Exception as exc:
                result_box["error"] = exc

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        # 主线程双事件等待：stop → 实时中断；done → 正常取结果
        outcome = context.wait_stop_or_done()
        if outcome == "stopped":
            self.pool.release(agent)  # 旧 agent 丢弃（线程自然回收）
            self._persist(context)
            return "已按指令中断当前任务。"

        self.pool.release(agent)  # 即用即弃（生命周期在 pool）
        self._persist(context)
        if "error" in result_box:
            raise result_box["error"]
        return result_box["value"]

    def _persist(self, context: AgentContext) -> None:
        """会话持久化（方案 2026-08-26）：persist=True + 有 storage 时落盘。

        write-behind：后台线程异步写（不阻塞主流程）；崩溃丢尾可接受。
        双模型：append 日志（只写新增消息——增量）+ snapshot 状态字段。
        """
        if not context.persist or self.storage is None:
            return

        def _worker() -> None:
            try:
                # 会话不存在则创建（幂等）
                existing = self.storage.load_session(context.id)
                if existing is None:
                    self.storage.create_session(context.id,
                                                title=context.goal or "对话")
                # 增量 append：只写上次持久化之后的新消息（防重复）
                start = getattr(context, "_persisted_count", 0)
                for msg in context.messages[start:]:
                    self.storage.append_message(context.id, msg)
                context._persisted_count = len(context.messages)
                self.storage.snapshot(
                    context.id,
                    turn=context.turn,
                    usage=context.usage,
                    status=context.status.value,
                    phase=context.phase.value,
                )
            except Exception:
                pass  # 持久化失败不阻塞对话（记录级容错）

        threading.Thread(target=_worker, daemon=True).start()
