"""A transport/parse failure must not silently repeat an AgentService action."""
import asyncio
import json

import httpx
import pytest

from ccfoundry_agent_kit import AgentClient, AgentManifest, ChatRequest, ChatResponse, create_agent_app
from ccfoundry_agent_kit import client as client_module


def use_transport(monkeypatch, transport):
    real = httpx.AsyncClient
    def create(*args, **kwargs):
        return real(*args, **kwargs, transport=transport)
    monkeypatch.setattr(client_module.httpx, "AsyncClient", create)


async def collect(client, payload=None):
    return [item async for item in client.stream_foundry_chat(payload or {"message": "act once"})]


@pytest.mark.parametrize("failure", [401, 403, 408, 429, 500, 502, "connect", "timeout", "malformed-json", "broken-stream"])
def test_errors_never_fall_back_to_a_second_action(monkeypatch, failure):
    seen = []
    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: token\ndata: {"content":"partial"}\n\n'
            raise httpx.ReadError("response interrupted")
    def handler(request):
        seen.append(request.url.path)
        assert request.headers["authorization"] == "Bearer agent-secret"
        if failure == "connect":
            raise httpx.ConnectError("unreachable", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("acted but timed out", request=request)
        if failure == "malformed-json":
            return httpx.Response(200, text="not JSON")
        if failure == "broken-stream":
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=BrokenBody())
        return httpx.Response(failure, json={"detail": "failed after dispatch"})
    use_transport(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises((httpx.HTTPError, json.JSONDecodeError)):
        asyncio.run(collect(AgentClient("https://agent.test", agent_secret="agent-secret")))
    assert seen == ["/api/chat"]


@pytest.mark.parametrize("status", [404, 405])
def test_only_unsupported_endpoint_uses_compatibility_fallback(monkeypatch, status):
    seen, actions = [], []
    def handler(request):
        seen.append(request.url.path)
        assert request.headers["authorization"] == "Bearer agent-secret"
        assert json.loads(request.content)["stream"] is True
        if request.url.path == "/api/chat":
            return httpx.Response(status)
        actions.append(True)
        return httpx.Response(200, json={"reply": "ok"})
    use_transport(monkeypatch, httpx.MockTransport(handler))
    payload = {"message": "act", "stream": False}
    events = asyncio.run(collect(AgentClient("https://agent.test", agent_secret="agent-secret"), payload))
    assert seen == ["/api/chat", "/chat"] and len(actions) == 1
    assert payload["stream"] is False
    assert events == [("message", {"reply": "ok"}), ("done", {})]


def test_authenticated_client_against_real_sdk_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "agent-secret")
    calls = []
    async def handler(request, space):
        calls.append(request.message)
        return ChatResponse(reply="done", model="model", usage={"total_tokens": 4})
    app = create_agent_app(manifest=AgentManifest(name="test", label="Test"), chat_handler=handler, agent_space_dir=tmp_path)
    use_transport(monkeypatch, httpx.ASGITransport(app=app))
    async def exercise():
        client = AgentClient("https://agent.test", agent_secret="agent-secret")
        assert (await client.health()).status == "ok"
        assert (await client.manifest()).name == "test"
        assert (await client.chat(ChatRequest(message="sync"))).reply == "done"
        await client.workspace_write("test.txt", "saved")
        assert (await client.workspace_read("test.txt"))["content"] == "saved"
        assert await client.workspace_tree()
        assert await client.reflections() == []
        events = await collect(client, {"message": "stream"})
        assert sum(event == "message" for event, _ in events) == 1
        assert events[-1] == ("done", {})
    asyncio.run(exercise())
    assert calls == ["sync", "stream"]
