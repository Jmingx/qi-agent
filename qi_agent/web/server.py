"""web 模块：FastAPI 应用 + WebSocket 端点 + Bridge（独立进程——只调 serve RPC）。

架构（方案 2026-08-30-WebShell——D6 独立进程）：
  浏览器 ⇄ WS ⇄ web 进程（本模块）⇄ serve RPC（内核进程）⇄ 内核
  → web 进程【只调 serve 的 RPC】（不 import AgentManager/Context——跨进程）

运行：
  python -m qi_agent.web.server --port 9000 --serve ws://127.0.0.1:8765
"""

import argparse
import asyncio
import json
import ipaddress
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

_WEB_TOKEN_PATH = Path.home() / ".qi-agent" / "web_token"


def _load_web_token() -> str:
    """读取或生成 web token。"""
    env_token = os.getenv("QI_WEB_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()

    if _WEB_TOKEN_PATH.exists():
        return _WEB_TOKEN_PATH.read_text(encoding="utf-8").strip()

    token = secrets.token_urlsafe(32)
    _WEB_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WEB_TOKEN_PATH.write_text(token, encoding="utf-8")
    try:
        os.chmod(_WEB_TOKEN_PATH, 0o600)
    except OSError:
        pass
    return token


def _is_loopback_host(host: str | None) -> bool:
    """判断 WebSocket 客户端是否来自本地回环。"""
    if not host:
        return False

    lowered = host.strip().lower()
    if lowered == "localhost":
        return True

    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _extract_ws_token(ws: WebSocket) -> str:
    """从 query 或 header 中提取 token。"""
    query_token = ws.query_params.get("token")
    if query_token:
        return query_token.strip()

    for header_name in ("x-qi-web-token", "token", "authorization"):
        header_value = ws.headers.get(header_name)
        if not header_value:
            continue
        if header_name == "authorization" and header_value.lower().startswith("bearer "):
            return header_value[7:].strip()
        return header_value.strip()

    return ""

# ── Bridge：serve RPC 客户端（每浏览器连接一个——转发到内核 serve）──────

class ServeBridge:
    """Bridge：浏览器 WS ⇄ serve RPC（协议翻译——JSON-RPC 透传）。

    浏览器帧（JSON-RPC 2.0）：请求 {id, method, params} → 转发 serve → 回响应
    serve 通知（流式/审批/结束）→ 转发浏览器

    并发约束：websockets 不允许并发 recv——【单一读循环】（pump 独占
    serve 读取）；call() 通过 pending 表等待响应（写入由 pump 分发）。
    """

    def __init__(self, serve_url: str) -> None:
        self.serve_url = serve_url
        self._client = None  # websockets 客户端（异步——连接 serve）
        self._listeners: list = []  # 浏览器 WS 连接（通知分发）
        self._pending: dict = {}  # id → asyncio.Future（响应等待）
        self._next_id = 1

    async def connect(self) -> None:
        """连 serve（内核进程）。"""
        import websockets
        self._client = await websockets.connect(self.serve_url)

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    async def call(self, method: str, params: dict) -> dict:
        """调用 serve RPC（JSON-RPC 请求 → 响应）。

        注意：不直接 recv（pump 独占读取）——注册 pending 等 pump 分发。
        """
        if self._client is None:
            raise RuntimeError("serve 未连接")
        import asyncio
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method,
               "params": params}
        await self._client.send(json.dumps(msg, ensure_ascii=False))
        data = await asyncio.wait_for(fut, timeout=120)
        if "error" in data:
            raise RuntimeError(f"{data['error']}")
        return data.get("result", {})

    async def pump(self) -> None:
        """serve 通知泵：独占读 serve → 响应分发给 pending / 通知广播浏览器。"""
        while self._client:
            try:
                raw = await self._client.recv()
            except Exception:
                break
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get("id") is not None and data["id"] in self._pending:
                # RPC 响应 → 唤醒 call()
                fut = self._pending.pop(data["id"])
                if not fut.done():
                    fut.set_result(data)
            elif "id" not in data:  # 通知（流式/审批/结束）→ 广播
                for ws in list(self._listeners):
                    try:
                        await ws.send_text(raw)
                    except Exception:
                        self._listeners.remove(ws)


# ── FastAPI 应用 ─────────────────────────────────────────────────────────

def create_app(serve_url: str = "ws://127.0.0.1:8765") -> FastAPI:
    """创建 FastAPI 应用（WS 端点 + 静态前端）。"""
    app = FastAPI(title="qi-agent Web Shell")
    bridge = ServeBridge(serve_url)
    web_token = _load_web_token()
    app.state.web_token = web_token

    @app.on_event("startup")
    async def _startup() -> None:
        await bridge.connect()
        # 通知泵（后台任务——serve → 浏览器广播）
        app.state.pump_task = asyncio.create_task(bridge.pump())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        app.state.pump_task.cancel()
        await bridge.close()

    @app.websocket("/ws")
    async def _ws(ws: WebSocket) -> None:
        client_host = getattr(ws.client, "host", None)
        if not _is_loopback_host(client_host):
            provided_token = _extract_ws_token(ws)
            if not provided_token or provided_token != web_token:
                await ws.close(code=4401, reason="unauthorized")
                return

        await ws.accept()
        bridge._listeners.append(ws)
        try:
            while True:
                raw = await ws.receive_text()
                data = json.loads(raw)
                method = data.get("method")
                params = data.get("params") or {}
                # 浏览器请求 → serve RPC → 响应回浏览器
                result = await bridge.call(method, params)
                await ws.send_text(json.dumps(
                    {"jsonrpc": "2.0", "id": data.get("id"),
                     "result": result}, ensure_ascii=False))
        except WebSocketDisconnect:
            bridge._listeners.remove(ws)
        except Exception as exc:
            try:
                await ws.send_text(json.dumps(
                    {"jsonrpc": "2.0", "id": data.get("id"),
                     "error": {"code": -32603, "message": str(exc)}},
                    ensure_ascii=False))
            except Exception:
                pass

    # 静态前端（React 构建产物——npm run build 后挂载）
    # 绝对路径（相对本模块——不依赖 cwd）
    import os
    _dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
    if os.path.isdir(_dist):
        app.mount("/", StaticFiles(directory=_dist, html=True),
                  name="frontend")

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="qi-agent Web Shell")
    parser.add_argument("--port", type=int, default=9000,
                        help="Web 端口（默认 9000）")
    parser.add_argument("--serve", default="ws://127.0.0.1:8765",
                        help="内核 serve WS 地址（默认 ws://127.0.0.1:8765）")
    args = parser.parse_args()

    app = create_app(args.serve)
    print(f"[web] qi-agent Web Shell: http://127.0.0.1:{args.port}")
    print(f"[web] 内核 serve: {args.serve}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
