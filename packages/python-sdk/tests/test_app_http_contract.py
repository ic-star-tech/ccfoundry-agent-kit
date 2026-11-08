"""AgentService auth and streaming/JSON contract, with observable side effects."""
import importlib.util
import json
from pathlib import Path
import sys
import types

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from ccfoundry_agent_kit import AgentManifest, ChatResponse, FoundryBootstrap, FoundryBootstrapConfig, create_agent_app


@pytest.fixture(autouse=True)
def no_global_secret(monkeypatch):
    monkeypatch.delenv("AGENT_SECRET", raising=False)


def make_app(tmp_path, **kwargs):
    calls = []
    async def handler(request, space):
        calls.append(request)
        return ChatResponse(reply="收到：" + request.message, notes_update="executed once",
                            model="test-model", usage={"total_tokens": 7})
    app = create_agent_app(manifest=AgentManifest(name="test_agent", label="Test"),
                           chat_handler=handler, agent_space_dir=tmp_path, **kwargs)
    return app, calls


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "test-secret"},
                                      {"Authorization": "Basic test-secret"}, {"Authorization": "Bearer "},
                                      [("Authorization", "Bearer test-secret"), ("Authorization", "Bearer wrong")]])
@pytest.mark.parametrize("endpoint", ["/chat", "/api/chat"])
def test_unauthorized_chat_never_executes(tmp_path, monkeypatch, headers, endpoint):
    monkeypatch.setenv("AGENT_SECRET", "test-secret")
    app, calls = make_app(tmp_path)
    response = TestClient(app).post(endpoint, json={"message": "write something"}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Unauthorized"}
    assert not calls and not (tmp_path / "agent_space/notes.md").exists()


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/workspace/read?path=secret.txt"), ("GET", "/api/workspace/tree"),
    ("PUT", "/api/workspace/write"), ("POST", "/api/workspace/upload"),
    ("DELETE", "/api/workspace/delete?path=secret.txt"), ("POST", "/api/workspace/rename"),
    ("POST", "/api/workspace/copy"), ("POST", "/api/workspace/move"),
    ("GET", "/api/reflections"), ("POST", "/foundry/poll"),
])
def test_auth_runs_before_workspace_or_payload_processing(tmp_path, monkeypatch, method, path):
    monkeypatch.setenv("AGENT_SECRET", "test-secret")
    app, _ = make_app(tmp_path)
    secret_file = tmp_path / "workspace/secret.txt"
    secret_file.write_text("private-data")
    response = TestClient(app).request(method, path, content=b"invalid JSON")
    assert response.status_code == 401
    assert "private-data" not in response.text
    assert secret_file.read_text() == "private-data"


@pytest.mark.parametrize("stream", [None, False, True])
def test_api_chat_honors_mode_and_executes_once(tmp_path, monkeypatch, stream):
    monkeypatch.setenv("AGENT_SECRET", "test-secret")
    app, calls = make_app(tmp_path)
    payload = {"content": "hello", "session_id": "s1", "user_id": "u1", "inline_context": {"mode": "inline"}}
    if stream is not None:
        payload["stream"] = stream
    response = TestClient(app).post("/api/chat", json=payload, headers={"Authorization": "bearer test-secret"})
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].stream is bool(stream)
    assert calls[0].mode.value == "inline" and calls[0].conversation_id == "s1"
    assert calls[0].user_id == "u1"
    assert (tmp_path / "agent_space/notes.md").read_text().count("executed once") == 1
    if stream:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text.count("event: message\n") == 1
        assert response.text.count("event: done\n") == 1
        assert "event: token\n" in response.text
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        message = next(event for event in events if "reply" in event)
    else:
        assert response.headers["content-type"].startswith("application/json")
        message = response.json()
    assert message["reply"] == "收到：hello"
    assert message["model"] == "test-model" and message["usage"] == {"total_tokens": 7}


