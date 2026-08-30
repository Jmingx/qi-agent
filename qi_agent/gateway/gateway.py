"""网关层（方案 2026-08-28-内核外壳分离——Gateway 类）。

职责：JSON-RPC 方法实现（调内核 AgentManager）+ 事件回调桥
（内核事件 → 网关转发外壳）。进程内 Phase 1——签名对齐 JSON-RPC
方法（未来换传输 stdio/socket 不换接口）。

方法集（对齐 Codex App Server 命名）：
  session/create      创建会话
  session/resume      恢复会话
  message/send        用户输入 → 内核处理（阻塞至本轮结束）
  approval/respond    审批响应（approval 请求回执）
  session/stop        中断当前任务

事件桥（内核 → 外壳）：
  serverRequest/approval  审批请求（内核暂停等待）
  item/agentMessage/delta 流式输出增量
  turn/end                本轮结束
"""

import threading
from typing import Callable

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext
from qi_agent.gateway.protocol import (
    ERROR_CONCURRENT_RUN, ERROR_INVALID_PARAMS, ERROR_SESSION_NOT_FOUND,
    RpcDispatcher, RpcError, RpcNotification, log_rpc,
)

# 审批超时（秒）——超时 = 拒绝（fail-closed，对齐现有审批）
APPROVAL_TIMEOUT = 60.0


class Gateway:
    """网关：JSON-RPC 方法实现 + 事件桥（调内核，不碰 IO/UI）。"""

    def __init__(self, manager: AgentManager | None = None) -> None:
        self.manager = manager or AgentManager()
        self.dispatcher = RpcDispatcher()
        # 外壳回调（shell 注入——唯一读 stdin/渲染的地方）
        self.shell_callback: Callable | None = None
        # 审批等待表（approval_id → event）
        self._approval_events: dict[str, threading.Event] = {}
        self._approval_results: dict[str, bool] = {}
        self._register_methods()

    # ── 方法注册 ─────────────────────────────────────────────────────────
    def _register_methods(self) -> None:
        # 用 log_rpc 装饰（可观测——接口日志）
        self.dispatcher.register("session/create",
                                 log_rpc("session/create")(self._create_session))
        self.dispatcher.register("session/resume",
                                 log_rpc("session/resume")(self._resume_session))
        self.dispatcher.register("message/send",
                                 log_rpc("message/send")(self._send_message))
        self.dispatcher.register(
            "approval/respond",
            log_rpc("approval/respond")(self._respond_approval))
        self.dispatcher.register("session/stop",
                                 log_rpc("session/stop")(self._stop_session))
        self.dispatcher.register("session/delegate",
                                 log_rpc("session/delegate")(self._delegate))

    # ── 方法实现（调内核）────────────────────────────────────────────────
    def _create_session(self, goal: str = "") -> dict:
        context = AgentContext(persist=True)
        self.manager.register(context, role="main")
        return {"session_id": context.id}

    def _resume_session(self, session_id: str) -> dict:
        from qi_agent.storage import get_storage

        loaded = get_storage().load_session(session_id)
        if loaded is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND,
                           f"会话不存在: {session_id}")
        context = AgentContext(persist=True, context_id=session_id)
        context.messages = loaded["messages"]
        context.turn = loaded["turn"]
        context.usage = loaded["usage"]
        context.system_prompt = (
            context.messages[0]["content"] if context.messages
            and context.messages[0]["role"] == "system" else "")
        self.manager.register(context, role="main")
        return {"session_id": session_id, "turn": context.turn,
                "messages": len(context.messages)}

    def _send_message(self, session_id: str, text: str) -> dict:
        # 并发防护（内核已做——RUNNING 拒绝；网关兜底翻译错误码）
        self._get_context(session_id)  # 校验会话存在（不存在抛 RpcError）
        try:
            reply = self.manager.run(
                session_id, text,
                stream_callback=self._make_stream_callback(session_id))
            return {"reply": reply}
        except RuntimeError as exc:
            if "正在运行" in str(exc):
                raise RpcError(ERROR_CONCURRENT_RUN,
                               f"context 正在运行: {session_id}") from exc
            raise

    def _respond_approval(self, session_id: str, approval_id: str,
                          decision: str) -> dict:
        """审批响应（外壳 → 内核——唤醒等待的审批）。"""
        event = self._approval_events.get(approval_id)
        if event is None:
            raise RpcError(ERROR_INVALID_PARAMS,
                           f"审批不存在或已超时: {approval_id}")
        self._approval_results[approval_id] = decision == "approve"
        event.set()  # 唤醒内核等待
        return {"ok": True}

    def _stop_session(self, session_id: str) -> dict:
        context = self._get_context(session_id)
        stopped = context.stop()
        return {"stopped": stopped is not None or True}

    def _delegate(self, session_id: str, goal: str, timeout: float = 300.0) -> dict:
        """拉起子 agent（外壳 /delegate → 内核 manager.spawn）。

        2026-08-30 补全：/delegate 之前是空壳命令。
        2026-08-30 修复：同步等子结果（原异步——CLI 拉起后立即返回，
        结果躺父 mailbox 没人展示——体验断裂）。现在等子完成返回结果
        （和 delegate_task 工具路径一致的用户体验）。
        """
        parent = self._get_context(session_id)
        sub = self.manager.spawn(goal, parent_id=parent.id)
        result = sub.wait(timeout=300)  # 等子完成（结果在 context.result）
        return {"session_id": sub.id, "status": "spawned",
                "result": result}

    # ── 内核 → 外壳事件桥 ────────────────────────────────────────────────
    def _get_context(self, session_id: str) -> AgentContext:
        context = self.manager.contexts.get(session_id)
        if context is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND,
                           f"会话不存在: {session_id}")
        return context

    def _make_stream_callback(self, session_id: str) -> Callable:
        def _cb(delta: str) -> None:
            self._notify("item/agentMessage/delta",
                         session_id=session_id, text=delta)
        return _cb

    def _notify(self, method: str, **params) -> None:
        """发通知给外壳（shell_callback 注入——外壳实现渲染）。"""
        if self.shell_callback is not None:
            self.shell_callback(RpcNotification(method=method,
                                                params=params).to_json())

    # ── 审批桥（内核暂停等待 → 外壳弹窗 → 响应唤醒）──────────────────────
    def request_approval(self, session_id: str, command: str,
                         arguments: dict | None = None) -> bool:
        """内核调用的审批入口：发通知给外壳 + 暂停等待响应。

        阻塞直到外壳 respond（或超时——超时 = 拒绝 fail-closed）。
        """
        approval_id = f"ap_{len(self._approval_events) + 1}"
        event = threading.Event()
        self._approval_events[approval_id] = event
        self._notify("serverRequest/approval",
                     session_id=session_id, approval_id=approval_id,
                     command=command, arguments=arguments or {})
        # 等待外壳响应（approval/respond 会 set event）
        event.wait(timeout=APPROVAL_TIMEOUT)
        result = self._approval_results.pop(approval_id, False)  # 超时 = 拒绝
        self._approval_events.pop(approval_id, None)
        return result
