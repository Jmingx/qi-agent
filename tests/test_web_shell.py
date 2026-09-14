"""serve + web 模块测试（方案 2026-08-30-WebShell——独立进程架构）。

验证：
  ① ServeTransport：WS 传输层（dispatch 转发——session/create）
  ② ServeBridge：web ⇄ serve RPC（call 转发——不并发 recv）
  ③ web FastAPI：/ws 端点端到端（浏览器路径 → serve → 内核）
"""

import asyncio
import json
import sys
import threading
import time

sys.path.insert(0, ".")

import pytest

# websockets 是运行时依赖（serve/web 都需要）
websockets = pytest.importorskip("websockets")


class FakeLLM:
    """Fake LLM——固定回复（测试不调真实 API）。"""

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult
        return ChatResult(content="测试回复", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "测试回复"},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        from qi_agent.llm import ChatResult
        if on_delta:
            on_delta("测试回复")
        return ChatResult(content="测试回复", tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": "测试回复"},
                          usage=None)


@pytest.fixture(scope="module")
def fake_llm_env():
    """patch factory.make_agent → FakeLLM（不真实调 API）。"""
    import qi_agent.agents.factory as factory

    def fake_make_agent(context, type="standard"):
        from qi_agent.agents.agent import Agent
        return Agent(FakeLLM(), system_prompt="", max_turns=2,
                     context=context)

    orig = factory.make_agent
    factory.make_agent = fake_make_agent
    yield
    factory.make_agent = orig


def _run_async(coro):
    return asyncio.run(coro)


def test_serve_transport_session_create(fake_llm_env):
    """ServeTransport：WS 层 dispatch（session/create）。"""
    from qi_agent.gateway.gateway import Gateway
    from qi_agent.serve import ServeTransport

    gateway = Gateway()
    transport = ServeTransport(gateway)
    port = 9101

    async def _test():
        async def _h(ws):
            await transport.handle_connection(ws)

        async with websockets.serve(_h, "127.0.0.1", port):
            await asyncio.sleep(0.1)
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "session/create",
                     "params": {"goal": "t"}}))
                resp = json.loads(await ws.recv())
                return resp["result"]["session_id"]

    sid = _run_async(_test())
    assert sid.startswith("ctx_"), f"session_id 异常: {sid}"


def test_serve_transport_message_send(fake_llm_env):
    """ServeTransport：message/send（Fake LLM 回复）。"""
    from qi_agent.gateway.gateway import Gateway
    from qi_agent.serve import ServeTransport

    gateway = Gateway()
    transport = ServeTransport(gateway)
    port = 9102

    async def _test():
        async def _h(ws):
            await transport.handle_connection(ws)

        async with websockets.serve(_h, "127.0.0.1", port):
            await asyncio.sleep(0.1)
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "session/create",
                     "params": {"goal": "t"}}))
                sid = json.loads(await ws.recv())["result"]["session_id"]
                await ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": 2, "method": "message/send",
                     "params": {"session_id": sid, "text": "hi"}}))
                while True:
                    frame = json.loads(await ws.recv())
                    if frame.get("id") == 2:
                        return frame["result"]["reply"]

    reply = _run_async(_test())
    assert reply == "测试回复"


def test_serve_bridge_call(fake_llm_env):
    """ServeBridge：call 转发（web ⇄ serve RPC——不并发 recv）。"""
    from qi_agent.gateway.gateway import Gateway
    from qi_agent.serve import ServeTransport
    from qi_agent.web.server import ServeBridge

    gateway = Gateway()
    transport = ServeTransport(gateway)
    port = 9103

    async def _test():
        async def _h(ws):
            await transport.handle_connection(ws)

        async with websockets.serve(_h, "127.0.0.1", port):
            await asyncio.sleep(0.1)
            bridge = ServeBridge(f"ws://127.0.0.1:{port}")
            await bridge.connect()
            # pump 独占读（后台任务）
            pump_task = asyncio.create_task(bridge.pump())
            try:
                r = await bridge.call("session/create", {"goal": "t"})
                return r.get("session_id", "")
            finally:
                pump_task.cancel()
                await bridge.close()

    sid = _run_async(_test())
    assert sid.startswith("ctx_")