def test_public_reads_and_valid_workspace_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "test-secret")
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    for path in ("/health", "/manifest", "/.well-known/agent-card.json", "/foundry/bootstrap/state"):
        response = client.get(path)
        assert response.status_code == 200 and "test-secret" not in response.text
    response = client.put("/api/workspace/write", json={"path": "saved.txt", "content": "saved"},
                          headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert (tmp_path / "workspace/saved.txt").read_text() == "saved"
    assert client.post("/health").status_code == 401


def test_unconfigured_local_development_and_required_mode(tmp_path):
    app, calls = make_app(tmp_path / "local")
    assert TestClient(app).post("/api/chat", json={"message": "local"}).status_code == 200
    assert len(calls) == 1
    protected, calls = make_app(tmp_path / "required", require_agent_secret=True)
    assert TestClient(protected).post("/api/chat", json={"message": "remote"}).status_code == 401
    assert calls == []


def test_empty_or_removed_secret_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "test-secret")
    app, calls = make_app(tmp_path)
    monkeypatch.delenv("AGENT_SECRET")  # before even the first request
    client = TestClient(app)
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    monkeypatch.setenv("AGENT_SECRET", "")
    assert client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer "}).status_code == 401
    assert not calls


def test_platform_poll_auth_is_explicit_and_does_not_exempt_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "test-secret")
    app, calls = make_app(tmp_path, platform_authenticated_poll=True)
    client = TestClient(app)
    # The platform verifies its OIDC header before forwarding this route.
    headers = {"Authorization": "Bearer platform-oidc-token"}
    assert client.post("/foundry/poll", headers=headers).status_code == 200
    assert client.post("/api/chat", json={"message": "hi"}, headers=headers).status_code == 401
    assert client.get("/api/workspace/tree", headers=headers).status_code == 401
    assert not calls


def test_bootstrap_rotation_is_live_and_callbacks_keep_their_own_auth(tmp_path):
    bootstrap = FoundryBootstrap(manifest=AgentManifest(name="test_agent", label="Test"), agent_space_dir=tmp_path,
                                 config=FoundryBootstrapConfig(enabled=False, callback_secret="callback-secret"))
    app, calls = make_app(tmp_path, foundry_bootstrap=bootstrap)
    client = TestClient(app)
    payload = {"agent_name": "test_agent", "agent_secret": "first-secret"}
    # A bearer is not a substitute for the separate bootstrap callback token.
    response = client.post("/foundry/bootstrap/approved", json=payload, headers={"Authorization": "Bearer first-secret"})
    assert response.status_code == 403
    response = client.post("/foundry/bootstrap/approved", json=payload,
                           headers={"X-Foundry-Bootstrap-Token": "callback-secret"})
    assert response.status_code == 200
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer first-secret"}).status_code == 200
    response = client.post("/foundry/bootstrap/approved", json={"agent_name": "test_agent", "env_vars": {"AGENT_SECRET": "rotated"}},
                           headers={"X-Foundry-Bootstrap-Token": "callback-secret"})
    assert response.status_code == 200
    assert client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer first-secret"}).status_code == 401
    assert client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer rotated"}).status_code == 200
    bootstrap.state.env_vars.pop("AGENT_SECRET")
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert len(calls) == 2


def test_mounted_sdk_keeps_health_public_and_websocket_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "test-secret")
    sdk, _ = make_app(tmp_path)
    reached = []
    @sdk.websocket("/socket")
    async def echo(websocket: WebSocket):
        reached.append(True)
        await websocket.accept()
        await websocket.send_text("ok")
        await websocket.close()
    parent = FastAPI()
    parent.mount("/agent", sdk)
    client = TestClient(parent)
    assert client.get("/agent/health").status_code == 200
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/agent/socket"):
            pytest.fail("unauthorized websocket accepted")
    assert error.value.code == 1008 and not reached
    with client.websocket_connect("/agent/socket", headers={"Authorization": "Bearer test-secret"}) as ws:
        assert ws.receive_text() == "ok"
    assert len(reached) == 1


def test_packaged_service_uses_sdk_auth_and_requires_startup_secret(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "examples/agentservice/foundry_service.py"
    spec = importlib.util.spec_from_file_location("test_packaged_service", path)
    with pytest.raises(RuntimeError, match="AGENT_SECRET is not set"):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    monkeypatch.setenv("AGENT_SECRET", "package-secret")
    sdk, calls = make_app(tmp_path)
    stub = types.ModuleType("me_agent_example.app")
    stub.app = sdk
    monkeypatch.setitem(sys.modules, "me_agent_example.app", stub)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = TestClient(module.app)
    assert client.get("/.well-known/agent-card.json").status_code == 200
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer package-secret"}).status_code == 200
    assert len(calls) == 1
