"""正式测评使用的 Gateway WebSocket 客户端。"""

from __future__ import annotations

import json
from typing import Any

import websockets


class GatewayRpcClient:
    """保持通知、审批和工具调用证据的最小 JSON-RPC 客户端。"""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._next_id = 1
        self.tool_calls: list[str] = []
        self.tool_call_details: list[dict[str, Any]] = []
        self._delta_parts: list[str] = []

    async def __aenter__(self) -> "GatewayRpcClient":
        self._ws = await websockets.connect(self.uri)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Gateway WebSocket 尚未连接")
        request_id = self._next_id
        self._next_id += 1
        await self._ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }, ensure_ascii=False))
        while True:
            payload = json.loads(await self._ws.recv())
            if payload.get("id") == request_id:
                if "error" in payload:
                    raise RuntimeError(str(payload["error"]))
                result = payload.get("result") or {}
                return result if isinstance(result, dict) else {"result": result}
            await self._handle_notification(payload)

    async def _handle_notification(self, payload: dict[str, Any]) -> None:
        method = str(payload.get("method") or "")
        params = payload.get("params") or {}
        if method == "item/toolCall":
            name = str(params.get("name") or "")
            if name:
                self.tool_calls.append(name)
                self.tool_call_details.append({
                    "name": name,
                    "arguments": params.get("arguments") or {},
                    "status": params.get("status"),
                    "reason": params.get("reason"),
                })
            return
        if method == "item/agentMessage/delta":
            text = str(params.get("text") or "")
            if text:
                self._delta_parts.append(text)
            return
        if method == "serverRequest/approval":
            await self._respond_to_approval(params)

    async def _respond_to_approval(self, params: dict[str, Any]) -> None:
        if self._ws is None:
            return
        approval_id = str(params.get("approval_id") or "")
        command = str(params.get("command") or "")
        session_id = str(params.get("session_id") or "")
        safe = {"get_time", "list_dir", "read_file"}
        decision = "approve" if command in safe else "deny"
        await self._ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "approval/respond",
            "params": {
                "session_id": session_id,
                "approval_id": approval_id,
                "decision": decision,
            },
        }, ensure_ascii=False))
        self._next_id += 1

    def collected_delta(self) -> str:
        return "".join(self._delta_parts)
