"""评测专用 Gateway：通过组合复用普通数据面，隔离评测控制面。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from qi_agent.context.context import AgentContext
from qi_agent.gateway.gateway import Gateway
from qi_agent.gateway.protocol import (
    ERROR_INVALID_PARAMS,
    ERROR_SESSION_NOT_FOUND,
    RpcDispatcher,
    RpcError,
    log_rpc,
)
from qi_agent.storage.memory_store import MemoryStore


_HISTORY_ROLES = {"system", "user", "assistant", "tool"}
_MAX_HISTORY_MESSAGES = 200
_MAX_MESSAGE_CHARS = 8_000
_MAX_MEMORY_ITEMS = 50
_MAX_MEMORY_CHARS = 2_000


class _CompositeDispatcher:
    """把 eval/* 控制面和普通 Gateway 数据面组合起来。"""

    def __init__(self, normal: RpcDispatcher, evaluation: RpcDispatcher) -> None:
        self._normal = normal
        self._evaluation = evaluation

    def dispatch(self, raw: str) -> str:
        try:
            method = str(json.loads(raw).get("method") or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._normal.dispatch(raw)
        if method.startswith("eval/"):
            return self._evaluation.dispatch(raw)
        return self._normal.dispatch(raw)


class _EvalFixture:
    """单个评测 Session 的旁路状态。"""

    def __init__(self, context: AgentContext, preconditions: dict[str, Any]) -> None:
        self.context = context
        self.preconditions = preconditions
        self.history = _validate_history(preconditions.get("history", []))
        self.memory = _validate_memory(preconditions.get("memory", []))
        context = preconditions.get("context") or {}
        self.workspace = _validate_workspace(context.get("workspace", {}))
        self.plugin_overrides = _validate_plugin_overrides(
            context.get("plugin_overrides", {})
        )
        self.sticky = _validate_sticky(context.get("sticky", []))
        self._memory_snapshot = _snapshot_memory()
        self.cleaned = False

    def install(self) -> None:
        if self.history:
            self.context.events.on(
                "agent/pre-step", self._inject_history, priority=900
            )
        if self.memory or self.sticky:
            self.context.events.on(
                "agent/pre-step", self._inject_context, priority=850
            )

    def _inject_history(self, messages: list[dict], **_: Any) -> list[dict]:
        if getattr(self.context, "_qi_eval_history_injected", False):
            return messages
        setattr(self.context, "_qi_eval_history_injected", True)
        system = messages[:1] if messages and messages[0].get("role") == "system" else []
        rest = messages[1:] if system else messages
        return system + self.history + rest

    def _inject_context(self, messages: list[dict], **_: Any) -> list[dict]:
        if getattr(self.context, "_qi_eval_context_injected", False):
            return messages
        setattr(self.context, "_qi_eval_context_injected", True)
        additions: list[str] = []
        if self.memory:
            additions.append(
                "以下是评测预置的长期记忆：\n"
                + "\n".join(f"§ {item['content']}" for item in self.memory)
            )
        if self.sticky:
            additions.append(
                "以下是评测预置的会话上下文：\n"
                + "\n".join(item["content"] for item in self.sticky)
            )
        if not additions:
            return messages
        result = list(messages)
        for index, message in enumerate(result):
            if message.get("role") == "system":
                result[index] = {
                    **message,
                    "content": f"{message.get('content', '')}\n\n"
                    + "\n\n".join(additions),
                }
                return result
        return [{"role": "system", "content": "\n\n".join(additions)}] + result

    def cleanup(self) -> None:
        if self.cleaned:
            return
        self.cleaned = True
        _restore_memory(self._memory_snapshot)
        if self.workspace.get("todos"):
            from qi_agent.tools.builtin.todo import _reset_store

            _reset_store()


class EvaluationGateway:
    """评测 Gateway Facade。

    普通 RPC 委托给内部 Gateway；只有 eval/* 由本类处理。
    生产 Gateway 不需要注册任何评测方法。
    """

    def __init__(self, base_gateway: Gateway | None = None) -> None:
        self.base_gateway = base_gateway or Gateway()
        self.manager = self.base_gateway.manager
        self._evaluation_dispatcher = RpcDispatcher()
        self.dispatcher = _CompositeDispatcher(
            self.base_gateway.dispatcher, self._evaluation_dispatcher
        )
        self._fixtures: dict[str, _EvalFixture] = {}
        self._lock = threading.RLock()
        self._evaluation_dispatcher.register(
            "eval/prepare", log_rpc("eval/prepare")(self._prepare)
        )
        self._evaluation_dispatcher.register(
            "eval/cleanup", log_rpc("eval/cleanup")(self._cleanup)
        )
        self._evaluation_dispatcher.register(
            "eval/inspect", log_rpc("eval/inspect")(self._inspect)
        )

    @property
    def shell_callback(self):
        return self.base_gateway.shell_callback

    @shell_callback.setter
    def shell_callback(self, callback) -> None:
        self.base_gateway.shell_callback = callback

    def _prepare(
        self,
        case_id: str,
        run_id: str,
        goal: str = "",
        preconditions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not case_id.strip() or not run_id.strip():
            raise RpcError(ERROR_INVALID_PARAMS, "case_id 和 run_id 不能为空")
        with self._lock:
            if self._fixtures:
                raise RpcError(ERROR_INVALID_PARAMS, "评测 Gateway 当前只允许串行 Case")
            precondition_spec = preconditions or {}
            context_spec = precondition_spec.get("context") or {}
            fixture_scope = f"eval-{run_id}-{case_id}"
            context = AgentContext(
                goal=goal,
                persist=True,
                metadata={"eval_case_id": case_id, "eval_run_id": run_id},
            )
            context._qi_eval_plugin_overrides = _validate_plugin_overrides(
                context_spec.get("plugin_overrides", {})
            )
            self.manager.register(context, role="main")
            self.base_gateway._storage().create_session(
                context.id, title=goal or "对话"
            )
            session_id = context.id
            context = self.manager.get_context(session_id)
            if context is None:
                raise RpcError(ERROR_SESSION_NOT_FOUND, f"Session 不存在: {session_id}")
            fixture = _EvalFixture(context, preconditions or {})
            try:
                _prepare_workspace(fixture.workspace)
                fixture.install()
            except Exception:
                # prepare 的任一步失败都不能留下 memory/todo/session 污染。
                fixture.cleanup()
                self.base_gateway._session_delete(session_id)
                raise
            self._fixtures[session_id] = fixture
            return {
                "session_id": session_id,
                "fixture_scope_id": fixture_scope,
                "prepared": {
                    "history_messages": len(fixture.history),
                    "memory_items": len(fixture.memory),
                    "sticky_items": len(fixture.sticky),
                },
            }

    def _cleanup(self, session_id: str, fixture_scope_id: str = "") -> dict[str, Any]:
        with self._lock:
            fixture = self._fixtures.pop(session_id, None)
        if fixture is None:
            return {"ok": True, "already_cleaned": True}
        cleanup_error = None
        try:
            fixture.cleanup()
        except Exception as exc:  # pragma: no cover - filesystem failure
            cleanup_error = str(exc)
        try:
            self.base_gateway._session_delete(session_id)
        except Exception as exc:  # pragma: no cover - cleanup is best effort
            cleanup_error = cleanup_error or str(exc)
        if cleanup_error:
            raise RpcError(ERROR_INVALID_PARAMS, f"评测资源清理失败: {cleanup_error}")
        return {"ok": True, "session_id": session_id}

    def _inspect(self, session_id: str) -> dict[str, Any]:
        """在清理前读取评测证据，避免清理动作覆盖断言所需结果。"""
        with self._lock:
            fixture = self._fixtures.get(session_id)
        if fixture is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND, f"评测 Fixture 不存在: {session_id}")
        context = self.manager.get_context(session_id)
        if context is None:
            raise RpcError(ERROR_SESSION_NOT_FOUND, f"Session 不存在: {session_id}")
        memory: dict[str, list[str]] = {"user": [], "memory": []}
        try:
            store = MemoryStore()
            memory = {
                "user": store.list_entries("user"),
                "memory": store.list_entries("memory"),
            }
        except Exception:
            # 记忆读取失败由正式 runner 转成断言失败，不影响 history/trace 回收。
            pass
        return {
            "session_id": session_id,
            "messages": list(context.messages),
            "usage": dict(context.usage or {}),
            "turn": context.turn,
            "status": context.status.value,
            "memory": memory,
        }


def _validate_history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _MAX_HISTORY_MESSAGES:
        raise RpcError(ERROR_INVALID_PARAMS, "preconditions.history 格式或数量无效")
    result: list[dict[str, Any]] = []
    for message in value:
        if not isinstance(message, dict) or message.get("role") not in _HISTORY_ROLES:
            raise RpcError(ERROR_INVALID_PARAMS, "history 消息 role 无效")
        content = str(message.get("content") or "")
        if len(content) > _MAX_MESSAGE_CHARS:
            raise RpcError(ERROR_INVALID_PARAMS, "history 消息过长")
        result.append({key: value for key, value in message.items() if key in {
            "role", "content", "name", "tool_call_id", "tool_calls"
        }})
    return result


def _validate_memory(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > _MAX_MEMORY_ITEMS:
        raise RpcError(ERROR_INVALID_PARAMS, "preconditions.memory 格式或数量无效")
    result = []
    for item in value:
        if not isinstance(item, dict) or item.get("target") not in {"user", "memory"}:
            raise RpcError(ERROR_INVALID_PARAMS, "memory target 无效")
        content = str(item.get("content") or "").strip()
        if not content or len(content) > _MAX_MEMORY_CHARS:
            raise RpcError(ERROR_INVALID_PARAMS, "memory content 无效")
        result.append({"target": str(item["target"]), "content": content})
    return result


def _validate_sticky(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > _MAX_MEMORY_ITEMS:
        raise RpcError(ERROR_INVALID_PARAMS, "context.sticky 格式或数量无效")
    result = []
    for item in value:
        content = str(item.get("content") or "").strip() if isinstance(item, dict) else ""
        if not content or len(content) > _MAX_MEMORY_CHARS:
            raise RpcError(ERROR_INVALID_PARAMS, "sticky content 无效")
        result.append({"content": content})
    return result


def _validate_workspace(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        raise RpcError(ERROR_INVALID_PARAMS, "context.workspace 格式无效")
    todos = value.get("todos", [])
    if not isinstance(todos, list) or len(todos) > _MAX_MEMORY_ITEMS:
        raise RpcError(ERROR_INVALID_PARAMS, "workspace.todos 格式或数量无效")
    normalized: list[dict[str, Any]] = []
    for item in todos:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            raise RpcError(ERROR_INVALID_PARAMS, "workspace.todo title 无效")
        normalized.append({
            "title": str(item["title"])[:_MAX_MESSAGE_CHARS],
            "status": str(item.get("status") or "pending"),
        })
    return {"todos": normalized}


def _validate_plugin_overrides(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RpcError(ERROR_INVALID_PARAMS, "context.plugin_overrides 格式无效")
    allowed = {"context_manager"}
    if any(key not in allowed for key in value):
        raise RpcError(ERROR_INVALID_PARAMS, "存在未允许的 plugin override")
    return value


def _prepare_workspace(workspace: dict[str, list[dict[str, Any]]]) -> None:
    todos = workspace.get("todos", [])
    if not todos:
        return
    from qi_agent.tools.builtin.todo import _reset_store, todo

    _reset_store()
    for item in todos:
        todo(action="create", title=item["title"])


def _snapshot_memory() -> dict[Path, bytes | None]:
    store = MemoryStore()
    snapshot: dict[Path, bytes | None] = {}
    for path in (Path(store.memory_path), Path(store.user_path)):
        snapshot[path] = path.read_bytes() if path.exists() else None
    return snapshot


def _restore_memory(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