def test_web_ws_handles_approval_while_message_is_pending(monkeypatch):
    """长消息尚未返回时，同一浏览器连接仍能回传审批响应。"""
    from fastapi.testclient import TestClient
    from qi_agent.web import server

    started = threading.Event()
    approved = threading.Event()

    async def fake_connect(self) -> None:
        self._client = object()

    async def fake_close(self) -> None:
        return None

    async def fake_pump(self) -> None:
        await asyncio.Event().wait()

    async def fake_call(self, method: str, params: dict) -> dict:
        if method == "message/send":
            started.set()
            assert await asyncio.to_thread(approved.wait, 1)
            return {"reply": "审批后的回复"}
        if method == "approval/respond":
            approved.set()
            return {"ok": True}
        raise AssertionError(f"意外 RPC: {method} {params}")

    monkeypatch.setattr(server.ServeBridge, "connect", fake_connect)
    monkeypatch.setattr(server.ServeBridge, "close", fake_close)
    monkeypatch.setattr(server.ServeBridge, "pump", fake_pump)
    monkeypatch.setattr(server.ServeBridge, "call", fake_call)

    app = server.create_app()
    with TestClient(app) as client:
        token = app.state.web_token
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_json({
                "jsonrpc": "2.0", "id": 1, "method": "message/send",
                "params": {"session_id": "ctx_1", "text": "执行需要审批的操作"},
            })
            assert started.wait(1), "message/send 没有进入后台处理"
            ws.send_json({
                "jsonrpc": "2.0", "id": 2, "method": "approval/respond",
                "params": {"session_id": "ctx_1", "approval_id": "ap_1", "decision": "approve"},
            })
            # 两个后台任务可按任意顺序完成，按 id 收齐即可。
            first = ws.receive_json()
            second = ws.receive_json()

    by_id = {first["id"]: first, second["id"]: second}
    assert by_id[2]["result"] == {"ok": True}
    assert by_id[1]["result"] == {"reply": "审批后的回复"}


def test_serve_control_frame_is_not_blocked_by_long_rpc(monkeypatch) -> None:
    """控制面即时性（2026-09-14 修复）：长 RPC 在飞时，同连接控制帧仍被处理。

    修复前 `handle_connection` 串行 `await` 派发——`message/send` 等待审批期间，
    同一连接上的 `approval/respond` 排在同一循环后面读不到 → 等待环，
    Web 审批永远走到 60s 超时（实机验出）。修复后每帧独立任务派发。
    """
    import qi_agent.agents.factory as factory
    from qi_agent.agents.agent import Agent
    from qi_agent.gateway.gateway import Gateway
    from qi_agent.serve import ServeTransport

    class SlowLLM(FakeLLM):
        """慢模型：让 message/send 占用 ~1.5s，模拟长回合。"""

        def chat(self, messages, tools=None):
            time.sleep(1.5)
            return super().chat(messages, tools)

    def fake_make_agent(context, type: str = "standard"):
        return Agent(SlowLLM(), system_prompt="", max_turns=2, context=context)

    monkeypatch.setattr(factory, "make_agent", fake_make_agent)

    gateway = Gateway()
    transport = ServeTransport(gateway)
    port = 9105

    async def _test() -> float:
        async def _handler(ws) -> None:
            await transport.handle_connection(ws)

        async with websockets.serve(_handler, "127.0.0.1", port):
            await asyncio.sleep(0.1)
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "session/create",
                     "params": {"goal": "t"}}))
                sid = None
                while sid is None:
                    frame = json.loads(await ws.recv())
                    if frame.get("id") == 1:
                        sid = frame["result"]["session_id"]
                # 长 RPC 在飞 + 紧跟一条控制帧：控制帧必须立刻回来
                await ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": 2, "method": "message/send",
                     "params": {"session_id": sid, "text": "hi"}}))
                started = time.monotonic()
                await ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": 3, "method": "session/list",
                     "params": {}}))
                while True:
                    frame = json.loads(await ws.recv())
                    if frame.get("id") == 3:
                        return time.monotonic() - started

    elapsed = asyncio.run(_test())
    assert elapsed < 1.0, f"控制帧被长 RPC 阻塞：{elapsed:.2f}s（等待环回归）"
