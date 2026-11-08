from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent_dev_board_api import app as board
from agent_dev_board_api.foundry_login import (
    FoundryDeveloperSession, FoundrySessions, canonical_foundry_url,
)
from agent_dev_board_api.local_agents import LocalAgentManager

PASSWORD = "fixture-password-should-not-survive"
ACCESS = "fixture-access-must-not-survive"
DEVELOPER = "sk-foundry-local-dev-fixture"
IDENTITY = {"provider": "foundry", "user_id": "fd190000-0000-4000-8000-000000000001",
            "username": "local-developer", "developer_id": "foundry:fd190000-0000-4000-8000-000000000001"}


@pytest.fixture
def foundry_peer():
    state = SimpleNamespace(calls=[], overrides={})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            state.calls.append((self.path, dict(self.headers), body))
            status, payload, headers = state.overrides.get(self.path, (200, None, {}))
            if payload is None:
                if self.path == "/api/auth/login":
                    assert body == {"username": "local-developer", "password": PASSWORD}
                    payload = {"ok": True, "token": ACCESS}
                elif self.path == "/api/developer/session":
                    assert self.headers.get("Authorization") == f"Bearer {ACCESS}"
                    payload = {"ok": True, "developer_token": DEVELOPER, "developer_identity": IDENTITY,
                               "expires_at": datetime.fromtimestamp(time.time() + 890, timezone.utc).isoformat()}
                elif self.headers.get("Authorization") != f"Bearer {DEVELOPER}":
                    status, payload = 401, {"detail": "login required"}
                elif self.path == "/api/developer/bootstrap-ticket":
                    payload = {"ok": True, "discovery_claim_token": "fixture-discovery-claim", "ticket_id": "ticket-19",
                               "developer_identity": IDENTITY, "developer_id": IDENTITY["developer_id"]}
                elif self.path == "/api/developer/notification-preferences":
                    payload = {"ok": True, "email": body["email"]}
                elif self.path.endswith("/retire"):
                    payload = {"ok": True, "status": "RETIRED"}
                else:
                    status, payload = 404, {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def local_board(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("local-account flow attempted GitHub discovery or network")

    monkeypatch.setattr(board, "_discover_github_token", forbidden)
    monkeypatch.setattr(board, "_github_identity", forbidden)
    monkeypatch.setattr(board, "_git_context", lambda: {})
    monkeypatch.setattr(board, "FOUNDRY_SESSIONS", FoundrySessions())
    monkeypatch.setattr(board.CLOUD_RUN_MANAGER, "cleanup_agent", lambda _: {"ok": True, "targets": []})
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    repo = tmp_path / "repo"
    (repo / "examples/me_agent/agent_space").mkdir(parents=True)
    (repo / "examples/me_agent/src").mkdir(parents=True)
    manager = LocalAgentManager(repo_root=repo, runtime_dir=tmp_path / "runtime",
                                agents_file=tmp_path / "agents.yaml", venv_python=repo / "python")
    manager.create_agent(template_id="me_agent", name="local-source")
    monkeypatch.setattr(board, "LOCAL_AGENT_MANAGER", manager)
    monkeypatch.setattr(board, "AGENTS_FILE", tmp_path / "agents.yaml")
    monkeypatch.setattr(board, "RUNTIME_DIR", tmp_path / "runtime")
    async def source_state(agent):
        return board._read_agent_source_bootstrap_state(agent.name)[0]
    monkeypatch.setattr(board, "_agent_bootstrap_state", source_state)
    return TestClient(board.app), manager


def login(client, peer):
    result = client.post("/api/developer/foundry-login", json={"foundry_url": peer.url,
                          "username": "local-developer", "password": PASSWORD})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    for secret in (PASSWORD, ACCESS, DEVELOPER):
        assert secret not in result.text
    return result.json()["session_id"]


def test_local_account_login_claim_notifications_and_retire(local_board, foundry_peer, caplog):
    client, manager = local_board
    peer = foundry_peer
    handle = login(client, peer)
    request = {"agent_name": "local-source", "foundry_url": peer.url, "foundry_session_id": handle}
    ticket = client.post("/api/developer/bootstrap-ticket", json={**request, "runtime_target": "source"})
    assert ticket.status_code == 200 and ticket.json()["claim_applied"], ticket.text
    state_path = board._agent_source_state_path("local-source")
    assert state_path is not None
    state = json.loads(state_path.read_text())
    assert state["developer_identity"]["user_id"] == IDENTITY["user_id"]
    assert state["discovery_claim_token"] == "fixture-discovery-claim"
    result = client.post("/api/developer/notification-preferences/sync", json={**request, "email": "local@example.test"})
    assert result.status_code == 200, result.text
    assert result.json()["preferences"]["developer_identity"]["provider"] == "foundry"
    # Retire follows the actual stored registration, not the currently selected UI URL.
    state["registered_agent_name"] = "local-source"
    state_path.write_text(json.dumps(state))
    result = client.post("/api/local-agents/local-source/retire", json=request)
    assert result.status_code == 200 and result.json()["foundry"]["ok"], result.text
    for path, headers, body in peer.calls[2:]:
        assert headers["Authorization"] == f"Bearer {DEVELOPER}", path
        assert "X-GitHub-Token" not in headers
        assert PASSWORD not in json.dumps(body) and ACCESS not in json.dumps(body)
    # Source claims persist by design; login and developer credentials never do.
    for path in manager.runtime_dir.rglob("*"):
        if path.is_file():
            for secret in (PASSWORD, ACCESS, DEVELOPER, handle):
                assert secret not in path.read_text(), path
    for secret in (PASSWORD, ACCESS, DEVELOPER):
        assert secret not in caplog.text
    client.post("/api/developer/foundry-logout", json={"session_id": handle})
    with pytest.raises(HTTPException) as rejected:
        board.FOUNDRY_SESSIONS.resolve(handle, peer.url)
    assert rejected.value.status_code == 401


@pytest.mark.parametrize("change", ["port", "scheme", "path", "expired", "mixed", "missing", "empty"])
def test_local_handle_never_falls_back_or_crosses_target(local_board, foundry_peer, change):
    client, _ = local_board
    peer = foundry_peer
    handle = login(client, peer)
    target = peer.url
    extra = {"auth_method": "foundry"}
    expected = 409
    if change == "port":
        target = "http://127.0.0.1:1"
    elif change == "scheme":
        target = target.replace("http:", "https:")
    elif change == "path":
        target += "/other-installation"
    elif change == "expired":
        session = board.FOUNDRY_SESSIONS.resolve(handle, target)
        board.FOUNDRY_SESSIONS._sessions[handle] = FoundryDeveloperSession(target, session.developer_token, session.identity, time.time() - 1)
        expected = 401
    elif change == "mixed":
        extra["github_token"] = "must-not-be-used"
        expected = 400
    elif change in {"missing", "empty"}:
        handle = "expired-or-restarted" if change == "missing" else ""
        expected = 401
    before = len(peer.calls)
    for endpoint in ("/api/developer/bootstrap-ticket", "/api/developer/notification-preferences/sync"):
        response = client.post(endpoint, json={"agent_name": "local-source", "foundry_url": target,
                               "foundry_session_id": handle, "email": "dev@example.test", **extra})
        assert response.status_code == expected, response.text
    assert len(peer.calls) == before


@pytest.mark.parametrize("status", [302, 307, 401, 403, 404, 405, 500])
@pytest.mark.parametrize("route", ["/api/auth/login", "/api/developer/session"])
def test_login_rejections_never_redirect_or_echo_body(local_board, foundry_peer, status, route):
    client, _ = local_board
    peer = foundry_peer
    peer.overrides[route] = (status, {"detail": PASSWORD + ACCESS + DEVELOPER}, {"Location": peer.url + "/credential-sink"})
    response = client.post("/api/developer/foundry-login", json={"foundry_url": peer.url, "username": "local-developer", "password": PASSWORD})
    assert response.status_code >= 400
    assert [call[0] for call in peer.calls] == (["/api/auth/login"] if route.endswith("login") else ["/api/auth/login", route])
    for secret in (PASSWORD, ACCESS, DEVELOPER):
        assert secret not in response.text
    assert not board.FOUNDRY_SESSIONS._sessions


@pytest.mark.parametrize("status", [307, 401, 500])
def test_ticket_failure_is_not_replayed_on_alias(local_board, foundry_peer, status):
    client, _ = local_board
    peer = foundry_peer
    handle = login(client, peer)
    peer.overrides["/api/developer/bootstrap-ticket"] = (status, {}, {"Location": peer.url + "/sink"})
    result = client.post("/api/developer/bootstrap-ticket", json={"agent_name": "local-source", "foundry_url": peer.url, "foundry_session_id": handle})
    assert not result.json()["ok"]
    assert [call[0] for call in peer.calls[2:]] == ["/api/developer/bootstrap-ticket"]


def test_context_local_mode_never_discovers_github(local_board, foundry_peer):
    client, _ = local_board
    response = client.post("/api/developer/context", json={"foundry_url": foundry_peer.url})
    assert response.status_code == 200
    assert response.json()["github"]["has_token"] is False


@pytest.mark.parametrize("body", [{"password": {"nested": PASSWORD}}, [PASSWORD], PASSWORD, {"password": PASSWORD * 500}])
def test_password_not_echoed_by_validation(local_board, body):
    client, _ = local_board
    result = client.post("/api/developer/foundry-login", json=body)
    assert result.status_code == 400
    assert PASSWORD not in result.text


@pytest.mark.parametrize("url", ["file:///tmp/x", "https://u:p@foundry.test", "https://foundry.test/?key=x", "https://foundry.test/#x", "https://foundry.test/../x", "http://foundry.test", "https://foundry.test:invalid", "https://foundry.test\\@evil.test"])
def test_login_target_validation(url, monkeypatch):
    monkeypatch.delenv("CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP", raising=False)
    with pytest.raises(HTTPException):
        canonical_foundry_url(url)


def test_target_canonicalization_and_bounded_cache():
    assert canonical_foundry_url("https://FOUNDRY.test:443/install/") == "https://foundry.test/install"
    sessions = FoundrySessions(capacity=1)
    item = FoundryDeveloperSession("https://foundry.test", DEVELOPER, IDENTITY, time.time() + 10)
    first = sessions.add(item)
    assert sessions.resolve(first, "https://FOUNDRY.test:443/") == item
    assert DEVELOPER not in repr(item)
    with pytest.raises(HTTPException) as raised:
        sessions.add(item)
    assert raised.value.status_code == 429
    sessions.remove(first)
    assert sessions.add(item) != first


@pytest.mark.parametrize("mutation", ["expired", "unbounded", "access_token", "wrong_provider", "missing_identity"])
def test_invalid_upstream_session_is_not_retained(local_board, foundry_peer, mutation):
    client, _ = local_board
    peer = foundry_peer
    result = {"ok": True, "developer_token": DEVELOPER, "developer_identity": dict(IDENTITY),
              "expires_at": datetime.fromtimestamp(time.time() + 800, timezone.utc).isoformat()}
    if mutation in {"expired", "unbounded"}:
        result["expires_at"] = datetime.fromtimestamp(time.time() + (-60 if mutation == "expired" else 86400), timezone.utc).isoformat()
    elif mutation == "access_token":
        result["developer_token"] = ACCESS
    elif mutation == "wrong_provider":
        result["developer_identity"]["provider"] = "github"
    else:
        del result["developer_identity"]
    peer.overrides["/api/developer/session"] = (200, result, {})
    response = client.post("/api/developer/foundry-login", json={"foundry_url": peer.url, "username": "local-developer", "password": PASSWORD})
    assert response.status_code == 502
    assert not board.FOUNDRY_SESSIONS._sessions
    for secret in (PASSWORD, ACCESS, DEVELOPER):
        assert secret not in response.text


def test_explicit_private_ca_and_hostname_verification(local_board, foundry_peer, tmp_path, monkeypatch):
    import ssl
    import subprocess
    from agent_dev_board_api.foundry_login import foundry_http_client

    # A temporary test-only CA/certificate; never modifies the host trust store.
    cert, key = tmp_path / "fixture.pem", tmp_path / "fixture.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    class TLSHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"private-ca-ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), TLSHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    import asyncio
    import httpx
    async def read(host):
        async with foundry_http_client() as client:
            return await client.get(f"https://{host}:{server.server_port}")
    try:
        monkeypatch.delenv("CCFOUNDRY_DEV_BOARD_CA_BUNDLE", raising=False)
        with pytest.raises(httpx.ConnectError):
            asyncio.run(read("localhost"))
        monkeypatch.setenv("CCFOUNDRY_DEV_BOARD_CA_BUNDLE", str(cert))
        assert asyncio.run(read("localhost")).text == "private-ca-ok"
        with pytest.raises(httpx.ConnectError):
            asyncio.run(read("127.0.0.1"))
        monkeypatch.setenv("CCFOUNDRY_DEV_BOARD_CA_BUNDLE", str(tmp_path / "missing.pem"))
        with pytest.raises(HTTPException) as rejected:
            foundry_http_client()
        assert rejected.value.status_code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
