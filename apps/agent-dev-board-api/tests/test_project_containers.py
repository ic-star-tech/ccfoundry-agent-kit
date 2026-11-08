from __future__ import annotations

import asyncio
import csv
import json

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from agent_dev_board_api import app as api
from agent_dev_board_api.project_containers import (
    ProjectContainers,
    copy_snapshot,
    docker_csv,
)
from agent_dev_board_api.project_runtime import ProjectAuth, create_app
from agent_dev_board_api.projects import load_project, project_id
from ccfoundry_agent_kit.agentservice_manifest import (
    validate_local_manifest,
    local_manifest_warnings,
)
from test_customer_projects import project, manager


def test_snapshot_excludes_credentials_and_local_runtime(tmp_path):
    root = project(tmp_path)
    forbidden = [
        ".env",
        ".env.production",
        ".container-secret",
        ".container.env",
        ".foundry_bootstrap.json",
        "pip.conf",
        "key.pem",
        "client.p12",
        ".npmrc",
        "runtime/bootstrap.json",
        ".venv/secret",
        ".git/config",
        "src/__pycache__/x.pyc",
    ]
    for name in forbidden:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SECRET")
    named_secret = root / "src" / "odd-credentials.txt"
    named_secret.write_text("ALSO SECRET")
    output = tmp_path / "snapshot"
    hashes = copy_snapshot(root, ["."], output, {named_secret.resolve()})
    assert set(hashes) == {"src/customer.py", "agent_space/config.yaml"}
    assert sorted(
        str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()
    ) == sorted(hashes)
    assert all(
        b"SECRET" not in p.read_bytes() for p in output.rglob("*") if p.is_file()
    )


@pytest.mark.parametrize("mode", ["file", "directory", "parent"])
def test_snapshot_rejects_escape(tmp_path, mode):
    root = project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_text("SECRET")
    includes = ["../outside"]
    if mode != "parent":
        (root / "src" / "link").symlink_to(
            outside if mode == "directory" else outside / "private",
            target_is_directory=mode == "directory",
        )
        includes = ["src"]
    with pytest.raises(ValueError):
        copy_snapshot(root, includes, tmp_path / "output")


def test_snapshot_preserves_empty_explicit_directories(tmp_path):
    root = project(tmp_path)
    (root / "agent_space" / "config.yaml").unlink()
    copy_snapshot(root, ["src", "agent_space"], tmp_path / "output")
    assert (tmp_path / "output" / "agent_space").is_dir()


def test_generated_manifest_validates_for_local_appliance(tmp_path):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    item = service.create_agent(
        template_id=project_id(root), name="customer_long_agent_name"
    )
    image_id = "sha256:" + "1" * 64
    text = yaml.safe_dump(
        ProjectContainers.manifest(item, load_project(root), image_id, "1.2.3")
    )
    assert validate_local_manifest(text) == []
    assert local_manifest_warnings(text) == []
    value = yaml.safe_load(text)
    assert value["spec"]["image"]["digest"] == image_id
    assert value["spec"]["runtime"]["config_env"] == ["AGENT_SECRET"]
    assert value["spec"]["runtime"]["endpoint_path"] == "/"


def test_docker_csv_keeps_paths_with_spaces_commas_and_quotes():
    fields = ["type=bind", 'src=/tmp/customer, "source"', "dst=/workspace"]
    assert next(csv.reader([docker_csv(*fields)])) == fields


async def echo(scope, receive, send):
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": json.dumps(
                {
                    "client": scope.get("client"),
                    "authorization": dict(scope.get("headers", []))
                    .get(b"authorization", b"")
                    .decode(),
                }
            ).encode(),
        }
    )


@pytest.mark.parametrize(
    "path,method,token,status",
    [
        ("/health", "GET", "", 200),
        ("/health", "HEAD", "", 200),
        ("/health", "POST", "", 401),
        ("/manifest", "GET", "", 401),
        ("/chat", "POST", "", 401),
        ("/api/chat", "POST", "wrong", 401),
        ("/api/chat", "POST", "agent-key", 200),
    ],
)
def test_image_auth_enforces_agentservice_bearer(path, method, token, status):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ProjectAuth(echo, "agent-key")),
            base_url="http://fixture",
        ) as client:
            response = await client.request(
                method,
                path,
                headers={"Authorization": "Bearer " + token} if token else {},
            )
            assert response.status_code == status

    asyncio.run(run())


