"""Explicit Docker integration test; use only a disposable CI Docker host.

Run with --work-dir pointing to a new directory. Requires SDK/API imports and
prepares dependency wheels from the configured pip index before offline tests.
It creates and removes only its own containers and image tags, never prunes.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx

from agent_dev_board_api.local_agents import LocalAgentManager
from agent_dev_board_api.project_containers import docker
from agent_dev_board_api.projects import project_id


def ready(url):
    deadline = time.monotonic() + 40
    with httpx.Client(trust_env=False, timeout=1) as client:
        while time.monotonic() < deadline:
            try:
                if client.get(url + "/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise RuntimeError("Customer container did not become healthy")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--base-image", default="python:3.11-slim")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    root = Path(__file__).resolve().parents[2]
    source = work / "customer source, fixture"
    (source / "src").mkdir(parents=True)
    (source / "agent_space").mkdir()
    (source / "agent_space" / "config.yaml").write_text("name: customer\n")
    (source / ".env.production").write_text(
        "EXCLUDED_FIXTURE_SECRET=must-not-be-in-image\n"
    )
    (source / "src" / "customer.py").write_text("""import os
from pathlib import Path
from ccfoundry_agent_kit import AgentManifest, ChatResponse, create_agent_app
base = Path(os.environ["CCFOUNDRY_AGENT_BASE_DIR"])
def answer(request, space):
    path = base / "workspace" / "messages"
    previous = path.read_text() if path.exists() else ""
    path.write_text(previous + request.message + "\\n")
    return ChatResponse(reply="customer:" + request.message, metadata={"uid":os.getuid(), "previous":previous})
