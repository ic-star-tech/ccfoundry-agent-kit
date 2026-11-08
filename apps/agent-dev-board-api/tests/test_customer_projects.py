from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dev_board_api import app as api
from agent_dev_board_api.local_agents import LocalAgentManager
from agent_dev_board_api.projects import MANIFEST, load_project, project_id

ROOT = Path(__file__).resolve().parents[3]


def project(tmp_path: Path) -> Path:
    root = tmp_path / "customer source"
    (root / "src").mkdir(parents=True)
    (root / "agent_space").mkdir()
    (root / "agent_space" / "config.yaml").write_text("name: customer\n")
    (root / "src" / "customer.py").write_text("""import json, os
from pathlib import Path
Path(__file__).with_name("imported").write_text("yes")
async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup": await send({"type":"lifespan.startup.complete"})
            if message["type"] == "lifespan.shutdown":
                await send({"type":"lifespan.shutdown.complete"}); return
    body = b""
    while True:
        event = await receive(); body += event.get("body", b"")
        if not event.get("more_body"): break
    value = json.loads(body or b"{}")
    result = {"status":"healthy", "reply":"customer-code:" + value.get("message", ""), "name":os.getenv("ME_AGENT_NAME"), "state":os.getenv("FOUNDRY_BOOTSTRAP_STATE_PATH")}
    await send({"type":"http.response.start", "status":200, "headers":[(b"content-type",b"application/json")]})
    await send({"type":"http.response.body", "body":json.dumps(result).encode()})
""")
    (root / MANIFEST).write_text(
        json.dumps(
            {
                "version": 1,
                "label": "Customer source",
                "app_module": "customer:app",
                "python": sys.executable,
            }
        )
    )
    return root


def manager(tmp_path: Path, root: Path) -> LocalAgentManager:
    return LocalAgentManager(
        repo_root=ROOT,
        runtime_dir=tmp_path / "runtime",
        agents_file=tmp_path / "runtime" / "agents.yaml",
        venv_python=Path(sys.executable),
        projects=[root],
    )


def test_project_inspection_does_not_import_or_install(tmp_path):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    template = next(t for t in service.list_templates() if t["kind"] == "project")
    assert template["id"] == project_id(root)
    assert template["project_path"] == str(root)
    assert not (root / "src" / "imported").exists()
    item = service.create_agent(template_id=template["id"], name="own_source")
    assert item["source_kind"] == "project" and item["app_module"] == "customer:app"
    assert item["status"] == "stopped"
    assert (
        Path(item["instance_dir"]) / "agent_space" / "config.yaml"
    ).read_text() == "name: customer\n"
    assert not (root / "src" / "imported").exists()
    assert Path(item["env_path"]).stat().st_mode & 0o777 == 0o600


def test_actual_customer_module_runs_in_its_selected_environment(tmp_path):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    item = service.create_agent(template_id=project_id(root), name="own_source")
    service.start_agent(item["name"])
    try:
        deadline = time.monotonic() + 10
        with httpx.Client(trust_env=False, timeout=1) as client:
            while True:
                try:
                    response = client.post(
                        item["base_url"] + "/chat", json={"message": "hello"}
                    )
                    break
                except httpx.ConnectError:
                    if time.monotonic() > deadline:
                        pytest.fail(
                            (
                                Path(item["instance_dir"]) / "logs" / "agent.log"
                            ).read_text()
                        )
                    time.sleep(0.03)
        assert response.status_code == 200
        assert response.json()["reply"] == "customer-code:hello"
        assert response.json()["name"] == "own_source"
        assert response.json()["state"] == str(
            Path(item["instance_dir"]) / ".foundry_bootstrap.json"
        )
        assert (root / "src" / "imported").exists()
    finally:
        service.stop_agent(item["name"])
    assert not service.children
    assert service.list_agents()[0]["status"] == "stopped"


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("app_dir", "../outside"),
        ("app_dir", "/tmp"),
        ("app_module", "customer:app;bad"),
        ("app_module", "other:app"),
        ("agent_space", "../outside"),
    ],
)
def test_project_path_and_module_validation(tmp_path, field, value):
    root = project(tmp_path)
    config = json.loads((root / MANIFEST).read_text())
    config[field] = value
    (root / MANIFEST).write_text(json.dumps(config))
    with pytest.raises((ValueError, FileNotFoundError)):
        load_project(root)


def test_project_symlink_escape_is_rejected(tmp_path):
    root = project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    config = json.loads((root / MANIFEST).read_text())
    config["agent_space"] = "escape"
    (root / MANIFEST).write_text(json.dumps(config))
    with pytest.raises(ValueError):
        load_project(root)


def test_api_only_selects_operator_registered_project(tmp_path, monkeypatch):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    monkeypatch.setattr(api, "LOCAL_AGENT_MANAGER", service)
    client = TestClient(api.app)
    templates = client.get("/api/local-agent-templates").json()
    assert any(t["kind"] == "project" for t in templates)
    out = client.post(
        "/api/local-agents",
        json={"template_id": project_id(root), "name": "customer_api"},
    )
    assert out.status_code == 200 and out.json()["source_kind"] == "project"
    denied = client.post(
        "/api/local-agents",
        json={
            "template_id": "project_unregistered",
            "name": "unknown",
            "project_path": str(root),
        },
    )
    assert denied.status_code == 400
    assert not (root / "src" / "imported").exists()


def test_running_customer_source_can_stop_after_manifest_is_removed(tmp_path):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    item = service.create_agent(template_id=project_id(root), name="editable_source")
    started = service.start_agent(item["name"])
    try:
        (root / MANIFEST).unlink()
        assert service.list_agents()[0]["pid"] == started["pid"]
        assert service.stop_agent(item["name"])["status"] == "stopped"
        assert not service.children
    finally:
        child = service.children.pop(started["pid"], None)
        if child is not None:
            child.terminate()
            child.wait(timeout=5)