def test_debug_credential_rotation_and_claim_loopback_are_scoped(tmp_path):
    state = tmp_path / "bootstrap.json"
    state.write_text(json.dumps({"env_vars": {"AGENT_SECRET": "rotated"}}))
    wrapped = ProjectAuth(echo, "initial", "operator", str(state))

    async def run():
        transport = httpx.ASGITransport(app=wrapped, client=("192.0.2.4", 1234))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fixture"
        ) as client:
            assert (
                await client.post("/chat", headers={"Authorization": "Bearer initial"})
            ).status_code == 401
            for path in ["/chat", "/foundry/bootstrap/developer-claim"]:
                response = await client.post(
                    path, headers={"Authorization": "Bearer operator"}
                )
                assert response.status_code == 200
                assert response.json()["authorization"] == "Bearer rotated"
                assert response.json()["client"][0] == (
                    "127.0.0.1" if path.endswith("developer-claim") else "192.0.2.4"
                )
            response = await client.post(
                "/foundry/bootstrap/developer-claim",
                headers={"Authorization": "Bearer rotated"},
            )
            assert response.json()["client"][0] == "192.0.2.4"
            assert (await client.get("/manifest")).status_code == 200
            response = await client.post(
                "/chat",
                headers=[
                    ("Authorization", "Bearer operator"),
                    ("Authorization", "Bearer rotated"),
                ],
            )
            assert response.status_code == 401
            state.write_text(json.dumps({"env_vars": {"AGENT_SECRET": ""}}))
            assert (
                await client.post("/chat", headers={"Authorization": "Bearer rotated"})
            ).status_code == 401

    asyncio.run(run())


def test_image_fails_before_importing_customer_code_without_secret(
    tmp_path, monkeypatch
):
    root = project(tmp_path)
    monkeypatch.delenv("AGENT_SECRET", raising=False)
    monkeypatch.setenv("CCFOUNDRY_PROJECT_ROOT", str(root))
    monkeypatch.setenv("CCFOUNDRY_PROJECT_CONFIG", str(root / "missing.json"))
    with pytest.raises(RuntimeError, match="AGENT_SECRET"):
        create_app()
    assert not (root / "src" / "imported").exists()


def test_unauthorized_websocket_closed_without_entering_project():
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    asyncio.run(
        ProjectAuth(echo, "secret")(
            {"type": "websocket", "path": "/ws", "headers": []}, receive, send
        )
    )
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_project_cloud_run_rejected_before_builder_or_login(tmp_path, monkeypatch):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    service.create_agent(template_id=project_id(root), name="customer")
    monkeypatch.setattr(api, "LOCAL_AGENT_MANAGER", service)
    with TestClient(api.app) as client:
        response = client.post(
            "/api/cloud-run/deploy",
            json={"agent_name": "customer", "project": "unused"},
        )
    assert (
        response.status_code == 400
        and "built-in templates only" in response.json()["detail"]
    )


def test_api_never_accepts_unregistered_project_build(tmp_path, monkeypatch):
    service = manager(tmp_path, project(tmp_path))
    monkeypatch.setattr(api, "LOCAL_AGENT_MANAGER", service)
    with TestClient(api.app) as client:
        for action in ["build", "start", "export", "push"]:
            response = client.post(
                "/api/local-agents/arbitrary/container/" + action,
                json={"target_ref": "localhost:5000/test:v1"},
            )
            assert response.status_code == 400


def test_snapshot_remains_readable_by_image_user_under_private_umask(tmp_path):
    import os

    root = project(tmp_path)
    previous = os.umask(0o077)
    try:
        copy_snapshot(root, ["src", "agent_space"], tmp_path / "context")
    finally:
        os.umask(previous)
    assert (tmp_path / "context").stat().st_mode & 0o777 == 0o755
    assert (tmp_path / "context" / "src").stat().st_mode & 0o777 == 0o755
    assert (
        tmp_path / "context" / "src" / "customer.py"
    ).stat().st_mode & 0o777 == 0o644


