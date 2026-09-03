"""qi-agent serve 的最小 WS JSON-RPC 客户端。"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import websockets


REPO_ROOT = Path(__file__).resolve().parents[2]
SAFE_APPROVAL_COMMANDS = {"get_time", "list_dir", "read_file"}


def _socket_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def start_serve_if_needed(host: str, port: int) -> subprocess.Popen[str] | None:
    """若 serve 未运行，则在后台启动它。"""

    if _socket_ready(host, port):
        return None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "qi_agent.serve", "--port", str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        text=True,
    )
    deadline = time.time() + 180.0
    while time.time() < deadline:
        if _socket_ready(host, port):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"qi-agent serve 已退出，返回码: {proc.returncode}")
        time.sleep(1.0)
    proc.terminate()
    raise TimeoutError(f"等待 qi-agent serve 超时：ws://{host}:{port}")


class WsRpcClient:
    """极简 JSON-RPC WS 客户端，只保留本次 POC 需要的能力。"""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._next_id = 1
        self.tool_calls: list[str] = []
        self._delta_parts: list[str] = []

    async def __aenter__(self) -> "WsRpcClient":
        self._ws = await websockets.connect(self.uri)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("WS 尚未连接")
        request_id = self._next_id
        self._next_id += 1
        await self._ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                },
                ensure_ascii=False,
            )
        )
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
        decision = "approve" if command in SAFE_APPROVAL_COMMANDS else "deny"
        await self._ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": self._next_id,
                    "method": "approval/respond",
                    "params": {
                        "session_id": session_id,
                        "approval_id": approval_id,
                        "decision": decision,
                    },
                },
                ensure_ascii=False,
            )
        )
        self._next_id += 1

    def collected_delta(self) -> str:
        return "".join(self._delta_parts)
