import asyncio
import json

import websockets

from qi_agent.web.server import ServeBridge


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.sent.append(data)
        await self.incoming.put(
            json.dumps({"jsonrpc": "2.0", "id": data["id"], "result": {"ok": True}}),
        )

    async def recv(self) -> str:
        payload = await self.incoming.get()
        if payload is None:
            raise RuntimeError("closed")
        return payload

    async def close(self) -> None:
        await self.incoming.put(None)


def test_bridge_passes_through_new_rpc(monkeypatch) -> None:
    fake_client = FakeClient()

    async def fake_connect(url: str) -> FakeClient:
        return fake_client

    monkeypatch.setattr(websockets, "connect", fake_connect)

    async def scenario() -> None:
        bridge = ServeBridge("ws://example")
        await bridge.connect()
        pump_task = asyncio.create_task(bridge.pump())
        result = await bridge.call("session/delete", {"session_id": "ctx-1"})
        assert result == {"ok": True}
        assert fake_client.sent[0]["method"] == "session/delete"
        assert fake_client.sent[0]["params"] == {"session_id": "ctx-1"}
        await bridge.close()
        await asyncio.wait_for(pump_task, timeout=1)

    asyncio.run(scenario())
