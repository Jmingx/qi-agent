"""Phase 0 验证：serve WS 服务端到端（真实 WS + JSON-RPC）。

验证：
  ① serve 启动（WS 端口监听）
  ② 客户端连上 → session/create → 拿到 context_id
  ③ message/send → 内核处理（Fake LLM——不真实调 API）
  ④ 通知桥（shell_callback → WS 通知帧——流式 delta）
"""

import asyncio
import json
import sys

sys.path.insert(0, ".")

import qi_agent.serve as serve_mod
from qi_agent.gateway.gateway import Gateway


class FakeLLM:
    """Fake LLM——返回固定回复（不调真实 API）。"""

    def chat(self, messages, tools=None):
        from qi_agent.llm import ChatResult
        content = "你好！我是测试助手。"
        return ChatResult(content=content, tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": content},
                          usage=None)

    def chat_stream(self, messages, tools=None, on_delta=None):
        from qi_agent.llm import ChatResult
        content = "你好！我是测试助手。"
        if on_delta:
            on_delta(content[:5])
            on_delta(content[5:])
        return ChatResult(content=content, tool_calls=[],
                          assistant_message={"role": "assistant",
                                             "content": content},
                          usage=None)


def _patch_fake_llm():
    """把 make_agent 的 LLM 换成 FakeLLM（不真实调 API）。"""
    import qi_agent.agents.factory as factory

    def fake_make_agent(context, type="standard"):
        from qi_agent.agents.agent import Agent
        return Agent(FakeLLM(), system_prompt="", max_turns=2, context=context)

    factory.make_agent = fake_make_agent


def main() -> int:
    _patch_fake_llm()

    # 起 serve（独立线程——真实 WS 端口）
    gateway = Gateway()
    transport = serve_mod.ServeTransport(gateway)

    import websockets
    port = 8877
    results: dict = {}

    async def _client_test() -> None:
        uri = f"ws://127.0.0.1:{port}"
        async with websockets.connect(uri) as ws:
            # ① session/create
            await ws.send(json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "session/create",
                 "params": {"goal": "测试会话"}}))
            resp = json.loads(await ws.recv())
            sid = resp.get("result", {}).get("session_id")
            results["session_create"] = bool(sid)
            results["session_id"] = sid
            print(f"① session/create → {sid}")

            # ② message/send（Fake LLM 回复）
            await ws.send(json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "message/send",
                 "params": {"session_id": sid, "text": "你好"}}))
            # 可能收到通知帧（delta）或响应帧
            while True:
                frame = json.loads(await ws.recv())
                if frame.get("id") == 2:
                    results["message_send"] = bool(
                        frame.get("result", {}).get("reply"))
                    print(f"② message/send → {frame.get('result')}")
                    break
                elif frame.get("method") == "item/agentMessage/delta":
                    results["stream_delta"] = True
                    print(f"   流式 delta: {frame['params']}")

    async def _server() -> None:
        async def _h(ws):
            await transport.handle_connection(ws)

        async with websockets.serve(_h, "127.0.0.1", port):
            await asyncio.sleep(0.2)  # 等 server 起
            await _client_test()
            # 关闭 server
            raise SystemExit

    try:
        asyncio.run(_server())
    except SystemExit:
        pass

    print("\n=== 结果 ===")
    ok = True
    for k, v in results.items():
        status = "✓" if v else "✗"
        if not v:
            ok = False
        print(f"  {status} {k}: {v}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
