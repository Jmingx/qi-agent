from __future__ import annotations

import json

from fastapi.testclient import TestClient


class _FakeUpstreamResponse:
    def __init__(self, body: bytes, headers: dict[str, str], status_code: int = 200) -> None:
        self._body = body
        self.headers = headers
        self.status_code = status_code

    async def aiter_raw(self):
        yield self._body

    async def aclose(self) -> None:
        return None


def test_jaeger_proxy_streams_root(monkeypatch) -> None:
    from qi_agent.web import server

    monkeypatch.setenv("QI_JAEGER_BASE_URL", "http://example.test:16686/jaeger")

    async def fake_connect(self) -> None:
        return None

    async def fake_close(self) -> None:
        return None

    monkeypatch.setattr(server.ServeBridge, "connect", fake_connect)
    monkeypatch.setattr(server.ServeBridge, "close", fake_close)

    captured: list[dict[str, object]] = []

    async def fake_send(self, request, stream=False):
        body = await request.aread()
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
                "stream": stream,
            }
        )
        return _FakeUpstreamResponse(b"root-body", {"content-type": "text/plain", "x-jaeger": "ok"})

    async def fake_aclose(self) -> None:
        return None

    monkeypatch.setattr(server.httpx.AsyncClient, "send", fake_send)
    monkeypatch.setattr(server.httpx.AsyncClient, "aclose", fake_aclose)

    app = server.create_app()
    with TestClient(app) as client:
        response = client.get("/jaeger", headers={"x-trace-id": "abc"})

    assert response.status_code == 200
    assert response.text == "root-body"
    assert response.headers["x-jaeger"] == "ok"
    assert len(captured) == 1

    request = captured[0]
    headers = request["headers"]
    assert request["method"] == "GET"
    assert request["url"] == "http://example.test:16686/jaeger/"
    assert request["body"] == b""
    assert request["stream"] is True
    assert headers["host"] == "example.test:16686"
    assert headers["x-trace-id"] == "abc"


def test_jaeger_proxy_forwards_subpath_and_body(monkeypatch) -> None:
    from qi_agent.web import server

    monkeypatch.setenv("QI_JAEGER_BASE_URL", "http://example.test:16686/jaeger")

    async def fake_connect(self) -> None:
        return None

    async def fake_close(self) -> None:
        return None

    monkeypatch.setattr(server.ServeBridge, "connect", fake_connect)
    monkeypatch.setattr(server.ServeBridge, "close", fake_close)

    captured: list[dict[str, object]] = []

    async def fake_send(self, request, stream=False):
        body = await request.aread()
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
                "stream": stream,
            }
        )
        return _FakeUpstreamResponse(b'[{"trace":true}]', {"content-type": "application/json"})

    async def fake_aclose(self) -> None:
        return None

    monkeypatch.setattr(server.httpx.AsyncClient, "send", fake_send)
    monkeypatch.setattr(server.httpx.AsyncClient, "aclose", fake_aclose)

    app = server.create_app()
    with TestClient(app) as client:
        response = client.post(
            "/jaeger/trace/abc123?pretty=true",
            content=json.dumps({"limit": 1}),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json() == [{"trace": True}]
    assert len(captured) == 1

    request = captured[0]
    headers = request["headers"]
    assert request["method"] == "POST"
    assert request["url"] == "http://example.test:16686/jaeger/trace/abc123?pretty=true"
    assert request["body"] == b'{"limit": 1}'
    assert request["stream"] is True
    assert headers["host"] == "example.test:16686"
    assert headers["content-type"] == "application/json"
