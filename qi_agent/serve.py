"""内核 serve 入口。"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
import weakref
from pathlib import Path
from typing import Any

from qi_agent.gateway.gateway import Gateway
from qi_agent.plugins import load_plugins
from qi_agent.plugins.config import load_plugin_config
from qi_agent.tools.decision import ToolAction, ToolDecision


def _truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _tool_call_status(result: Any) -> tuple[str, str | None]:
    if isinstance(result, ToolDecision):
        if result.action == ToolAction.BLOCK:
            return "blocked", result.reason or None
        return "running", None
    if result is False:
        return "blocked", None
    return "running", None


def _tool_result_ok(output: str) -> bool:
    blocked_prefixes = ("[安全拦截]", "[审批拒绝]", "[参数错误]", "[工具错误]")
    return not output.startswith(blocked_prefixes)


def _summarize_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            text = str(value)
    return _truncate_text(text, limit)


def _last_user_prompt(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return str(message["content"])
    return ""


def _tool_outcome(output: str) -> str | None:
    if output.startswith(("[安全拦截]", "[审批拒绝]")):
        return "blocked"
    if output.startswith(("[参数错误]", "[工具错误]")):
        return "error"
    return None


def _contains_weak_signal(text: str) -> bool:
    weak_terms = ("抱歉", "无法", "不能", "不确定", "可能", "建议", "大概", "暂时")
    return any(term in text for term in weak_terms)


def _eval_feed_path() -> Path:
    return Path.home() / ".qi-agent" / "eval_feed" / f"{dt.date.today().isoformat()}.jsonl"


class ServeTransport:
    """把 Gateway 事件转成 WebSocket 通知。"""

    def __init__(
        self,
        gateway: Gateway,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.gateway = gateway
        self.connections: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = loop
        self._send_queues: dict[Any, asyncio.Queue[str]] = {}
        self._workers: set[Any] = set()
        self._plugin_config = load_plugin_config()
        self._feed_stats: weakref.WeakKeyDictionary[Any, dict[str, Any]] = (
            weakref.WeakKeyDictionary()
        )
        # 以对象身份去重（WeakSet）：避免同一个 session_id 的新 context 被误判为
        # “已附加”，也避免 int id() 复用导致长跑进程里新对象撞上历史 id 被跳过。
        self._attached_contexts: weakref.WeakSet[Any] = weakref.WeakSet()
        self._wrap_manager_register()
        for context in list(self.gateway.manager.contexts.values()):
            self._attach_context(context)

    def _wrap_manager_register(self) -> None:
        original_register = self.gateway.manager.register

        def wrapped_register(context, role: str = "subagent"):
            context_id = original_register(context, role)
            self._attach_context(context)
            return context_id

        self.gateway.manager.register = wrapped_register

    def _attach_context(self, context) -> None:
        if context in self._attached_contexts:
            return
        self._attached_contexts.add(context)
        # 插件通过事件总线读取这份非业务元数据，避免让插件反向依赖 Gateway。
        setattr(
            context.events,
            "_qi_observability_metadata",
            getattr(context, "metadata", {}),
        )
        load_plugins(context.events, self._plugin_config)
        self._wrap_tool_call_bail(context)
        context.events.on(
            "agent/turn-start",
            self._make_subtask_progress_handler(context, "turn-start", "📖 开始"),
        )
        context.events.on(
            "agent/pre-llm",
            self._make_subtask_progress_handler(context, "pre-llm", "🤖 调用 LLM"),
        )
        context.events.on("agent/turn-start", self._make_feed_turn_start_handler(context))
        context.events.on("agent/pre-llm", self._make_feed_pre_llm_handler(context))
        context.events.on("agent/final-answer", self._make_final_answer_handler(context))
        context.events.on("agent/tool-result", self._make_tool_result_handler(context))
        context.events.on("agent/turn-end", self._make_turn_end_handler(context))

    def _wrap_tool_call_bail(self, context) -> None:
        original_bail = context.events.bail

        def wrapped_bail(event: str, **data: Any) -> Any:
            result = original_bail(event, **data)
            if event == "agent/tool-call":
                status, reason = _tool_call_status(result)
                parent_id = getattr(context, "parent_id", None)
                arguments_detail = _summarize_text(data.get("arguments") or {}, 80)
                detail = _summarize_text(
                    f"🔧 {data.get('name', '')} {arguments_detail}".strip(),
                    80,
                )
                if parent_id:
                    self._notify_subtask_progress(context, "tool-call", detail)
                else:
                    payload = {
                        "session_id": context.id,
                        "name": data.get("name", ""),
                        "arguments": data.get("arguments") or {},
                        "status": status,
                    }
                    if reason:
                        payload["reason"] = reason
                    self._notify("item/toolCall", **payload)
            return result

        context.events.bail = wrapped_bail

    def _notify_subtask_progress(self, context, event: str, detail: str) -> None:
        parent_id = getattr(context, "parent_id", None)
        if not parent_id:
            return
        self._notify(
            "item/subtaskProgress",
            session_id=str(parent_id),
            sub_id=context.id,
            event=event,
            detail=detail,
        )

    def _make_subtask_progress_handler(self, context, event: str, detail: str):
        def _handler(**_: Any) -> None:
            self._notify_subtask_progress(context, event, detail)

        return _handler

    def _feed_state(self, context) -> dict[str, Any]:
        state = self._feed_stats.get(context)
        if state is None:
            state = {
                "started_at": time.perf_counter(),
                "tools_used": [],
                "tool_errors": 0,
                "blocked_count": 0,
                "llm_calls": 0,
            }
            self._feed_stats[context] = state
        return state

    def _make_feed_turn_start_handler(self, context):
        def _handler(**_: Any) -> None:
            self._feed_stats[context] = {
                "started_at": time.perf_counter(),
                "tools_used": [],
                "tool_errors": 0,
                "blocked_count": 0,
                "llm_calls": 0,
            }

        return _handler

    def _make_feed_pre_llm_handler(self, context):
        def _handler(**_: Any) -> None:
            self._feed_state(context)["llm_calls"] += 1

        return _handler

    def _append_eval_feed(self, context, reply: str, trace_id: str | None) -> None:
        try:
            state = self._feed_state(context)
            payload = {
                "session_id": context.id,
                "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "prompt": _truncate_text(
                    _last_user_prompt(getattr(context, "messages", []))
                    or getattr(context, "goal", ""),
                    200,
                ),
                "reply_excerpt": _truncate_text(reply, 200),
                "turns": int(getattr(context, "turn", 0) or 0),
                "tools_used": list(dict.fromkeys(state["tools_used"])),
                "tool_errors": int(state["tool_errors"]),
                "blocked_count": int(state["blocked_count"]),
                "llm_calls": int(state["llm_calls"]),
                "total_tokens": int(getattr(context, "usage", {}).get("total_tokens", 0) or 0),
                "elapsed_s": round(time.perf_counter() - float(state["started_at"]), 3),
                "trace_id": trace_id or "",
            }
            payload["signals"] = {
                "auto_error": bool(
                    payload["tool_errors"] >= 1
                    or payload["blocked_count"] >= 3
                    or payload["turns"] > 8
                ),
                "weak_flag": _contains_weak_signal(reply),
            }
            payload["verdict"] = (
                "error"
                if payload["signals"]["auto_error"]
                else ("flagged" if payload["signals"]["weak_flag"] else "ok")
            )
            feed_path = _eval_feed_path()
            feed_path.parent.mkdir(parents=True, exist_ok=True)
            with feed_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:  # pragma: no cover
            print(f"[eval_feed] 落盘失败（静默）: {exc!r}")

    def _make_final_answer_handler(self, context):
        def _handler(**data: Any) -> None:
            parent_id = getattr(context, "parent_id", None)
            if parent_id:
                detail = _summarize_text(
                    data.get("content")
                    or data.get("text")
                    or data.get("answer")
                    or data.get("message")
                    or "",
                    80,
                )
                if not detail:
                    detail = "✍️ 回答"
                self._notify_subtask_progress(context, "final-answer", detail)
                return
            # 主 context 正常完成：agent 循环正常路径只 emit final-answer
            # （turn-end 事件仅在 stopped/max_turns 发）——这里补发
            # turn/end(completed) 通知：前端"回合完成 + trace_id 标记"依赖它
            # （2026-09-03 消息级跳转——曾误挂 turn-end 事件导致正常对话无标记）
            trace_id = getattr(context.events, "_qi_telemetry_trace_id", None)
            payload: dict[str, Any] = {
                "session_id": context.id,
                "reason": "completed",
            }
            if trace_id:
                payload["trace_id"] = trace_id
            self._notify("turn/end", **payload)
            reply = _summarize_text(
                data.get("content")
                or data.get("text")
                or data.get("answer")
                or data.get("message")
                or "",
                200,
            )
            self._append_eval_feed(context, reply, trace_id)

        return _handler

    def _make_tool_result_handler(self, context):
        def _handler(
            name: str,
            arguments: dict,
            output: str,
            duration: float,
            **_: Any,
        ) -> None:
            parent_id = getattr(context, "parent_id", None)
            if parent_id:
                self._notify_subtask_progress(context, "tool-result", "✓ 完成")
                return
            state = self._feed_state(context)
            outcome = _tool_outcome(str(output))
            if outcome == "blocked":
                state["blocked_count"] += 1
            elif outcome == "error":
                state["tool_errors"] += 1
            if name and name not in state["tools_used"]:
                state["tools_used"].append(name)
            self._notify(
                "item/toolResult",
                session_id=context.id,
                name=name,
                ok=_tool_result_ok(str(output)),
                summary=_truncate_text(str(output), 120),
                duration_ms=int(duration * 1000),
            )

        return _handler

    def _make_turn_end_handler(self, context):
        def _handler(reason: str, error: str | None = None, **_: Any) -> None:
            parent_id = getattr(context, "parent_id", None)
            if parent_id:
                self._notify_subtask_progress(context, "turn-end", "🏁")
                return
            payload = {"session_id": context.id, "reason": reason}
            if error is not None:
                payload["error"] = error
            trace_id = getattr(context.events, "_qi_telemetry_trace_id", None)
            if trace_id:
                payload["trace_id"] = trace_id
            self._notify("turn/end", **payload)

        return _handler

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        for ws, queue in list(self._send_queues.items()):
            if ws not in self._workers:
                self._workers.add(ws)
                loop.create_task(self._send_worker(ws, queue))

    def _make_shell_callback(self) -> Any:
        def _notify(payload: str) -> None:
            loop = self._loop
            if loop is None:
                return
            for conn in list(self.connections):
                loop.call_soon_threadsafe(self._send, conn, payload)

        return _notify

    def _send(self, ws: Any, msg: str) -> None:
        queue = self._send_queues.get(ws)
        if queue is None:
            queue = asyncio.Queue()
            self._send_queues[ws] = queue
            loop = self._loop
            if loop is not None and ws not in self._workers:
                self._workers.add(ws)
                loop.create_task(self._send_worker(ws, queue))
        queue.put_nowait(msg)

    async def _send_worker(self, ws: Any, queue: asyncio.Queue[str]) -> None:
        while True:
            payload = await queue.get()
            try:
                await ws.send(payload)
            except Exception:
                return

    async def handle_connection(self, ws: Any) -> None:
        self.connections.add(ws)
        if self.gateway.shell_callback is None:
            self.gateway.shell_callback = self._make_shell_callback()
        try:
            async for raw in ws:
                try:
                    response = await asyncio.get_running_loop().run_in_executor(
                        None,
                        self.gateway.dispatcher.dispatch,
                        raw,
                    )
                except Exception as exc:
                    response = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32603, "message": str(exc)},
                        }
                    )
                if response:
                    await ws.send(response)
        finally:
            self.connections.discard(ws)

    def _notify(self, method: str, **params: Any) -> None:
        if self.gateway.shell_callback is not None:
            self.gateway.shell_callback(
                json.dumps(
                    {"jsonrpc": "2.0", "method": method, "params": params},
                    ensure_ascii=False,
                )
            )


def run_ws(port: int) -> None:
    import websockets

    gateway = Gateway()
    transport = ServeTransport(gateway)

    async def _handler(ws: Any) -> None:
        await transport.handle_connection(ws)

    async def _main() -> None:
        transport.set_loop(asyncio.get_running_loop())
        async with websockets.serve(_handler, "127.0.0.1", port):
            print(f"[serve] qi-agent 内核 WS 服务: ws://127.0.0.1:{port}")
            print("[serve] 等待客户端连接（Ctrl+C 退出）...")
            await asyncio.Future()

    asyncio.run(_main())


def run_stdio() -> None:
    gateway = Gateway()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = gateway.dispatcher.dispatch(line)
        if response:
            print(response, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="qi-agent 内核 serve")
    parser.add_argument("--port", type=int, default=8765, help="WS 端口")
    parser.add_argument("--stdio", action="store_true", help="使用 stdio 传输")
    args = parser.parse_args()
    if args.stdio:
        run_stdio()
    else:
        run_ws(args.port)


if __name__ == "__main__":
    main()
