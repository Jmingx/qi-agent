"""Gateway 层：JSON-RPC 入口和内核事件桥。"""

from __future__ import annotations

import threading
from typing import Any, Callable

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext, ContextStatus
from qi_agent.gateway.protocol import (
    ERROR_CONCURRENT_RUN,
    ERROR_INVALID_PARAMS,
    ERROR_SESSION_NOT_FOUND,
    RpcDispatcher,
    RpcError,
    RpcNotification,
    log_rpc,
)

APPROVAL_TIMEOUT = 60.0
_CONTEXT_LIMIT = 64_000


def _estimate_message_tokens(messages: list[dict]) -> int:
    """用消息字符长度粗略估算 token。"""
    tokens = 0
    for message in messages:
        content = message.get("content")
        if content:
            tokens += max(1, len(str(content)) // 4)
        tool_calls = message.get("tool_calls")
        if tool_calls:
            tokens += max(1, len(str(tool_calls)) // 8)
    return tokens


def _truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


class Gateway:
    """JSON-RPC 方法和内核事件的统一入口。"""

    def __init__(
        self,
        manager: AgentManager | None = None,
        provider: str = "deepseek",
    ) -> None:
        self.manager = manager or AgentManager(provider=provider)
        self.dispatcher = RpcDispatcher()
        self.shell_callback: Callable[[str], None] | None = None
        self._approval_events: dict[str, threading.Event] = {}
        self._approval_results: dict[str, bool] = {}
        self._register_methods()

    def _register_methods(self) -> None:
        self.dispatcher.register("session/create", log_rpc("session/create")(self._create_session))
        self.dispatcher.register("session/resume", log_rpc("session/resume")(self._resume_session))
        self.dispatcher.register("message/send", log_rpc("message/send")(self._send_message))
        self.dispatcher.register(
            "approval/respond",
            log_rpc("approval/respond")(self._respond_approval),
        )
        self.dispatcher.register("session/stop", log_rpc("session/stop")(self._stop_session))
        self.dispatcher.register("session/delegate", log_rpc("session/delegate")(self._delegate))
        self.dispatcher.register(
            "session/delegate_async",
            log_rpc("session/delegate_async")(self._delegate_async),
        )
        self.dispatcher.register("session/list", log_rpc("session/list")(self._list_sessions))
        self.dispatcher.register("session/status", log_rpc("session/status")(self._session_status))
        self.dispatcher.register("session/delete", log_rpc("session/delete")(self._session_delete))
        self.dispatcher.register("session/search", log_rpc("session/search")(self._session_search))
        self.dispatcher.register("context/info", log_rpc("context/info")(self._context_info))
        self.dispatcher.register(
            "context/history",
            log_rpc("context/history")(self._context_history),
        )
        self.dispatcher.register(
            "context/compact",
            log_rpc("context/compact")(self._context_compact),
        )
        self.dispatcher.register("context/clear", log_rpc("context/clear")(self._context_clear))
        self.dispatcher.register("context/usage", log_rpc("context/usage")(self._context_usage))
        self.dispatcher.register("memory/get", log_rpc("memory/get")(self._memory_get))
        self.dispatcher.register("memory/save", log_rpc("memory/save")(self._memory_save))
        self.dispatcher.register("memory/remove", log_rpc("memory/remove")(self._memory_remove))

    def _storage(self):
        from qi_agent.storage import get_storage

        storage = getattr(self.manager, "storage", None)
        return storage or get_storage()

    def _create_session(self, goal: str = "") -> dict:
        context = AgentContext(persist=True)
        context.goal = goal
        context._persisted_count = 0
        self.manager.register(context, role="main")
        self._storage().create_session(context.id, title=goal or "对话")
        return {"session_id": context.id}

    def _resume_session(self, session_id: str) -> dict:
        loaded = self._storage().load_session(session_id)
        if loaded is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND, f"会话不存在: {session_id}")
        context = AgentContext(persist=True, context_id=session_id)
        context.messages = loaded["messages"]
        context.turn = loaded["turn"]
        context.usage = loaded["usage"]
        context.system_prompt = (
            context.messages[0]["content"]
            if context.messages and context.messages[0].get("role") == "system"
            else ""
        )
        context._persisted_count = len(context.messages)
        self.manager.register(context, role="main")
        return {"session_id": session_id, "turn": context.turn, "messages": len(context.messages)}

    def _send_message(self, session_id: str, text: str) -> dict:
        self._get_context(session_id)
        try:
            reply = self.manager.run(
                session_id,
                text,
                stream_callback=self._make_stream_callback(session_id),
            )
            return {"reply": reply}
        except RuntimeError as exc:
            if "正在运行" in str(exc):
                raise RpcError(ERROR_CONCURRENT_RUN, f"context 正在运行: {session_id}") from exc
            self._notify("turn/end", session_id=session_id, reason="error", error=str(exc))
            raise
        except Exception as exc:
            self._notify("turn/end", session_id=session_id, reason="error", error=str(exc))
            raise

    def _respond_approval(self, session_id: str, approval_id: str, decision: str) -> dict:
        event = self._approval_events.get(approval_id)
        if event is None:
            raise RpcError(ERROR_INVALID_PARAMS, f"审批不存在或已超时: {approval_id}")
        self._approval_results[approval_id] = decision == "approve"
        event.set()
        return {"ok": True}

    def _stop_session(self, session_id: str) -> dict:
        context = self._get_context(session_id)
        stopped = context.stop()
        return {"stopped": stopped is not None or True}

    def _delegate(self, session_id: str, goal: str, timeout: float = 300.0) -> dict:
        parent = self._get_context(session_id)
        sub = self.manager.spawn(goal, parent_id=parent.id)
        result = sub.wait(timeout=timeout)
        return {"session_id": sub.id, "status": "spawned", "result": result}

    def _delegate_async(self, session_id: str, goal: str) -> dict:
        parent = self._get_context(session_id)
        sub = self.manager.spawn(goal, parent_id=parent.id)
        return {"sub_id": sub.id, "status": "spawned"}

    def _list_sessions(self) -> dict:
        try:
            sessions = self._storage().list_sessions()
        except Exception:
            sessions = []
        active = [
            cid
            for cid, ctx in self.manager.contexts.items()
            if ctx.status != ContextStatus.COMPLETED
        ]
        return {"active": active, "sessions": sessions}

    def _session_status(self, session_id: str) -> dict:
        context = self.manager.get_context(session_id)
        if context is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND, f"会话不存在: {session_id}")
        return {
            "session_id": session_id,
            "status": context.status.value,
            "turn": context.turn,
            "messages": len(context.messages),
            "result": context.result,
            "error": context.error or getattr(context, "_error", None),
        }

    def _session_delete(self, session_id: str) -> dict:
        context = self.manager.get_context(session_id)
        if context is not None and context.status == ContextStatus.RUNNING:
            context.stop()
        if hasattr(self.manager, "unregister"):
            self.manager.unregister(session_id)
        self._storage().delete_session(session_id)
        return {"ok": True}

    def _session_search(self, query: str) -> dict:
        query = query.strip()
        if not query:
            return {"results": []}
        return {"results": self._storage().search_messages(query)}

    def _context_info(self, session_id: str) -> dict:
        context = self._get_context(session_id)
        recent = [
            {"role": message.get("role"), "content": str(message.get("content", ""))[:80]}
            for message in context.messages[-5:]
        ]
        return {
            "session_id": session_id,
            "turn": context.turn,
            "messages": len(context.messages),
            "recent": recent,
        }

    def _context_history(self, session_id: str, offset: int = 0, limit: int = 20) -> dict:
        context = self._get_context(session_id)
        total = len(context.messages)
        offset = max(0, offset)
        limit = max(0, limit)
        messages = context.messages[offset : offset + limit] if limit else []
        return {"session_id": session_id, "total": total, "messages": messages}

    def _context_compact(self, session_id: str) -> dict:
        context = self._get_context(session_id)
        from qi_agent.context.compressor import compress_messages

        try:
            summary = compress_messages(context.messages)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "summary": _truncate_text(str(summary), 200),
            "before": len(context.messages),
        }

    def _context_clear(self, session_id: str) -> dict:
        context = self._get_context(session_id)
        if context.status == ContextStatus.RUNNING:
            context.stop()
        context.reset_session()
        self._sync_context_storage(context)
        return {"ok": True, "messages": len(context.messages)}

    def _context_usage(self, session_id: str) -> dict:
        context = self._get_context(session_id)
        usage = dict(context.usage or {})
        actual = any(
            int(usage.get(key, 0) or 0) > 0
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        if actual:
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            total_tokens = int(
                usage.get("total_tokens", prompt_tokens + completion_tokens)
                or (prompt_tokens + completion_tokens)
            )
        else:
            prompt_tokens = _estimate_message_tokens(context.messages)
            completion_tokens = 0
            total_tokens = prompt_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "est_ratio": round(total_tokens / _CONTEXT_LIMIT, 4),
            "context_limit": _CONTEXT_LIMIT,
        }

    def _memory_get(self) -> dict:
        from qi_agent.storage.memory_store import MemoryStore

        try:
            memory = MemoryStore().read_memory()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "memory": _truncate_text(str(memory), 2000)}

    def _memory_save(self, text: str, target: str = "memory") -> dict:
        if not text:
            raise RpcError(ERROR_INVALID_PARAMS, "text 不能为空")
        from qi_agent.storage.memory_store import MemoryStore

        MemoryStore().add_memory(text, target=target)
        return {"ok": True}

    def _memory_remove(self, text: str, target: str = "memory") -> dict:
        if not text:
            raise RpcError(ERROR_INVALID_PARAMS, "text 不能为空")
        from qi_agent.storage.memory_store import MemoryStore

        MemoryStore().remove_memory(text, target=target)
        return {"ok": True}

    def _get_context(self, session_id: str) -> AgentContext:
        context = self.manager.contexts.get(session_id)
        if context is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND, f"会话不存在: {session_id}")
        return context

    def _sync_context_storage(self, context: AgentContext) -> None:
        storage = self._storage()
        storage.delete_session(context.id)
        storage.create_session(context.id, title=context.goal or "对话")
        for message in context.messages:
            storage.append_message(context.id, message)
        storage.snapshot(
            context.id,
            turn=context.turn,
            usage=context.usage,
            status=context.status.value,
            phase=context.phase.value,
        )
        context._persisted_count = len(context.messages)

    def _make_stream_callback(self, session_id: str) -> Callable:
        def _cb(delta: str) -> None:
            ctx = self.manager.get_context(session_id)
            turn = ctx.turn if ctx else 0
            self._notify("item/agentMessage/delta", session_id=session_id, text=delta, turn=turn)

        return _cb

    def _notify(self, method: str, **params: Any) -> None:
        if self.shell_callback is not None:
            self.shell_callback(RpcNotification(method=method, params=params).to_json())

    def request_approval(
        self,
        session_id: str,
        command: str,
        arguments: dict | None = None,
    ) -> bool:
        approval_id = f"ap_{len(self._approval_events) + 1}"
        event = threading.Event()
        self._approval_events[approval_id] = event
        self._notify(
            "serverRequest/approval",
            session_id=session_id,
            approval_id=approval_id,
            command=command,
            arguments=arguments or {},
        )
        event.wait(timeout=APPROVAL_TIMEOUT)
        result = self._approval_results.pop(approval_id, False)
        self._approval_events.pop(approval_id, None)
        return result
