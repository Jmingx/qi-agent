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

from typing import Any, Callable

from qi_agent.agents.mailbox import Dispatcher, Message, MessageType
from qi_agent.agents.pool import AgentPool
from qi_agent.context.context import AgentContext, ContextStatus, WaitOutcome
from qi_agent.util import generate_id
from qi_agent.logging_setup import get_run_logger
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
        # 邮局（方案 2026-08-29 v3 中央队列修正）：Dispatcher 独立线程搬运。
        # mailbox 统一挂 context（主/子一样）——Manager 不持有 main_mailbox
        # （修正：AgentManager 只管理 context，主 agent = 主程序创建的
        #  context，其 mailbox 随 context 注册）
        self.dispatcher = Dispatcher()
        self.dispatcher.start()  # 启动搬运线程（v3：中央队列异步投递）

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
        # 邮局（v3 修正）：mailbox 是 AgentContext 必备属性（构造即创建）——
        # register 统一注册路由（主/子一样，Manager 不特设）
        self.dispatcher.register(context.mailbox)
        context.events.emit("agent-manager/register",
                            context_id=context.id, role=role)
        return context.id

    def unregister(self, context_id: str) -> None:
        """注销（任务结束清理）。"""
        with self._lock:
            context = self.contexts.pop(context_id, None)
        # 邮局：注销路由（mailbox 解绑防误发）
        if context is not None:
            self.dispatcher.unregister(context_id)

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
        parent_id: str = "",
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
            parent_id: 父 context id（结果回传目标——v3 修正：
                不用魔法 "main"，投回父 context 的 mailbox）
            client_factory: 子 agent 的 LLM 客户端工厂（测试注入；生产默认）
            timeout: 超时秒数（超时 → FAILED）
            max_turns: 子 agent 最大对话轮数（预算兜底）
            tool_executor_factory: 子 agent 执行器工厂（测试注入）
            tools: 子 agent 工具白名单（None = 默认只读子集）
            write_paths: 可写路径白名单（授权清单）
        """
        session_id = generate_id("agt")
        # 收敛（方案 2026-08-29-Subagent类型收敛）：不再建 SubagentContext——
        # 直接用 AgentContext + 设子专属字段（write_paths/timeout）+ 
        # system_prompt（context_text 消除——背景直接算成 system_prompt）
        from qi_agent.context.context import AgentContext
        from qi_agent.tools.builtin.delegate_task import _SUBAGENT_PROMPT

        ctx = AgentContext(context_id=session_id, goal=goal,
                           persist=False,  # 子 agent 默认瞬态
                           max_turns=max_turns)
        # ── 子 agent 专属配置（归拢字段——AgentContext 默认空）──
        ctx.write_paths = write_paths or []   # 授权清单
        ctx.timeout = timeout                 # 子任务超时
        ctx.system_prompt = _SUBAGENT_PROMPT.format(
            goal=goal, context=context)       # 背景注入 system prompt
        ctx.parent_id = parent_id
        ctx.begin_chat()  # spawn 语义 = 立即运行（创建即 RUNNING）
        # 统一注册（v3 修正）：走 register()——dict + 邮局路由 + 事件上报
        self.register(ctx, role="subagent")

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

        # 并发额度（等待最多 60s——2026-08-30：避免无限阻塞挂死）
        # 超时未获得额度 → 通知父（NOTIFY pool_timeout——父感知排队超时）
        parent_id = getattr(context, "parent_id", "") or "main"
        if self.pool.acquire(None, timeout=60) is None:
            context.fail("并发额度等待超时（>60s）")
            get_run_logger().warning(
                "pool-timeout context=%s parent=%s", context.id, parent_id)
            self.dispatcher.send(Message(
                sender=context.id, target=parent_id, type=MessageType.NOTIFY,
                data={"event": "pool_timeout", "context_id": context.id,
                      "reason": "concurrency_slot_timeout"}))
            return
        try:
            result = _run_subagent(context, kwargs.get("client_factory"),
                                   kwargs.get("tool_executor_factory"),
                                   kwargs.get("tools"),
                                   kwargs.get("write_paths"))
            if context.status == ContextStatus.RUNNING:
                context.complete(result)
            # 日志（run.log——子任务完成）
            get_run_logger().info(
                "subagent-complete context=%s parent=%s status=%s",
                context.id, parent_id,
                (result or {}).get("status", "completed"))
            # 邮局结果回传（v3 修正）：subagent 完成 → 投回【父 context】
            # 的 mailbox（不用魔法 "main"——parent_id 寻址）
            self.dispatcher.send(Message(
                sender=context.id, target=parent_id, type=MessageType.RESULT,
                data=result))
        except Exception as exc:
            if context.status == ContextStatus.RUNNING:
                context.fail(f"子任务执行异常: {exc}")
            # 日志（run.log——子任务异常）
            get_run_logger().error(
                "subagent-error context=%s parent=%s error=%s",
                context.id, parent_id, exc)
            # 失败通知（v3 补充 2026-08-29）：意外崩溃也投 message 给父——
            # 失败通知统一（常规失败走 RESULT.data.status=="failed"，
            # 意外崩溃走这里——父 agent 都能收到，不依赖 poll）
            self.dispatcher.send(Message(
                sender=context.id, target=parent_id, type=MessageType.RESULT,
                data={"summary": "", "artifacts": [],
                      "status": "failed", "error": f"子任务执行异常: {exc}",
                      "question": None, "usage": None}))
        finally:
            self.pool.release(None)  # 回收额度（异常也不泄漏）

    # ── 控制面（任何控制者：父 agent / 用户 / CLI）───────────────────────
    def steer(self, context_id: str, message: str,
              sender_id: str = "unknown") -> bool:
        """注入补充指令（agent 下轮生效）。返回是否找到运行环境。

        不要求 RUNNING——IDLE 也能排队（用户先说"改方向"，agent 启动后
        下轮生效）。指令只是入队，运行中才消费。

        语义（2026-08-29 用户拍板）：调用者（外部/父 agent）发送邮件——
          sender = 调用者自己的 context_id（sender_id——谁调填谁）
          target = 需要做出改变的 agent（入参 context_id）
        → 消息构造在 manager 一处完成（context.steer 已删——不绕）。

        sender_id 默认 "unknown"（外部用户调用——无 context 身份）。
        """
        context = self.contexts.get(context_id)
        if context is None:
            return False
        self.dispatcher.send(Message(
            sender=sender_id,      # 调用者身份（谁调填谁的 context_id）
            target=context_id,     # 入参即 target（需要改变的 agent）
            type=MessageType.STEER,
            data=message))
        context.events.emit("subagent/steer",
                            session_id=context_id, message=message)
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

    # ── 邮局对话投递（方案 2026-08-29 v2 验收 4）─────────────────────────
    def send_message(self, context_id: str, text: str,
                     sender_id: str = "") -> bool:
        """对话投递：父 agent → subagent（持续追加消息——多轮指导）。

        父 agent 可持续给 subagent 发对话（不再 spawn 一次性传参）；
        子循环每轮 drain 消费 → 追加 context.messages（存储分离）。

        Args:
            context_id: 目标子 agent（context id——2026-08-30 命名修正：
                项目无 session 概念，context 就是会话载体，统一 context_id）
            text: 对话内容
            sender_id: 发送方 context id（v3 修正——不用魔法 "main"；
                空 = 兼容旧调用（投递方未知））

        Returns:
            是否投递成功（context 不存在/无邮箱 → False）
        """
        context = self.contexts.get(context_id)
        if context is None or getattr(context, "mailbox", None) is None:
            return False
        self.dispatcher.send(Message(
            sender=sender_id or "main", target=context_id,
            type=MessageType.MESSAGE, data=text))
        return True

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
        # 日志（run.log——run 入口审计；完整打印 input——不省略）
        get_run_logger().info(
            "run-start context=%s turn=%d input=%s",
            context_id, context.turn, user_input)
        # 并发防护（2026-08-28 教训：审批弹窗时用户输入被主线程抢走 →
        # 同 context 并发 run → 消息交错 + 400 + 'value' KeyError）
        # 同一 context 已有任务在跑 → 拒绝（不并发写同一数据载体）
        if context.status == ContextStatus.RUNNING:
            raise RuntimeError(
                f"context {context_id} 正在运行（status=RUNNING）——"
                f"请先 /stop 或等待完成")
        # 每次 run 是新的会话轮次——清除上次的 stop 标志（防残留中断）
        # （方案 2026-08-24-stop实时中断：stop 是一次性的，run 重新开始）
        context._stop_flag.clear()
        agent = self.pool.acquire(context)
        if agent is None:
            raise RuntimeError("AgentPool 获取执行者超时")

        def runner() -> str:
            return agent.chat(user_input, stream_callback=stream_callback)

        # 后台线程跑整个 chat（LLM + 工具循环）——主线程可响应 stop
        result_box: dict = {}
        done_box: dict = {"event": threading.Event()}  # worker 完成信号

        def _worker() -> None:
            try:
                result_box["value"] = runner()
            except Exception as exc:
                result_box["error"] = exc
            finally:
                done_box["event"].set()  # worker 真正完成（含 result_box 写入）

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        # 主线程双事件等待：stop → 实时中断；done → 正常取结果
        outcome = context.wait_stop_or_done()
        if outcome == WaitOutcome.STOPPED:
            if agent is not None:
                self.pool.release(agent)  # 旧 agent 丢弃（线程自然回收）
            self._persist(context)
            return "已按指令中断当前任务。"

        # 正常路径：等 worker 真正完成（result_box 写入）——防竞态
        done_box["event"].wait(timeout=60)
        if agent is not None:
            self.pool.release(agent)  # 即用即弃（生命周期在 pool）
        self._persist(context)
        if "error" in result_box:
            raise result_box["error"]
        self._maybe_extract_memory(context)  # 主动记忆：每 10 轮触发提炼
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

    def _maybe_extract_memory(self, context: AgentContext) -> None:
        """主动记忆（方案 2026-08-26-主动记忆系统 V1）：每 10 轮触发提炼。

        提炼 = spawn 一个 subagent（后台 daemon 线程，复用现有执行链）：
          subagent 读最近对话 → LLM 分析提炼 → 写 MEMORY.md/USER.md
        - 主对话不阻塞（异步）
        - 失败容错：提炼失败不影响主对话（结果丢弃）
        - 无需审批（方案 D3——记忆文件直接写）
        """
        interval = getattr(context, "memory_extract_interval", 10)
        last = getattr(context, "last_extract_turn", 0)
        if context.turn - last < interval:
            return  # 未到提炼间隔
        context.last_extract_turn = context.turn

        # 提炼 subagent：读最近 N 轮消息 → LLM 提炼 → 写记忆文件
        recent_messages = context.messages[-20:]  # 最近约 10 轮（含 system）

        def _extract_worker() -> None:
            try:
                from qi_agent.agents.factory import load_api_key
                from qi_agent.llm import LLMClient
                from qi_agent.storage.memory_store import MemoryStore

                # 与主对话一致：LLMClient 需要 api_key（2026-08-28 教训：
                # 无参调用炸 missing api_key——真实场景提炼失败）
                client = LLMClient(load_api_key())
                # 提炼 prompt：让 LLM 输出"值得长期记住的信息"
                extract_prompt = (
                    "你是记忆提炼助手。分析下面的对话，提取【值得长期记住】"
                    "的信息（用户偏好/身份/项目决策/关键约定）。\n"
                    "输出格式：每行一条，以 [USER] 或 [MEMORY] 开头表示去向"
                    "（[USER]=用户画像，如偏好/身份；[MEMORY]=长期知识，如决策）。\n"
                    "示例：\n[USER] 用户喜欢简洁回答\n"
                    "[MEMORY] 项目决定用 SQLite\n"
                    "没有值得记的 → 输出 NONE。\n\n"
                    f"对话：\n{recent_messages}"
                )
                result = client.chat(
                    [{"role": "system", "content": extract_prompt}])
                content = result.content.strip()
                if not content or content.upper() == "NONE":
                    return
                store = MemoryStore()
                wrote = 0
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("[USER]"):
                        store.add_memory(line[6:].strip(), target="user")
                        wrote += 1
                    elif line.startswith("[MEMORY]"):
                        store.add_memory(line[8:].strip(), target="memory")
                        wrote += 1
                    else:
                        # 格式容错（2026-08-27 教训：LLM 不带前缀输出
                        # 被静默丢弃 → 记忆永远写不进）：
                        # 无前缀行 → 启发式判断去向（含偏好词 → USER，
                        # 否则 MEMORY），默认 MEMORY
                        target = ("user" if any(
                            kw in line for kw in ("喜欢", "偏好", "爱好",
                                                  "我叫", "我的", "习惯"))
                            else "memory")
                        store.add_memory(line, target=target)
                        wrote += 1
                # 可观测：提炼结果留痕（不再静默）
                if wrote:
                    context.events.emit(
                        "agent/memory-extracted",
                        context_id=context.id, count=wrote)
            except Exception as exc:
                # 提炼失败不影响主对话，但留痕（可观测——不静默吞）
                context.events.emit(
                    "agent/memory-extract-failed",
                    context_id=context.id, error=str(exc))

        threading.Thread(target=_extract_worker, daemon=True).start()