def test_retired_container_source_stays_retired_without_docker(tmp_path, monkeypatch):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    item = service.create_agent(template_id=project_id(root), name="retired_customer")
    item.update({"runtime_kind": "container", "status": "retired"})
    service._replace_agent(service._load_registry(), item)

    def forbidden(*args, **kwargs):
        pytest.fail("Retired sources must not inspect or start Docker")

    monkeypatch.setattr("agent_dev_board_api.project_containers.docker", forbidden)
    assert service.list_agents() == []
    assert service.list_agents(include_retired=True)[0]["status"] == "retired"
    with pytest.raises(ValueError, match="Retired"):
        service.containers.start(item["name"])


def test_missing_descriptor_keeps_inventory_and_stop_available(tmp_path):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    item = service.create_agent(template_id=project_id(root), name="missing_descriptor")
    (root / "ccfoundry.project.json").unlink()
    assert any(t["id"] == project_id(root) for t in service.list_templates())
    assert service.stop_agent(item["name"])["status"] == "stopped"
    with pytest.raises(ValueError, match="project files are unavailable"):
        service.start_agent(item["name"])


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_runtime_credential_write_cannot_overwrite_a_link_target(tmp_path, kind):
    import os
    from agent_dev_board_api.project_containers import private_runtime_file

    target = tmp_path / "unrelated"
    target.write_text("unchanged")
    credential = tmp_path / ".container-secret"
    if kind == "symlink":
        credential.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, credential)
    else:
        os.mkfifo(credential)
    with pytest.raises((ValueError, OSError)):
        private_runtime_file(credential, "new-secret")
    assert target.read_text() == "unchanged"


def test_registry_push_receipt_cannot_use_an_old_digest(tmp_path, monkeypatch):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    item = service.create_agent(template_id=project_id(root), name="push_fixture")
    item["project_build_id"] = "a" * 32
    directory = service.containers.builds / item["project_build_id"]
    directory.mkdir(parents=True)
    image_id = "sha256:" + "1" * 64
    repository = "registry.example.internal/team/source"
    old_digest = "sha256:" + "2" * 64
    current_push = "sha256:" + "3" * 64
    (directory / "result.json").write_text(
        json.dumps(
            {
                "source_id": item["source_id"],
                "image_id": image_id,
                "platform": "linux/amd64",
            }
        )
    )
    service._replace_agent(service._load_registry(), item)

    def engine(args, *, timeout=30, log=None):
        if args[:2] == ["image", "push"]:
            log.write_text("test: digest: " + current_push + " size: 1234\n")
            return ""
        if args[:2] == ["image", "inspect"]:
            return json.dumps(
                [{"Id": image_id, "RepoDigests": [repository + "@" + old_digest]}]
            )
        assert args[:2] == ["image", "tag"]
        return ""

    monkeypatch.setattr("agent_dev_board_api.project_containers.docker", engine)
    with pytest.raises(RuntimeError, match="verifiable repository digest"):
        service.containers.push(item["name"], repository + ":test")
    assert not (directory / "push.json").exists()


def test_stop_refuses_a_container_owned_by_another_source(tmp_path, monkeypatch):
    root = project(tmp_path)
    service = manager(tmp_path, root)
    item = service.create_agent(template_id=project_id(root), name="owner_fixture")
    item.update({"container_id": "c" * 64, "runtime_kind": "container"})
    service._replace_agent(service._load_registry(), item)
    calls = []

    def engine(args, **kwargs):
        calls.append(args[:2])
        if args[:2] == ["container", "ls"]:
            return item["container_id"]
        if args[:2] == ["container", "inspect"]:
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {"ai.foundry.dev.source": "another-source"}
                        },
                        "State": {"Running": True},
                    }
                ]
            )
        pytest.fail("Must not mutate a container belonging to another source")

    monkeypatch.setattr("agent_dev_board_api.project_containers.docker", engine)
    with pytest.raises(RuntimeError, match="ownership label"):
        service.containers.stop(item["name"])
    assert calls == [["container", "ls"], ["container", "inspect"]]
    assert service._load_registry()["agents"][0]["container_id"] == item["container_id"]
