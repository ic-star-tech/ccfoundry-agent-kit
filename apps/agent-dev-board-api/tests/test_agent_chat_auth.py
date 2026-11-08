"""The Dev Board keeps agent credentials server-side and bound to the agent URL."""
from pathlib import Path
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dev_board_api import app as board
from ccfoundry_agent_kit import AgentManifest, ChatResponse, create_agent_app
from ccfoundry_agent_kit import client as client_module


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/sync"])
def test_board_chat_authenticates_after_bootstrap(tmp_path, monkeypatch, endpoint):
    monkeypatch.setenv("AGENT_SECRET", "board-agent-secret")
    calls = []
    async def handler(request, space):
        calls.append(request)
        return ChatResponse(reply="hello")
    agent_app = create_agent_app(manifest=AgentManifest(name="demo", label="Demo"), chat_handler=handler, agent_space_dir=tmp_path)
    agent = board.LiteAgentConfig(name="demo", label="Demo", base_url="http://agent.test")
    monkeypatch.setattr(board, "_load_agents", lambda: {"demo": agent})
    state = {"public_base_url": "http://agent.test/", "env_vars": {"AGENT_SECRET": "board-agent-secret"}}
    monkeypatch.setattr(board, "_read_agent_source_bootstrap_state", lambda name: (state, None))
    real = httpx.AsyncClient
    monkeypatch.setattr(client_module.httpx, "AsyncClient", lambda *a, **kw: real(*a, **kw, transport=httpx.ASGITransport(app=agent_app)))
    response = TestClient(board.app).post(endpoint, json={"agent_name": "demo", "message": "hi"})
    assert response.status_code == 200
    assert len(calls) == 1 and "hello" in response.text
    assert "board-agent-secret" not in response.text


@pytest.mark.parametrize("url", ["http://other.test", "http://agent.test/other", ""])
def test_changed_agent_url_does_not_receive_previous_secret(monkeypatch, url):
    state = {"public_base_url": url, "env_vars": {"AGENT_SECRET": "old-secret"}}
    monkeypatch.setattr(board, "_read_agent_source_bootstrap_state", lambda name: (state, None))
    received = []
    monkeypatch.setattr(board, "AgentClient", lambda base_url, **kw: received.append((base_url, kw)))
    board._chat_agent_client(board.LiteAgentConfig(name="demo", label="Demo", base_url="http://agent.test"))
    assert received == [("http://agent.test", {"agent_secret": None})]


def test_cloud_run_plan_binds_platform_poll_to_private_iam_service(tmp_path):
    root = Path(__file__).resolve().parents[3]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # Any external execution is a test failure; dry-run must only print its plan.
    for name in ("gcloud", "docker", "curl"):
        command = bindir / name
        command.write_text("#!/bin/sh\necho 'unexpected external execution' >&2\nexit 99\n")
        command.chmod(0o755)
    result = subprocess.run(["bash", str(root / "scripts/deploy-cloudrun.sh"), "test-agent", "--project", "test-project", "--dry-run"],
                            env={"PATH": f"{bindir}:/usr/bin:/bin"}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    deploy = next(line for line in result.stdout.splitlines() if line.startswith("[dry-run] gcloud run deploy"))
    assert "FOUNDRY_POLL_AUTH_MODE=platform" in deploy
    assert "--no-allow-unauthenticated" in deploy and "--invoker-iam-check" in deploy
    scheduler = next(line for line in result.stdout.splitlines() if line.startswith("[dry-run] gcloud scheduler jobs create"))
    assert "/foundry/poll" in scheduler and "--oidc-token-audience=" in scheduler
