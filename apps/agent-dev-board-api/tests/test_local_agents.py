from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent_dev_board_api import app as app_module
from agent_dev_board_api.local_agents import LocalAgentManager


def _make_manager(tmp_path: Path) -> LocalAgentManager:
    repo_root = tmp_path / "repo"
    (repo_root / "examples" / "me_agent" / "agent_space").mkdir(parents=True)
    (repo_root / "examples" / "me_agent" / "src").mkdir(parents=True)
    (repo_root / "examples" / "verilog_module_writer" / "agent_space").mkdir(parents=True)
    (repo_root / ".venv" / "bin").mkdir(parents=True)
    return LocalAgentManager(
        repo_root=repo_root,
        runtime_dir=tmp_path / "runtime",
        agents_file=tmp_path / "agents.yaml",
        venv_python=repo_root / ".venv" / "bin" / "python",
    )


def test_create_agent_reports_retired_name_collision(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    instance_dir = manager.instances_dir / "test1"
    manager._save_registry({
        "agents": [
            {
                "name": "test1",
                "label": "Test1",
                "template_id": "me_agent",
                "host": "127.0.0.1",
                "port": 8085,
                "base_url": "http://127.0.0.1:8085",
                "instance_dir": str(instance_dir),
                "status": "retired",
            }
        ]
    })

    with pytest.raises(ValueError, match="retired and cannot be reused"):
        manager.create_agent(template_id="me_agent", name="test1")


def test_create_agent_defaults_to_source_only(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)

    item = manager.create_agent(template_id="me_agent", name="test_source")

    assert item["name"] == "test_source"
    assert item["status"] == "stopped"
    assert item["pid"] is None
    assert Path(item["env_path"]).exists()
    assert (manager.instances_dir / "test_source" / "agent_space").exists()


def test_install_developer_claim_to_source_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.create_agent(template_id="me_agent", name="test_source")
    monkeypatch.setattr(app_module, "LOCAL_AGENT_MANAGER", manager)

    result = app_module._install_developer_claim_to_source(
        "test_source",
        {
            "discovery_claim_token": "secret-claim",
            "bootstrap_delivery": "poll",
            "foundry_base_url": "https://foundry.example.com",
            "public_base_url": "http://127.0.0.1:8085",
            "developer_identity": {"github_login": "dev"},
            "force_rediscover": True,
        },
    )

    state_path = Path(result["state_path"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    public_state = app_module._public_bootstrap_state(state)
    assert state["discovery_claim_token"] == "secret-claim"
    assert state["foundry_base_url"] == "https://foundry.example.com"
    assert state["developer_identity"]["github_login"] == "dev"
    assert public_state["has_discovery_claim"] is True
    assert "discovery_claim_token" not in public_state


def test_create_local_agent_returns_validation_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_create_agent(**_: object) -> dict[str, object]:
        raise ValueError("Local agent 'test1' is retired and cannot be reused.")

    monkeypatch.setattr(app_module.LOCAL_AGENT_MANAGER, "create_agent", fail_create_agent)

    response = TestClient(app_module.app).post(
        "/api/local-agents",
        json={"template_id": "me_agent", "name": "test1"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Local agent 'test1' is retired and cannot be reused."


def test_retire_local_agent_continues_when_foundry_user_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = app_module.LiteAgentConfig(
        name="test10",
        label="Test10",
        base_url="http://127.0.0.1:8097",
    )

    async def bootstrap_state(_: object) -> dict[str, object]:
        return {
            "enabled": True,
            "foundry_base_url": "https://foundry.cochiper.com",
            "registered_agent_name": "test10_agent_ext",
            "registration_status": "APPROVED",
        }

    async def fail_remote_retire(**_: object) -> dict[str, object]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "User no longer exists",
                "route_attempts": [{"method": "POST", "status_code": 401}],
            },
        )

    monkeypatch.setattr(app_module, "_load_agents", lambda: {"test10": agent})
    monkeypatch.setattr(app_module, "_agent_bootstrap_state", bootstrap_state)
    monkeypatch.setattr(app_module, "_retire_foundry_agent", fail_remote_retire)
    monkeypatch.setattr(app_module.CLOUD_RUN_MANAGER, "cleanup_agent", lambda _name: {"ok": True, "actions": []})
    monkeypatch.setattr(app_module.LOCAL_AGENT_MANAGER, "ensure_runtime_files", lambda: None)
    monkeypatch.setattr(
        app_module.LOCAL_AGENT_MANAGER,
        "retire_agent",
        lambda name, remote_result=None: {"name": name, "status": "retired", "retire_result": remote_result},
    )

    response = TestClient(app_module.app).post(
        "/api/local-agents/test10/retire",
        json={
            "foundry_url": "https://foundry.cochiper.com",
            "developer_token": "stale-token",
            "stop_local": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["foundry"]["ok"] is False
    assert payload["foundry"]["status"] == "REMOTE_RETIRE_FAILED"
    assert "Foundry login session is stale" in payload["foundry"]["message"]
    assert payload["foundry"]["upstream_message"] == "User no longer exists"
    assert payload["local_agent"]["status"] == "retired"
