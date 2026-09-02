"""serve + web 模块测试（方案 2026-08-30-WebShell——独立进程架构）。

验证：
  ① ServeTransport：WS 传输层（dispatch 转发——session/create）
  ② ServeBridge：web ⇄ serve RPC（call 转发——不并发 recv）
  ③ web FastAPI：/ws 端点端到端（浏览器路径 → serve → 内核）
"""

import asyncio
import json
import sys

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
