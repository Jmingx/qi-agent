"""qi-agent web 应用：WebSocket 桥接、静态前端和 Jaeger 反代。"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import secrets
from pathlib import Path
from types import SimpleNamespace
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

from fastapi import (
    FastAPI,
    HTTPException,
    Request as FastAPIRequest,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

try:
    import httpx  # type: ignore[import-not-found]
except ModuleNotFoundError:
    class HTTPError(Exception):
        """轻量级 httpx 兼容异常。"""


    class Timeout:
        """轻量级 httpx Timeout 兼容对象。"""

        def __init__(self, timeout: float | None) -> None:
            self.timeout = timeout


    class ProxyRequest:
        """轻量级 httpx Request 兼容对象。"""

        def __init__(self, method: str, url: str, headers: dict[str, str], content: object) -> None:
            self.method = method
            self.url = url
            self.headers = headers
            self._content = content

        async def aread(self) -> bytes:
            content = self._content
            if content is None:
                return b""
            if isinstance(content, bytes):
                return content
            if isinstance(content, bytearray):
                return bytes(content)
            if isinstance(content, str):
                return content.encode()
            if hasattr(content, "__aiter__"):
                chunks: list[bytes] = []
                async for chunk in content:
                    if chunk:
                        chunks.append(bytes(chunk))
                return b"".join(chunks)
            if hasattr(content, "__iter__"):
                return b"".join(bytes(chunk) for chunk in content if chunk)
            return bytes(content)


    class Response:
        """轻量级 httpx Response 兼容对象。"""

        def __init__(self, status_code: int, headers: dict[str, str], raw_response: object) -> None:
            self.status_code = status_code
            self.headers = headers
            self._raw_response = raw_response

        async def aiter_raw(self):
            while True:
                chunk = await asyncio.to_thread(self._raw_response.read, 65536)
                if not chunk:
                    break
                yield chunk

        async def aclose(self) -> None:
            await asyncio.to_thread(self._raw_response.close)


    class AsyncClient:
        """轻量级 httpx.AsyncClient 兼容对象。"""

        def __init__(self, timeout: Timeout | None = None, trust_env: bool = False) -> None:
            self.timeout = timeout
            self.trust_env = trust_env

        def build_request(
            self,
            method: str,
            url: str,
            headers: dict[str, str] | None = None,
            content: object = None,
        ) -> ProxyRequest:
            request_headers = dict(headers or {})
            parsed = urlsplit(url)
            if parsed.netloc:
                request_headers.setdefault("host", parsed.netloc)
            return ProxyRequest(method, url, request_headers, content)

        async def send(self, request: ProxyRequest, stream: bool = False) -> Response:
            body = await request.aread()
            data = body if body else None
            req = urllib_request.Request(
                request.url,
                data=data,
                headers=dict(request.headers),
                method=request.method,
            )
            opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
            timeout = getattr(self.timeout, "timeout", None)
            try:
                raw_response = await asyncio.to_thread(opener.open, req, timeout)
            except urllib_error.URLError as exc:
                raise HTTPError(str(exc)) from exc
            headers = {key: value for key, value in raw_response.headers.items()}
            return Response(raw_response.status, headers, raw_response)

        async def aclose(self) -> None:
            return None


    httpx = SimpleNamespace(
        AsyncClient=AsyncClient,
        HTTPError=HTTPError,
        Timeout=Timeout,
        Request=ProxyRequest,
        Response=Response,
    )

_WEB_TOKEN_PATH = Path.home() / ".qi-agent" / "web_token"
_JAEGER_PROXY_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
_JAEGER_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


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


def _load_jaeger_base_url() -> str:
    """读取 Jaeger 反代目标，默认走本机 base-path。"""
    # 需要配合 JAEGER_QUERY_BASE_PATH=/jaeger 启动，这样 UI 内部资源会走相对路径。
    base_url = os.getenv("QI_JAEGER_BASE_URL", "http://127.0.0.1:16686/jaeger").strip()
    return base_url.rstrip("/") or "http://127.0.0.1:16686/jaeger"


def _build_jaeger_upstream_url(base_url: str, path: str, query: str) -> str:
    """把 web 侧 /jaeger 路径映射到 Jaeger 的 base-path。"""
    upstream_path = path.lstrip("/")
    upstream_url = f"{base_url}/{upstream_path}" if upstream_path else f"{base_url}/"
    return f"{upstream_url}?{query}" if query else upstream_url


def _filter_proxy_headers(items: list[tuple[str, str]]) -> dict[str, str]:
    """只保留可安全透传的头，避免 hop-by-hop 头污染代理链路。"""
    return {
        name: value
        for name, value in items
        if name.lower() not in _JAEGER_HOP_HEADERS
    }


async def _close_proxy_response(
    client: httpx.AsyncClient,
    response: httpx.Response,
) -> None:
    """响应结束后统一回收 upstream 连接。"""
    await response.aclose()
    await client.aclose()


async def _proxy_jaeger_request(
    request: FastAPIRequest,
    path: str,
    base_url: str,
) -> StreamingResponse:
    """把浏览器请求流式转发到 Jaeger。"""
    upstream_url = _build_jaeger_upstream_url(base_url, path, request.url.query)
    client = httpx.AsyncClient(timeout=httpx.Timeout(None), trust_env=False)
    upstream_request = client.build_request(
        request.method,
        upstream_url,
        headers=_filter_proxy_headers(list(request.headers.items())),
        content=request.stream(),
    )
    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Jaeger upstream unavailable") from exc

    return StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=_filter_proxy_headers(list(upstream_response.headers.items())),
        background=BackgroundTask(_close_proxy_response, client, upstream_response),
    )


class ServeBridge:
    """浏览器 WS <-> core serve RPC bridge。"""

    def __init__(self, serve_url: str) -> None:
        self.serve_url = serve_url
        self._client = None
        self._listeners: list[_WebSocketListener] = []
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._request_lock = asyncio.Lock()

    async def connect(self) -> None:
        """连接内核 serve。"""
        import websockets

        self._client = await websockets.connect(self.serve_url)

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    async def call(self, method: str, params: dict) -> dict:
        """调用 serve RPC。"""
        if self._client is None:
            raise RuntimeError("serve 未连接")

        # 多个浏览器请求可以并发等待内核响应，但分配 id 和发送帧必须串行，
        # 避免同一条 serve WebSocket 上出现帧/待响应表的竞态。
        async with self._request_lock:
            msg_id = self._next_id
            self._next_id += 1
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[msg_id] = fut
            msg = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
            await self._client.send(json.dumps(msg, ensure_ascii=False))
        try:
            data = await asyncio.wait_for(fut, timeout=120)
        finally:
            # 浏览器断开导致外层任务取消时，不能遗留永远不可达的 Future。
            self._pending.pop(msg_id, None)
        if "error" in data:
            raise RuntimeError(f"{data['error']}")
        return data.get("result", {})

    async def pump(self) -> None:
        """独占读取 serve 通知，并广播给所有浏览器连接。"""
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
                fut = self._pending.pop(data["id"])
                if not fut.done():
                    fut.set_result(data)
            elif "id" not in data:
                for listener in list(self._listeners):
                    try:
                        await listener.send_text(raw)
                    except Exception:
                        if listener in self._listeners:
                            self._listeners.remove(listener)


class _WebSocketListener:
    """单个浏览器连接的串行发送器。

    内核通知由 ``ServeBridge.pump`` 发出，RPC 响应由连接内的后台任务发出。
    两者必须共用同一把锁，不能依赖 ASGI 实现在并发 ``send_text`` 时的偶然行为。
    """

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._send_lock = asyncio.Lock()

    async def send_text(self, raw: str) -> None:
        async with self._send_lock:
            await self.ws.send_text(raw)


def create_app(serve_url: str = "ws://127.0.0.1:8765") -> FastAPI:
    """创建 FastAPI 应用。"""
    app = FastAPI(title="qi-agent Web Shell")
    bridge = ServeBridge(serve_url)
    app.state.bridge = bridge
    web_token = _load_web_token()
    jaeger_base_url = _load_jaeger_base_url()
    app.state.web_token = web_token

    @app.on_event("startup")
    async def _startup() -> None:
        await bridge.connect()
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
        listener = _WebSocketListener(ws)
        bridge._listeners.append(listener)
        request_tasks: set[asyncio.Task[None]] = set()

        async def _respond(data: dict) -> None:
            request_id = data.get("id")
            try:
                method = data.get("method")
                if not isinstance(method, str) or not method:
                    raise ValueError("RPC method 不能为空")
                params = data.get("params") or {}
                if not isinstance(params, dict):
                    raise ValueError("RPC params 必须是对象")
                result = await bridge.call(method, params)
                payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                payload = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": str(exc)},
                }
            try:
                await listener.send_text(json.dumps(payload, ensure_ascii=False))
            except Exception:
                # 连接已断开时无需再向浏览器报告；内核审批仍会按超时拒绝。
                return

        def _track(task: asyncio.Task[None]) -> None:
            request_tasks.discard(task)

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await listener.send_text(json.dumps({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "请求不是合法 JSON"},
                    }, ensure_ascii=False))
                    continue
                if not isinstance(data, dict):
                    await listener.send_text(json.dumps({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "请求必须是 JSON 对象"},
                    }, ensure_ascii=False))
                    continue
                # 不在接收循环 await：长运行的 Agent 回合期间，审批/停止等控制
                # 请求仍必须立刻被接收并转发，否则会形成“等待审批却收不到审批”的环。
                task = asyncio.create_task(_respond(data))
                request_tasks.add(task)
                task.add_done_callback(_track)
        except WebSocketDisconnect:
            pass
        finally:
            if listener in bridge._listeners:
                bridge._listeners.remove(listener)
            for task in request_tasks:
                task.cancel()
            if request_tasks:
                await asyncio.gather(*request_tasks, return_exceptions=True)

    @app.api_route("/jaeger", methods=_JAEGER_PROXY_METHODS)
    @app.api_route("/jaeger/{path:path}", methods=_JAEGER_PROXY_METHODS)
    async def _jaeger_proxy(request: FastAPIRequest, path: str = "") -> StreamingResponse:
        # 路由必须在 StaticFiles 之前注册，否则 /jaeger 会被前端静态站点吞掉。
        return await _proxy_jaeger_request(request, path, jaeger_base_url)

    _dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
    if os.path.isdir(_dist):
        app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="qi-agent Web Shell")
    parser.add_argument("--port", type=int, default=9000, help="Web 端口，默认 9000")
    parser.add_argument("--serve", default="ws://127.0.0.1:8765", help="内核 serve WS 地址，默认 ws://127.0.0.1:8765")
    args = parser.parse_args()

    app = create_app(args.serve)
    print(f"[web] qi-agent Web Shell: http://127.0.0.1:{args.port}")
    print(f"[web] 内核 serve: {args.serve}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
