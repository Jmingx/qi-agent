"""内核 serve 入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from qi_agent.gateway.gateway import Gateway
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
        self._attached_contexts: set[str] = set()
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
        if context.id in self._attached_contexts:
            return
        self._attached_contexts.add(context.id)
        self._wrap_tool_call_bail(context)
        context.events.on("agent/tool-result", self._make_tool_result_handler(context))
        context.events.on("agent/turn-end", self._make_turn_end_handler(context))

    def _wrap_tool_call_bail(self, context) -> None:
        original_bail = context.events.bail

        def wrapped_bail(event: str, **data: Any) -> Any:
            result = original_bail(event, **data)
            if event == "agent/tool-call":
                status, reason = _tool_call_status(result)
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

    def _make_tool_result_handler(self, context):
        def _handler(
            name: str,
            arguments: dict,
            output: str,
            duration: float,
            **_: Any,
        ) -> None:
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
            payload = {"session_id": context.id, "reason": reason}
            if error is not None:
                payload["error"] = error
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
