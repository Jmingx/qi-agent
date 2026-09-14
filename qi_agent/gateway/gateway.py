"""Gateway 层：JSON-RPC 入口和内核事件桥。"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from qi_agent.agents.agent_manager import AgentManager
from qi_agent.context.context import AgentContext, ContextStatus, generate_id
from qi_agent.gateway.protocol import (
    ERROR_CONCURRENT_RUN,
    ERROR_INVALID_PARAMS,
    ERROR_SESSION_NOT_FOUND,
    RpcDispatcher,
    RpcError,
    RpcNotification,
    log_rpc,
)
from qi_agent.logging_setup import get_run_logger
from qi_agent.workspaces import SessionWorkspace, normalize_workspace

APPROVAL_TIMEOUT = 60.0
_CONTEXT_LIMIT = 64_000
_SESSION_METADATA_KEYS = frozenset({"eval_case_id", "eval_run_id"})
# 压缩摘要的注入标记（compressor.assemble 写入）——用于上下文分项归类
_SUMMARY_MARKER = "[早期对话已压缩为摘要]"

# 已决审批表上限（幂等响应需要记住最近解决过的 id，但不能无限增长）
_MAX_RESOLVED_APPROVALS = 200

# 旧前端契约兼容：decision=approve/deny → 新选项值
_LEGACY_DECISIONS = {"approve": "once", "deny": "deny"}

# 命令脱敏规则（内核侧统一执行：日志 / 外壳 / 未来界面共用一份，避免各写一套）
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization: Bearer <token> / Basic <base64>
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{6,}"), r"\1 ***"),
    # api_key=xxx / token: xxx / password=xxx / secret: xxx
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|credential)"
                r"(\s*[=:]\s*)\S+"), r"\1\2***"),
    # 常见密钥前缀（sk-/ghp_/xoxb-…）
    (re.compile(r"\b(sk|pk|ghp|gho|glpat|xox[baprs])[_\-][A-Za-z0-9_\-]{6,}"), "***"),
)


@dataclass
class _ApprovalEntry:
    """一条待决审批：归属会话 + 合法选项集 + 唤醒事件 + 用户选择。"""

    session_id: str
    choices: list[str]
    event: threading.Event = field(default_factory=threading.Event)
    choice: str | None = None


def redact_command(command: str) -> str:
    """脱敏命令中的凭证（展示前调用；只改展示值，不影响真实执行）。"""
    redacted = command or ""
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _normalize_approval_options(options: Any) -> list[dict[str, str]]:
    """审批选项统一成 `[{"value","label","tone"}]`（通知 payload 形态）。"""
    from qi_agent.interaction import normalize_options

    return [option.to_dict() for option in normalize_options(options)]


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
        "system": 0,  # 系统提示（含 env_info 等注入的 system 消息）
        "tools": int(tool_tokens),  # 工具 schema（不在 messages 里，需单独估算）
        "history": 0,  # 普通历史消息（user/assistant）
        "tool_output": 0,  # 工具返回（role=tool）
        "summary": 0,  # 压缩摘要
        "input": 0,  # 当前这轮的用户输入
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
        # 审批表（2026-09-13 方案 §3.4）：id → 待决条目；已决表用于幂等响应。
        # 加锁原因：多会话并发 + 并行工具调用会同时发起/响应审批。
        self._approvals: dict[str, "_ApprovalEntry"] = {}
        self._approval_results: dict[str, str] = {}
        self._approval_lock = threading.Lock()
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
        self.dispatcher.register("skill/activate", log_rpc("skill/activate")(self._skill_activate))
        self.dispatcher.register("skill/status", log_rpc("skill/status")(self._skill_status))
        self.dispatcher.register("workspace/list", log_rpc("workspace/list")(self._list_workspaces))
        self.dispatcher.register("workspace/pick", log_rpc("workspace/pick")(self._pick_workspace))
        self.dispatcher.register("workspace/add", log_rpc("workspace/add")(self._add_workspace))
        self.dispatcher.register(
            "workspace/remove", log_rpc("workspace/remove")(self._remove_workspace)
        )

    def _storage(self):
        from qi_agent.storage import get_storage

        storage = getattr(self.manager, "storage", None)
        return storage or get_storage()

    def _create_session(
        self,
        goal: str = "",
        metadata: dict[str, str] | None = None,
        workspace_id: str = "",
    ) -> dict:
        safe_metadata = {
            key: str(value)[:128]
            for key, value in (metadata or {}).items()
            if key in _SESSION_METADATA_KEYS and value
        }
        workspace = self._load_session_workspace(workspace_id) if workspace_id else None
        context = AgentContext(persist=True, metadata=safe_metadata, workspace=workspace)
        self._attach_skill_plugin(context)
        context.goal = goal
        context._persisted_count = 0
        self.manager.register(context, role="main")
        self._storage().create_session(
            context.id,
            title=goal or "对话",
            workspace_id=workspace.workspace_id if workspace else None,
        )
        return {"session_id": context.id, **self._workspace_payload(workspace)}

    def _resume_session(self, session_id: str) -> dict:
        loaded = self._storage().load_session(session_id)
        if loaded is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND, f"会话不存在: {session_id}")
        workspace = self._load_session_workspace(str(loaded.get("workspace_id") or ""))
        context = AgentContext(persist=True, context_id=session_id, workspace=workspace)
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
        return {
            "session_id": session_id,
            "turn": context.turn,
            "messages": len(context.messages),
            **self._workspace_payload(workspace),
        }

    def _load_session_workspace(self, workspace_id: str) -> SessionWorkspace | None:
        if not workspace_id:
            return None
        storage = self._storage()
        getter = getattr(storage, "get_workspace", None)
        record = getter(workspace_id) if getter else None
        if not record:
            raise RpcError(ERROR_INVALID_PARAMS, "工作空间不存在或已移除")
        try:
            root = normalize_workspace(str(record["canonical_path"]))
        except (KeyError, OSError, ValueError) as exc:
            raise RpcError(ERROR_INVALID_PARAMS, f"工作空间不可用: {exc}") from exc
        return SessionWorkspace(
            workspace_id=str(record["id"]), root=root, label=str(record["label"])
        )

    @staticmethod
    def _workspace_payload(workspace: SessionWorkspace | None) -> dict:
        if workspace is None:
            return {"workspace_id": None, "workspace_label": None, "workspace_available": False}
        return {
            "workspace_id": workspace.workspace_id,
            "workspace_label": workspace.label,
            "workspace_available": True,
        }

    def _list_workspaces(self) -> dict:
        storage = self._storage()
        lister = getattr(storage, "list_workspaces", None)
        if lister is None:
            return {"workspaces": []}
        result = []
        for item in lister():
            path = str(item["canonical_path"])
            result.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "path": path,
                    "available": Path(path).is_dir(),
                }
            )
        return {"workspaces": result}

    def _pick_workspace(self) -> dict:
        """打开本机系统的文件夹选择框，返回用户明确选中的目录。

        浏览器不能获得本机绝对路径；选择框必须由与工具同机的 Gateway
        打开。此处只负责取得用户选择，不登记目录，也不改变进程 cwd。
        """
        try:
            import tkinter as tk
            from tkinter import filedialog

            window = tk.Tk()
            window.withdraw()
            window.attributes("-topmost", True)
            try:
                selected = filedialog.askdirectory(
                    parent=window,
                    title="选择 qi-agent 工作空间",
                    mustexist=True,
                )
            finally:
                window.destroy()
        except Exception as exc:
            raise RpcError(ERROR_INVALID_PARAMS, f"无法打开系统目录选择框: {exc}") from exc

        if not selected:
            return {"selected": False}
        try:
            root = normalize_workspace(selected)
        except (OSError, ValueError) as exc:
            raise RpcError(ERROR_INVALID_PARAMS, f"所选目录不可用: {exc}") from exc
        return {
            "selected": True,
            "path": str(root),
            "label": root.name or root.drive or str(root),
        }

    def _add_workspace(self, path: str, label: str = "") -> dict:
        root = normalize_workspace(path)
        storage = self._storage()
        adder = getattr(storage, "add_workspace", None)
        if adder is None:
            raise RpcError(ERROR_INVALID_PARAMS, "当前存储不支持工作空间")
        record = adder(generate_id("ws"), (label.strip() or root.name), str(root))
        return {
            "workspace": {
                "id": record["id"],
                "label": record["label"],
                "path": record["canonical_path"],
                "available": True,
            }
        }

    def _remove_workspace(self, workspace_id: str) -> dict:
        remover = getattr(self._storage(), "remove_workspace", None)
        if remover is None or not remover(workspace_id):
            raise RpcError(ERROR_INVALID_PARAMS, "工作空间不存在或已移除")
        return {"removed": True}

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
                "turn/end",
                session_id=session_id,
                turn=context.turn,
                reason="error",
                error=str(exc),
            )
            raise
        except Exception as exc:
            context = self._get_context(session_id)
            self._notify(
                "turn/end",
                session_id=session_id,
                turn=context.turn,
                reason="error",
                error=str(exc),
            )
            raise

    def _respond_approval(self, session_id: str, approval_id: str,
                          choice: str | None = None,
                          decision: str | None = None) -> dict:
        """外壳响应审批：校验会话归属 + 选项合法性，唤醒等待中的 agent 线程。

        - `choice`（新契约）：选项 value（once/session/deny…）
        - `decision`（旧契约兼容）：approve → once / deny → deny
        - 重复响应：幂等返回 `{"ok": True, "duplicate": True}`（前端重试安全）
        """
        with self._approval_lock:
            entry = self._approvals.get(approval_id)
            if entry is None:
                if approval_id in self._approval_results:
                    return {"ok": True, "duplicate": True}
                raise RpcError(ERROR_INVALID_PARAMS, f"审批不存在或已超时: {approval_id}")
            if entry.session_id != session_id:
                # 会话归属校验（缺陷 D2）：别的会话拿到 id 也不能代为放行
                raise RpcError(
                    ERROR_INVALID_PARAMS, f"审批不属于该会话: {approval_id}"
                )
            value = choice or _LEGACY_DECISIONS.get(str(decision or ""), "")
            if value not in entry.choices:
                raise RpcError(
                    ERROR_INVALID_PARAMS,
                    f"审批选项不合法: {value}（可选: {entry.choices}）",
                )
            entry.choice = value
            self._approval_results[approval_id] = value
            if len(self._approval_results) > _MAX_RESOLVED_APPROVALS:
                self._approval_results.pop(next(iter(self._approval_results)), None)
        entry.event.set()
        get_run_logger().info(
            "approval-response context=%s approval=%s choice=%s",
            session_id, approval_id, value,
        )
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
        question: str,
        options: Any = None,
        *,
        meta: dict | None = None,
        timeout: float | None = None,
    ) -> str | None:
        """内核调用的审批入口：发通知给外壳 + 阻塞等待用户选择。

        Args:
            session_id: 发起会话（审批归属，响应时校验）
            question: 展示给用户的问题（风险/权限范围由决策层写清）
            options: 选项（InteractionOption / dict / str）
            meta: 结构化上下文（tool/code/command/arguments）
            timeout: 等待上限（None → APPROVAL_TIMEOUT）

        Returns:
            用户所选项的 value；超时或无人响应 → None（= 拒绝，fail-closed）
        """
        wait = APPROVAL_TIMEOUT if timeout is None else timeout
        payload = _normalize_approval_options(options)
        context = dict(meta or {})
        # 发号用统一 ID util（缺陷 D1：旧实现 f"ap_{len+1}" 会在连续审批中复用）
        approval_id = generate_id("ap")
        entry = _ApprovalEntry(
            session_id=session_id,
            choices=[item["value"] for item in payload],
        )
        with self._approval_lock:
            self._approvals[approval_id] = entry
        logger = get_run_logger()
        started = time.monotonic()
        logger.info(
            "approval-request context=%s approval=%s tool=%s code=%s options=%s command=%s",
            session_id, approval_id, context.get("tool", ""), context.get("code", ""),
            entry.choices, redact_command(str(context.get("command", ""))),
        )
        try:
            self._notify(
                "serverRequest/approval",
                session_id=session_id,
                root_session_id=self._resolve_root_session(session_id),
                approval_id=approval_id,
                question=question,
                options=payload,
                name=str(context.get("tool", "")),
                code=str(context.get("code", "")),
                command=redact_command(str(context.get("command", ""))),
                arguments=context.get("arguments") or {},
                tool_call_id=str(context.get("tool_call_id", "")),
                turn=context.get("turn"),
                timeout_ms=int(wait * 1000),
            )
            entry.event.wait(timeout=wait)
            if entry.choice is None:
                # 超时 = 拒绝（fail-closed）：可观测的核心健康指标
                logger.warning(
                    "approval-timeout context=%s approval=%s waited=%.1fs",
                    session_id, approval_id, time.monotonic() - started,
                )
            self._emit_approval_resolved(
                session_id=session_id,
                approval_id=approval_id,
                context=context,
                choice=entry.choice or "timeout",
                waited_ms=int((time.monotonic() - started) * 1000),
            )
            return entry.choice
        finally:
            with self._approval_lock:
                self._approvals.pop(approval_id, None)

    def _emit_approval_resolved(
        self, *, session_id: str, approval_id: str, context: dict,
        choice: str, waited_ms: int,
    ) -> None:
        """已决通知（M2-a）：记录由内核产出，前端把它落在触发它的工具行上。

        没有这条通知，弹框一收起就"什么都没发生过"（2026-09-14 UX 反馈）；
        超时同样发（choice="timeout"），否则超时只留在 run.log 里。
        """
        self._notify(
            "item/approvalResolved",
            session_id=session_id,
            root_session_id=self._resolve_root_session(session_id),
            approval_id=approval_id,
            tool_call_id=str(context.get("tool_call_id", "")),
            turn=context.get("turn"),
            name=str(context.get("tool", "")),
            code=str(context.get("code", "")),
            choice=choice,
            waited_ms=max(0, waited_ms),
            decided_at=int(time.time() * 1000),
        )

    def _resolve_root_session(self, session_id: str) -> str:
        """沿 parent_id 上溯到根会话（子 agent 的审批弹到用户正看的会话）。

        找不到 context（会话已结束/ID 未知）→ 原样返回（前端按该 id 兜底匹配）。
        """
        context = self.manager.get_context(session_id)
        current = session_id
        while context is not None and getattr(context, "parent_id", ""):
            parent = context.parent_id
            parent_context = self.manager.get_context(parent)
            if parent_context is None:
                return parent
            current, context = parent, parent_context
        return current