app = create_agent_app(manifest=AgentManifest(name=os.getenv("ME_AGENT_NAME", "customer"), label="Customer fixture"), chat_handler=answer, agent_space_dir=base / "agent_space")
""")
    wheels = work / "wheels"
    wheels.mkdir()
    # This preparation is online. The image build below has network disabled.
    with (work / "wheel-download.log").open("w") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "-d",
                str(wheels),
                "setuptools>=69",
                "wheel",
                str(root / "packages" / "python-sdk"),
                "uvicorn>=0.30,<1.0",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    pip_config = source / "pip, secret.conf"
    pip_config.write_text(
        "[global]\nno-index = true\n# private-config-marker-must-not-be-in-image\n"
    )
    descriptor = {
        "version": 1,
        "app_module": "customer:app",
        "python": sys.executable,
        "build": {
            "python_image": args.base_image,
            "offline": True,
            "wheelhouse": str(wheels),
            "include": ["src", "agent_space", "pip, secret.conf", ".env.production"],
            "pip_config_file": str(pip_config),
        },
    }
    (source / "ccfoundry.project.json").write_text(json.dumps(descriptor))
    manager = LocalAgentManager(
        repo_root=root,
        runtime_dir=work / "board",
        agents_file=work / "board/agents.yaml",
        venv_python=Path(sys.executable),
        projects=[source],
    )
    unique = uuid.uuid4().hex[:12]
    name = "fixture_" + unique
    item = manager.create_agent(template_id=project_id(source), name=name)
    production_id = ""
    volume_name = ""
    registry_id = ""
    image_id = ""
    target = ""
    build_ref = "local/" + name.replace("_", "-") + ":test"
    report = {"fixture": unique, "checks": []}
    try:
        record = manager.containers.build(name, image_ref=build_ref, offline=True)
        image_id = record["image_id"]
        report["build"] = record
        assert record["offline"] and record["platform"] == "linux/amd64"
        report["checks"].append("offline-build-with-network-none-and-secret-mount")
        context = Path(record["manifest_path"]).parent / "context"
        assert not any(
            "private-config-marker" in p.read_text(errors="ignore")
            or "must-not-be-in-image" in p.read_text(errors="ignore")
            for p in context.rglob("*")
            if p.is_file() and p.suffix != ".whl"
        )
        inspection = json.loads(docker(["image", "inspect", image_id]))[0]
        assert inspection["Config"]["User"] == "10001:10001"
        assert "CCFOUNDRY_DEBUG_SECRET" not in "\n".join(inspection["Config"]["Env"])
        assert not docker(
            [
                "run",
                "--rm",
                "--entrypoint",
                "python",
                image_id,
                "-c",
                'from pathlib import Path; assert not Path("/app/project/.env.production").exists(); assert not Path("/app/project/pip, secret.conf").exists(); assert not Path("/run/secrets/pip_config").exists()',
            ]
        )
        report["checks"].append("secret-exclusion-image-and-context")
        started = manager.containers.start(name)
        ready(item["base_url"])
        secret = manager.containers.debug_secret(name, item["base_url"])
        with httpx.Client(
            base_url=item["base_url"], trust_env=False, timeout=10
        ) as client:
            assert (
                client.post("/api/chat", json={"message": "unauthorized"}).status_code
                == 401
            )
            reply = client.post(
                "/chat",
                json={"message": "first"},
                headers={"Authorization": "Bearer " + secret},
            )
            assert reply.status_code == 200, reply.text
            assert reply.json()["reply"] == "customer:first"
            assert reply.json()["metadata"]["uid"] != 0
        info = json.loads(docker(["container", "inspect", started["container_id"]]))[0]
        assert info["HostConfig"]["ReadonlyRootfs"]
        assert (
            info["HostConfig"]["PortBindings"]["8080/tcp"][0]["HostIp"] == "127.0.0.1"
        )
        assert info["HostConfig"]["LogConfig"]["Config"] == {
            "max-file": "2",
            "max-size": "10m",
        }
        report["checks"].append("loopback-nonroot-authenticated-customer-chat")
        manager.stop_agent(name)
        manager.containers.start(name)
        ready(item["base_url"])
        secret = manager.containers.debug_secret(name, item["base_url"])
        with httpx.Client(
            base_url=item["base_url"], trust_env=False, timeout=10
        ) as client:
            reply = client.post(
                "/chat",
                json={"message": "second"},
                headers={"Authorization": "Bearer " + secret},
            )
            assert (
                reply.status_code == 200
                and reply.json()["metadata"]["previous"] == "first\n"
            ), reply.text
        report["checks"].append("customer-data-survives-container-recreation")
        with httpx.Client(
            base_url=item["base_url"], trust_env=False, timeout=10
        ) as client:
            stream = client.post(
                "/api/chat",
                json={"message": "stream", "stream": True},
                headers={"Authorization": "Bearer " + secret},
            )
            assert (
                stream.status_code == 200
                and "text/event-stream" in stream.headers["content-type"]
            )
            assert "event: message" in stream.text and "customer:stream" in stream.text
        report["checks"].append("foundry-chat-sse-from-customer-code")
        manager.stop_agent(name)
        # Packaged AgentService mode: image default UID, no debug bearer,
        # empty persistent volume seeded once and retained across replacement.
        volume_name = "kit-fixture-state-" + unique
        docker(
            ["volume", "create", "--label", "ai.foundry.fixture=" + unique, volume_name]
        )
        for iteration in ["one", "two"]:
            production_id = docker(
                [
                    "run",
                    "-d",
                    "--name",
                    "kit-fixture-production-" + unique,
                    "--label",
                    "ai.foundry.fixture=" + unique,
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=64m",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--cpus",
                    "2",
                    "--memory",
                    "1g",
                    "--pids-limit",
                    "256",
                    "--mount",
                    "type=volume,src=" + volume_name + ",dst=/app/state",
                    "-e",
                    "AGENT_SECRET=isolated-production-fixture",
                    "-p",
                    "127.0.0.1::8080",
                    image_id,
                ]
            )
            production_info = json.loads(
                docker(["container", "inspect", production_id])
            )[0]
            production_port = production_info["NetworkSettings"]["Ports"]["8080/tcp"][
                0
            ]["HostPort"]
            production_url = "http://127.0.0.1:" + production_port
            ready(production_url)
            with httpx.Client(
                base_url=production_url, trust_env=False, timeout=10
            ) as client:
                assert client.get("/manifest").status_code == 401
                assert client.get("/foundry/bootstrap/state").status_code == 401
                assert (
                    client.post("/chat", json={"message": "no-secret"}).status_code
                    == 401
                )
                reply = client.post(
                    "/chat",
                    json={"message": iteration},
                    headers={"Authorization": "Bearer isolated-production-fixture"},
                )
                assert reply.status_code == 200, reply.text
                assert reply.json()["metadata"]["uid"] == 10001
                assert reply.json()["metadata"]["previous"] == (
                    "" if iteration == "one" else "one\n"
                )
            assert (
                docker(
                    ["exec", production_id, "cat", "/app/state/agent_space/config.yaml"]
                )
                == "name: customer"
            )
            docker(["container", "rm", "-f", production_id])
            production_id = ""
        report["checks"].append(
            "production-bearer-default-uid-and-seeded-persistent-state"
        )
        exported = manager.containers.export(name)
        report["export"] = exported
        assert Path(exported["archive_path"]).stat().st_size > 0
        docker(
            ["image", "load", "--input", exported["archive_path"]],
            timeout=300,
            log=work / "load.log",
        )
        assert json.loads(docker(["image", "inspect", image_id]))[0]["Id"] == image_id
        report["checks"].append("archive-load-preserves-image-id")
        registry_id = docker(
            [
                "run",
                "-d",
                "--name",
                "kit-test-registry-" + unique,
                "--label",
                "ai.foundry.fixture=" + unique,
                "-p",
                "127.0.0.1::5000",
                "registry:2",
            ],
            timeout=120,
        )
        port = json.loads(docker(["container", "inspect", registry_id]))[0][
            "NetworkSettings"
        ]["Ports"]["5000/tcp"][0]["HostPort"]
        target = f"127.0.0.1:{port}/customer/{unique}:test"
        with httpx.Client(trust_env=False) as client:
            for _ in range(60):
                try:
                    if client.get(f"http://127.0.0.1:{port}/v2/").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
        pushed = manager.containers.push(name, target)
        report["push"] = pushed
        with httpx.Client(trust_env=False) as client:
            response = client.get(
                f"http://127.0.0.1:{port}/v2/customer/{unique}/manifests/test",
                headers={
                    "Accept": "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"
                },
            )
            assert response.status_code == 200, response.text
            top_digest = response.headers["Docker-Content-Digest"]
            published = response.json()
            if "manifests" in published:
                matches = [
                    m
                    for m in published["manifests"]
                    if m.get("platform", {}).get("os") == "linux"
                    and m.get("platform", {}).get("architecture") == "amd64"
                ]
                assert len(matches) == 1
                leaf = client.get(
                    f"http://127.0.0.1:{port}/v2/customer/{unique}/manifests/{matches[0]['digest']}",
                    headers={
                        "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
                    },
                )
                assert leaf.status_code == 200
                published = leaf.json()
                leaf_digest = leaf.headers["Docker-Content-Digest"]
            else:
                leaf_digest = top_digest
            assert image_id in {top_digest, leaf_digest, published["config"]["digest"]}
            config = client.get(
                f"http://127.0.0.1:{port}/v2/customer/{unique}/blobs/{published['config']['digest']}"
            )
            assert config.status_code == 200
            assert config.json()["config"]["User"] == inspection["Config"]["User"]
            assert config.json()["rootfs"]["diff_ids"] == inspection["RootFS"]["Layers"]
            assert (
                pushed["repo_digest"].split("@")[1]
                == response.headers["Docker-Content-Digest"]
            )
        report["checks"].append("disposable-private-registry-real-digest-and-config")
        report["ok"] = True
    finally:
        try:
            _, current = manager.containers._item(name)
            if current.get("container_id"):
                docker(
                    ["container", "logs", "--tail", "100", current["container_id"]],
                    log=work / "container.log",
                )
        except Exception as exc:
            report["log_collection_error"] = str(exc)
        try:
            manager.containers.stop(name)
        except Exception as exc:
            report["cleanup_runtime_error"] = str(exc)
        if production_id:
            docker(
                ["container", "logs", "--tail", "100", production_id],
                log=work / "production.log",
            )
            docker(["container", "rm", "-f", production_id])
        if volume_name:
            docker(["volume", "rm", volume_name])
        if registry_id:
            docker(["container", "rm", "-f", registry_id])
        for ref in [target, build_ref]:
            if ref:
                try:
                    docker(["image", "rm", ref])
                except RuntimeError:
                    pass
        (work / "result.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "ok": True,
                "checks": report["checks"],
                "result": str(work / "result.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
