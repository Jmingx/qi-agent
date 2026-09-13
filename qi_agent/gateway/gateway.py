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
_SESSION_METADATA_KEYS = frozenset({"eval_case_id", "eval_run_id"})
# 压缩摘要的注入标记（compressor.assemble 写入）——用于上下文分项归类
_SUMMARY_MARKER = "[早期对话已压缩为摘要]"


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


def _estimate_tool_schemas_tokens() -> int:
    """工具 schema 占用的 token 估算（JSON 序列化后 char/4）。"""
    try:
        import json

        from qi_agent.tools.registry import get_tool_schemas

        schemas = get_tool_schemas()
    except Exception:
        return 0
    if not schemas:
        return 0
    try:
        return max(1, len(json.dumps(schemas, ensure_ascii=False)) // 4)
    except Exception:
        return 0


def _message_tokens(message: dict) -> int:
    """单条消息的 token 估算（content + tool_calls）。"""
    tokens = 0
    content = message.get("content")
    if content:
        tokens += max(1, len(str(content)) // 4)
    tool_calls = message.get("tool_calls")
    if tool_calls:
        tokens += max(1, len(str(tool_calls)) // 8)
    return tokens


def _estimate_context_breakdown(messages: list[dict], tool_tokens: int) -> dict[str, int]:
    """上下文分项估算：系统提示 / 工具 schema / 对话历史 / 工具输出 / 压缩摘要 / 当前输入。

    语义说明（UI v3 §9）：这是**当前占用**的构成，不是会话累计消耗。
    分项用途是回答「上下文窗口被谁吃掉了」——因此必须按消息角色归位，
    并且把压缩摘要单独列出（否则用户会以为历史还在、额度却没了）。
    估算用 char/4（对齐 context/estimator），真实 usage 到达时以 prompt_tokens 校准总量。
    """
    buckets = {
        "system": 0,       # 系统提示（含 env_info 等注入的 system 消息）
        "tools": int(tool_tokens),  # 工具 schema（不在 messages 里，需单独估算）
        "history": 0,      # 普通历史消息（user/assistant）
        "tool_output": 0,  # 工具返回（role=tool）
        "summary": 0,      # 压缩摘要
        "input": 0,        # 当前这轮的用户输入
    }
    last_user_index = -1
    for index, message in enumerate(messages):
        if str(message.get("role") or "") != "user":
            continue
        content = str(message.get("content") or "")
        if content.startswith(_SUMMARY_MARKER):
            continue
        last_user_index = index

    for index, message in enumerate(messages):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        tokens = _message_tokens(message)
        if role == "system":
            buckets["system"] += tokens
        elif role == "tool":
            buckets["tool_output"] += tokens
        elif content.startswith(_SUMMARY_MARKER):
            buckets["summary"] += tokens
        elif index == last_user_index:
            buckets["input"] += tokens
        else:
            buckets["history"] += tokens
    return buckets


def _truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


class Gateway:
    """JSON-RPC 方法和内核事件的统一入口。"""

    def __init__(
        self,
        manager: AgentManager | None = None,
    ) -> None:
        self.manager = manager or AgentManager()
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
        self.dispatcher.register("session/trace", log_rpc("session/trace")(self._session_trace))
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
        self.dispatcher.register("skill/list", log_rpc("skill/list")(self._skill_list))
        self.dispatcher.register("skill/view", log_rpc("skill/view")(self._skill_view))
        self.dispatcher.register(
            "skill/activate", log_rpc("skill/activate")(self._skill_activate)
        )
        self.dispatcher.register("skill/status", log_rpc("skill/status")(self._skill_status))

    def _storage(self):
        from qi_agent.storage import get_storage

        storage = getattr(self.manager, "storage", None)
        return storage or get_storage()

    def _create_session(
        self,
        goal: str = "",
        metadata: dict[str, str] | None = None,
    ) -> dict:
        safe_metadata = {
            key: str(value)[:128]
            for key, value in (metadata or {}).items()
            if key in _SESSION_METADATA_KEYS and value
        }
        context = AgentContext(persist=True, metadata=safe_metadata)
        self._attach_skill_plugin(context)
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
        self._attach_skill_plugin(context)
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
            context = self._get_context(session_id)
            return {"reply": reply, "turn": context.turn}
        except RuntimeError as exc:
            if "正在运行" in str(exc):
                raise RpcError(ERROR_CONCURRENT_RUN, f"context 正在运行: {session_id}") from exc
            context = self._get_context(session_id)
            self._notify(
                "turn/end", session_id=session_id, turn=context.turn,
                reason="error", error=str(exc),
            )
            raise
        except Exception as exc:
            context = self._get_context(session_id)
            self._notify(
                "turn/end", session_id=session_id, turn=context.turn,
                reason="error", error=str(exc),
            )
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

    def _session_trace(self, session_id: str) -> dict:
        context = self.manager.get_context(session_id)
        if context is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND, f"会话不存在: {session_id}")
        # trace_id 不是 context 的正式字段，而是 telemetry 插件挂在事件总线
        # 上的附加状态；Gateway 只负责转发，不直接依赖插件实现细节。
        trace_id = getattr(context.events, "_qi_telemetry_trace_id", None)
        return {"trace_id": trace_id or None}

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
        """上下文用量：**占用（context_tokens）与会话累计（session_tokens）分开**。

        历史缺陷（UI v3 §9.1）：老实现把两者塞进同一个 total_tokens——有真实 usage 时
        给的是会话累计（agent.py 把每轮 usage 累加进 context.usage），没有真实 usage 时
        （DeepSeek 流式通常不返回 usage）给的是当前占用估算。同一字段两种语义，
        header 的「x / 64k」既不表示窗口占用也不表示花费，用户无法据此判断该不该压缩。

        现在：
        - context_tokens：**当前窗口占用**（估算 = 消息 + 工具 schema，永远标注 estimated 语义）
        - session_tokens：**会话累计消耗**（有真实 usage 用真实值，否则 0 + estimated）
        - breakdown：占用由谁构成（系统提示/工具 schema/历史/工具输出/摘要/当前输入）
        旧字段全部保留（前端与 CLI 兼容），语义与 context_tokens 对齐。
        """
        context = self._get_context(session_id)
        usage = dict(context.usage or {})
        real_prompt = int(usage.get("prompt_tokens", 0) or 0)
        real_completion = int(usage.get("completion_tokens", 0) or 0)
        real_total = int(usage.get("total_tokens", 0) or 0) or (real_prompt + real_completion)
        session_estimated = real_total <= 0

        tool_tokens = _estimate_tool_schemas_tokens()
        breakdown = _estimate_context_breakdown(context.messages, tool_tokens)
        estimated_context = sum(breakdown.values())
        # 占用优先用估算（它算的是「当前消息 + schema」，语义正确）；
        # 真实 usage 只在单轮单调用时等于占用，多轮累加后不能当占用用。
        context_tokens = estimated_context
        session_tokens = real_total if real_total > 0 else 0
        percent = min(100, round(context_tokens / _CONTEXT_LIMIT * 100))

        return {
            # ── UI v3 新字段（语义唯一） ──
            "context_tokens": context_tokens,
            "context_estimated": True,
            "session_tokens": session_tokens,
            "session_completion_tokens": real_completion,
            "session_estimated": session_estimated,
            "breakdown": breakdown,
            "percent": percent,
            "warn_at": int(_CONTEXT_LIMIT * 0.8),
            "compact_at": int(_CONTEXT_LIMIT * 0.7),
            # ── 旧字段（兼容保留，与 context_tokens 对齐） ──
            "prompt_tokens": real_prompt if real_prompt > 0 else context_tokens,
            "completion_tokens": real_completion,
            "total_tokens": real_total if real_total > 0 else context_tokens,
            "est_ratio": round(context_tokens / _CONTEXT_LIMIT, 4),
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

    def _skill_list(self) -> dict:
        """列出当前注册表的 Skill；分组来自元数据而非目录层级。"""
        from qi_agent.skills.registry import get_skill_registry

        registry = get_skill_registry()
        registry.refresh()
        return {
            "skills": [
                {
                    "name": item.name,
                    "description": item.description,
                    "categories": list(item.categories),
                    "tags": list(item.tags),
                    "scope": item.scope,
                    "version": item.version,
                }
                for item in registry.list()
            ]
        }

    def _skill_view(self, skill_id: str, resource_path: str = "") -> dict:
        from qi_agent.skills.registry import get_skill_registry

        return {
            "skill_id": skill_id,
            "content": get_skill_registry().view(skill_id, resource_path),
        }

    def _skill_activate(self, session_id: str, skill_id: str, text: str) -> dict:
        """用户显式指定本轮 Skill：注入 L2 后马上执行剩余任务。"""
        if not text.strip():
            raise RpcError(ERROR_INVALID_PARAMS, "text 不能为空")
        context = self._get_context(session_id)
        from qi_agent.skills.registry import get_skill_registry

        registry = get_skill_registry()
        record = registry.get(skill_id)
        if record is None:
            raise RpcError(ERROR_INVALID_PARAMS, f"未注册的 Skill: {skill_id}")
        content = registry.view(skill_id)
        if content.startswith("[Skill"):
            raise RpcError(ERROR_INVALID_PARAMS, content)
        # Agent.chat 将在本轮开始时把 context.turn 加一。
        context.events._qi_active_skill = {
            "name": skill_id,
            "content": content,
            "turn": context.turn + 1,
        }
        reply = self._send_message(session_id, text)
        return {"skill_id": skill_id, "scope": record.scope, **reply}

    def _skill_status(self, session_id: str) -> dict:
        context = self._get_context(session_id)
        active = getattr(context.events, "_qi_active_skill", None)
        if active and active.get("turn", 0) < context.turn:
            active = None
        return {
            "session_id": session_id,
            "active": {"skill_id": active["name"]} if active else None,
        }

    @staticmethod
    def _attach_skill_plugin(context: AgentContext) -> None:
        """Gateway 创建/恢复的会话也要有 Skill 注入，不能只依赖 CLI runtime。"""
        if getattr(context.events, "_qi_skill_plugin_installed", False):
            return
        from qi_agent.plugins.builtin.skill_index import SkillIndexPlugin

        SkillIndexPlugin().install(context.events)
        context.events._qi_skill_plugin_installed = True

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
