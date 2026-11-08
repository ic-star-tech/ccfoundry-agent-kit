"""Build and run explicit customer projects using the operator's local Docker."""

from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import platform as host_platform
import re
import secrets
import shutil
import stat
import subprocess
import threading
import uuid
from pathlib import Path

import yaml
from ccfoundry_agent_kit.agentservice_manifest import validate_local_manifest

from .projects import MANIFEST

IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
BASE_IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*(?:@sha256:[0-9a-f]{64})?")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
EXCLUDED = {
    ".git",
    ".venv",
    "venv",
    ".dev-board",
    "node_modules",
    "__pycache__",
    ".ssh",
    ".aws",
    ".azure",
    "credentials",
    "secrets",
    "runtime",
    "logs",
    "project-builds",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "pip.conf",
    "pip.ini",
    ".container-secret",
    ".container.env",
    MANIFEST,
}


def excluded(path: Path) -> bool:
    return any(
        p in EXCLUDED
        or p.startswith((".env", ".foundry_", "id_rsa", "id_ed25519"))
        or p.endswith(
            (
                ".pem",
                ".key",
                ".p12",
                ".pfx",
                ".jks",
                ".pyc",
                ".pyo",
                ".jsonl",
                ".egg-info",
            )
        )
        for p in path.parts
    )


def docker_csv(*values: str) -> str:
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerow(values)
    return output.getvalue().rstrip("\n")


def private_runtime_file(path: Path, value: str) -> None:
    # Runtime files are writable by the previous container. Do not follow a
    # link it left behind when an operator restarts it with greater authority.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        info = os.fstat(output.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(
                "Container runtime credentials must be private regular files"
            )
        os.fchmod(output.fileno(), 0o600)
        os.ftruncate(output.fileno(), 0)
        output.write(value)


def selected_files(
    root: Path, includes: list[str], secrets_paths: set[Path]
) -> list[Path]:
    files: set[Path] = set()
    for name in includes:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not name:
            raise ValueError("Build includes must stay inside the customer project")
        source = root / relative
        if excluded(relative):
            continue
        if not source.exists():
            raise ValueError(f"Build input does not exist: {name}")
        candidates = [source]
        if source.is_dir():
            candidates = []
            for directory, dirs, names in os.walk(source, followlinks=False):
                parent = Path(directory)
                dirs[:] = [
                    d for d in dirs if not excluded((parent / d).relative_to(root))
                ]
                if parent.is_symlink() or any((parent / d).is_symlink() for d in dirs):
                    raise ValueError("Build input contains a directory symlink")
                candidates.extend(parent / n for n in names)
        for path in candidates:
            rel = path.relative_to(root)
            if excluded(rel) or path.resolve() in secrets_paths:
                continue
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(root)
                or not path.is_file()
            ):
                raise ValueError(
                    "Build input must contain regular files inside the project"
                )
            files.add(rel)
    if (
        len(files) > 20000
        or sum((root / p).stat().st_size for p in files) > 512 * 1024 * 1024
    ):
        raise ValueError(
            "Build context exceeds 20,000 files or 512 MiB; narrow build.include"
        )
    return sorted(files)


def copy_snapshot(
    root: Path,
    includes: list[str],
    destination: Path,
    secret_paths: set[Path] | None = None,
) -> dict[str, str]:
    secret_paths = secret_paths or set()
    names = selected_files(root, includes, secret_paths)
    destination.mkdir(parents=True, exist_ok=True)
    for name in includes:
        if (root / name).is_dir() and not excluded(Path(name)):
            (destination / name).mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in names:
        data = (root / name).read_bytes()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if (root / name).stat().st_mode & 0o111 else 0o644)
        hashes[str(name)] = hashlib.sha256(data).hexdigest()
    # The job directory is private on the host. Source trees inside an image
    # must still be traversable by its non-root runtime, regardless of umask.
    for directory in [destination, *(p for p in destination.rglob("*") if p.is_dir())]:
        directory.chmod(0o755)
    if selected_files(root, includes, secret_paths) != names or any(
        hashlib.sha256((root / name).read_bytes()).hexdigest() != h
        for name, h in hashes.items()
    ):
        raise RuntimeError(
            "Project changed during snapshot; retry with stable source files"
        )
    return hashes


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(
            str(path.relative_to(root)).encode()
            + b"\0"
            + hashlib.sha256(path.read_bytes()).digest()
        )
    return digest.hexdigest()


def docker(args: list[str], *, timeout: int = 30, log: Path | None = None) -> str:
    try:
        if log is not None:
            with log.open("ab") as output:
                log.chmod(0o600)
                result = subprocess.run(
                    ["docker", *args],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
            if result.returncode:
                raise RuntimeError(
                    f"Docker operation failed; inspect the private log at {log}"
                )
            return ""
        result = subprocess.run(
            ["docker", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Docker is required for customer container operations"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Docker operation timed out; inspect Docker state before retrying"
        ) from exc
    if result.returncode:
        raise RuntimeError(
            "Docker operation failed; check the selected local Docker engine"
        )
    return result.stdout.strip()


class ProjectContainers:
    def __init__(self, agents):
        self.agents = agents
        self.lock = threading.RLock()
        self.builds = agents.runtime_dir / "project-builds"

    def _item(self, name: str):
        registry = self.agents._load_registry()
        item = next(
            (dict(a) for a in registry.get("agents", []) if a.get("name") == name), None
        )
        if item is None or item.get("template_id") not in self.agents.project_roots:
            raise ValueError("Select a configured customer project source")
        self.agents._validated_instance_dir(item)
        self.agents._validated_runtime_port(item)
        self.agents._validated_runtime_host(item)
        if item.get("status") == "retired":
            raise ValueError("Retired source names cannot run or publish containers")
        return registry, item

    def _record(self, item):
        build_id = str(item.get("project_build_id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", build_id):
            raise ValueError(
                "Build this customer source before running or exporting its image"
            )
        directory = self.builds / build_id
        record = json.loads((directory / "result.json").read_text())
        if record.get("source_id") != item["source_id"] or not IMAGE_ID.fullmatch(
            record.get("image_id", "")
        ):
            raise RuntimeError("Container build receipt does not match this source")
        return record, directory

    def build(
        self,
        name: str,
        *,
        image_ref: str = "",
        platform: str = "",
        offline: bool | None = None,
    ):
        with self.lock:
            registry, item = self._item(name)
            project = self.agents._validated_template(item["template_id"])
            root, config = project["project_root"], project["build"]
            version = str(config.get("version") or "0.1.0")
            slug = name.replace("_", "-")
            image_ref = image_ref or f"local/{slug}:{version}"
            if not IMAGE.fullmatch(image_ref) or len(image_ref) > 255:
                raise ValueError(
                    "Image reference must be a repository:tag without credentials or command options"
                )
            platform = platform or (
                "linux/arm64"
                if host_platform.machine().lower() in {"aarch64", "arm64"}
                else "linux/amd64"
            )
            if platform not in {"linux/amd64", "linux/arm64"}:
                raise ValueError("Choose linux/amd64 or linux/arm64")
            offline = bool(config.get("offline", False)) if offline is None else offline
            base = str(config.get("python_image") or "python:3.12-slim")
            if not BASE_IMAGE.fullmatch(base) or len(base) > 255:
                raise ValueError("build.python_image must be a Docker image reference")
            secret_paths: dict[str, Path] = {}
            for key in ["pip_config", "pip_ca"]:
                value = config.get(key + "_file")
                if value:
                    path = Path(value).expanduser()
                    path = (root / path if not path.is_absolute() else path).resolve(
                        strict=True
                    )
                    if not path.is_file():
                        raise ValueError(f"build.{key}_file must be a regular file")
                    secret_paths[key] = path
            includes = config.get("include")
            if includes is None:
                includes = [
                    str(project["app_dir"].relative_to(root)),
                    str(project["agent_space_dir"].relative_to(root)),
                ]
                includes += [
                    n
                    for n in ["pyproject.toml", "README.md", "requirements.txt"]
                    if (root / n).is_file()
                ]
            if not isinstance(includes, list) or not all(
                isinstance(n, str) for n in includes
            ):
                raise ValueError(
                    "build.include must be an array of relative source paths"
                )
            self.manifest(item, project, "sha256:" + "0" * 64, version)
            try:
                docker(["buildx", "version"])
            except RuntimeError as exc:
                raise RuntimeError(
                    "Customer image builds require Docker Buildx with BuildKit; prepare it before building"
                ) from exc
            job_id = uuid.uuid4().hex
            directory = self.builds / job_id
            context = directory / "context"
            context.mkdir(parents=True, mode=0o700)
            directory.chmod(0o700)
            files = copy_snapshot(
                root, includes, context / "project", set(secret_paths.values())
            )
            for required in [project["app_dir"], project["agent_space_dir"]]:
                if not (context / "project" / required.relative_to(root)).is_dir():
                    raise ValueError(
                        "Build includes must contain the declared app_dir and agent_space"
                    )
            sdk_root = self.agents.repo_root / "packages" / "python-sdk"
            sdk_inputs = ["pyproject.toml", "README.md", "src"]
            if (sdk_root / "LICENSE").is_file():
                sdk_inputs.append("LICENSE")
            copy_snapshot(sdk_root, sdk_inputs, context / "sdk")
            copy_snapshot(
                project["agent_space_dir"],
                ["."],
                context / "agent_space",
                set(secret_paths.values()),
            )
            (context / "wheels").mkdir()
            wheelhouse = config.get("wheelhouse")
            if wheelhouse:
                wheels = Path(wheelhouse).expanduser()
                wheels = (
                    root / wheels if not wheels.is_absolute() else wheels
                ).resolve(strict=True)
                wheel_names = [
                    p.name
                    for p in wheels.iterdir()
                    if p.is_file() and p.suffix == ".whl"
                ]
                copy_snapshot(wheels, wheel_names, context / "wheels")
            if offline:
                if not list((context / "wheels").glob("*.whl")):
                    raise ValueError(
                        "Offline image builds require build.wheelhouse with all runtime and build dependency wheels"
                    )
                # Missing base images fail instead of intentionally pulling them.
                docker(["image", "inspect", base])
            shutil.copyfile(
                Path(__file__).with_name("project_runtime.py"),
                context / "project_runtime.py",
            )
            (context / "project-runtime.json").write_text(
                json.dumps(
                    {
                        "app_module": project["app_module"],
                        "app_dir": str(project["app_dir"].relative_to(root)),
                    }
                )
            )
            (context / "project_runtime.py").chmod(0o644)
            (context / "project-runtime.json").chmod(0o644)
            mounts = " ".join(
                f"--mount=type=secret,id={key},required=true" for key in secret_paths
            )
            env = " ".join(
                f"{key}=/run/secrets/{value}"
                for key, value in [
                    ("PIP_CONFIG_FILE", "pip_config"),
                    ("PIP_CERT", "pip_ca"),
                ]
                if value in secret_paths
            )
            pip_options = "--no-index --find-links=/wheels" if offline else ""
            install_project = (
                "/build/project"
                if (context / "project" / "pyproject.toml").is_file()
                else ""
            )
            if (context / "project" / "requirements.txt").is_file():
                install_project += " -r /build/project/requirements.txt"
            dockerfile = f"""FROM {base} AS builder
WORKDIR /build
COPY sdk /build/sdk
COPY project /build/project
COPY wheels /wheels
RUN {mounts} {env} python -m pip install --no-cache-dir --prefix=/install {pip_options} /build/sdk {install_project} 'uvicorn>=0.30,<1.0'
FROM {base}
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FOUNDRY_DISCOVERY_ENABLE=false ME_AGENT_BASE_DIR=/app/state CCFOUNDRY_AGENT_BASE_DIR=/app/state HOME=/app/state
COPY --from=builder /install /usr/local
COPY project /app/project
COPY project_runtime.py /app/project_runtime.py
COPY project-runtime.json /app/project-runtime.json
COPY agent_space /app/agent_space.seed
RUN mkdir -p /app/state && chown 10001:10001 /app/state
WORKDIR /app/project
USER 10001:10001
EXPOSE 8080
CMD ["python","-m","uvicorn","project_runtime:create_app","--factory","--app-dir","/app","--host","0.0.0.0","--port","8080"]
"""
            (context / "Dockerfile").write_text(dockerfile)
            if (
                project["manifest_sha256"]
                != hashlib.sha256((root / MANIFEST).read_bytes()).hexdigest()
            ):
                raise RuntimeError("Project manifest changed during build preparation")
            snapshot = tree_digest(context)
            args = [
                "buildx",
                "build",
                "--load",
                "--pull=false",
                "--platform",
                platform,
                "--iidfile",
                str(directory / "image-id"),
                "-t",
                image_ref,
            ]
            if offline:
                args += ["--network=none"]
            for key, path in secret_paths.items():
                args += ["--secret", docker_csv(f"id={key}", f"src={path}")]
            docker([*args, str(context)], timeout=900, log=directory / "build.log")
            image_id = (directory / "image-id").read_text().strip()
            if not IMAGE_ID.fullmatch(image_id) or tree_digest(context) != snapshot:
                raise RuntimeError(
                    "Image build did not produce a stable snapshot and image receipt"
                )
            actual_platform = json.loads(docker(["image", "inspect", image_id]))[0]
            if f"{actual_platform['Os']}/{actual_platform['Architecture']}" != platform:
                raise RuntimeError(
                    "Built image architecture differs from the requested platform"
                )
            record = {
                "build_id": job_id,
                "source_id": item["source_id"],
                "agent_name": name,
                "image_ref": image_ref,
                "image_id": image_id,
                "platform": platform,
                "offline": offline,
                "context_sha256": snapshot,
                "source_files": files,
                "project_manifest_sha256": project["manifest_sha256"],
            }
            manifest = self.manifest(item, project, image_id, version)
            (directory / "agentservice.yaml").write_text(
                yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
            )
            (directory / "result.json").write_text(json.dumps(record, indent=2))
            (directory / "result.json").chmod(0o600)
            # A build can take minutes: preserve concurrent source changes and
            # never resurrect a source retired while Docker was working.
            registry, current = self._item(name)
            if current["source_id"] != item["source_id"]:
                raise RuntimeError("Source changed during image build")
            item = current
            item["project_build_id"] = job_id
            self.agents._replace_agent(registry, item)
            return {
                **record,
                "manifest_path": str(directory / "agentservice.yaml"),
                "log_path": str(directory / "build.log"),
            }

    @staticmethod
    def manifest(item, project, image_id, version):
        name = item["name"].replace("_", "-")
        config_env = project["build"].get("config_env", [])
        if not isinstance(config_env, list) or not all(
            isinstance(x, str) and ENV_NAME.fullmatch(x) for x in config_env
        ):
            raise ValueError("build.config_env must list environment variable names")
        value = {
            "apiVersion": "foundry.ai/v1",
            "kind": "AgentService",
            "metadata": {
                "name": "customer-" + name[:44] + "-" + item["source_id"][:8],
                "version": version,
                "displayName": item["label"],
                "description": project["description"],
                "author": "Customer",
                "category": "productivity",
            },
            "spec": {
                "tier": "local",
                "zone": "GREEN",
                "image": {
                    "repository": "local/" + name,
                    "tag": version,
                    "digest": image_id,
                },
                "runtime": {
                    "runner": "docker",
                    "container_name": "foundry-customer-" + name,
                    "port": 8080,
                    "protocol": "http",
                    "endpoint_path": "/",
                    "healthcheck_path": "/health",
                    "additional_networks": ["foundry_agent_llm_net"],
                    "persistent_volumes": [
                        {
                            "subpath": "state",
                            "container_path": "/app/state",
                            "uid": 10001,
                            "gid": 10001,
                            "mode": "0700",
                        }
                    ],
                    "environment": {
                        "ME_AGENT_NAME": item["name"],
                        "ME_AGENT_LABEL": item["label"],
                        "ME_AGENT_BASE_DIR": "/app/state",
                        "CCFOUNDRY_AGENT_BASE_DIR": "/app/state",
                    },
                    "config_env": list(dict.fromkeys(["AGENT_SECRET", *config_env])),
                },
                "agent": {
                    "agent_name": item["name"],
                    "label": item["label"],
                    "interaction_modes": ["chat"],
                },
                "access": {
                    "required_plan": "advanced",
                    "required_features": ["marketplace_advanced_agents"],
                },
                "config_schema": [
                    {
                        "key": key,
                        "label": key,
                        "type": "secret"
                        if any(s in key for s in ["KEY", "TOKEN", "SECRET", "PASSWORD"])
                        else "string",
                        "required": False,
                    }
                    for key in config_env
                    if key != "AGENT_SECRET"
                ],
            },
        }
        errors = validate_local_manifest(yaml.safe_dump(value))
        if errors:
            raise ValueError(
                "Generated AgentService manifest is invalid: " + "; ".join(errors)
            )
        return value

    def _inspect(self, item):
        cid = str(item.get("container_id") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", cid):
            # Recover a create that completed just before a client timeout or
            # board restart. Never adopt by name alone.
            raw = docker(
                [
                    "container",
                    "ls",
                    "-a",
                    "--no-trunc",
                    "--filter",
                    "name=^/ccfoundry-dev-" + item["source_id"] + "$",
                    "--format",
                    "{{.ID}}",
                ]
            )
            if not raw:
                return None
            if not re.fullmatch(r"[0-9a-f]{64}", raw):
                raise RuntimeError("Ambiguous customer container ownership")
            cid = raw
        else:
            raw = docker(
                [
                    "container",
                    "ls",
                    "-a",
                    "--no-trunc",
                    "--filter",
                    "id=" + cid,
                    "--format",
                    "{{.ID}}",
                ]
            )
            if cid not in raw.splitlines():
                return None
        info = json.loads(docker(["container", "inspect", cid]))[0]
        if (
            info.get("Config", {}).get("Labels", {}).get("ai.foundry.dev.source")
            != item["source_id"]
        ):
            raise RuntimeError("Container ownership label does not match this source")
        item["container_id"] = cid
        return info

    def status(self, item):
        info = self._inspect(item)
        return "running" if info and info["State"]["Running"] else "stopped"

    def start(self, name: str):
        with self.lock:
            registry, item = self._item(name)
            host = os.getenv("DOCKER_HOST") or docker(
                ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
            )
            if not host.startswith("unix://"):
                raise ValueError(
                    "Local container debug requires a local Docker Unix socket"
                )
            info = self._inspect(item)
            if info and info["State"]["Running"]:
                item.update(
                    {"runtime_kind": "container", "status": "running", "pid": None}
                )
                self.agents._replace_agent(registry, item)
                return item
            if info:
                docker(["container", "rm", item["container_id"]])
            from .local_agents import _is_pid_running

            if _is_pid_running(int(item.get("pid") or 0)):
                raise ValueError(
                    "Stop the local Python runtime before starting a container"
                )
            record, _ = self._record(item)
            uid, gid = (os.getuid(), os.getgid()) if os.getuid() else (10001, 10001)
            instance = self.agents._validated_instance_dir(item)
            if os.getuid() == 0:
                for path in instance.rglob("*"):
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode) or (
                        not path.is_dir()
                        and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
                    ):
                        raise ValueError(
                            "Root-operated container restart requires a runtime tree without links or special files"
                        )
            secret = secrets.token_hex(32)
            secret_path = instance / ".container-secret"
            private_runtime_file(secret_path, secret)
            values = self.agents.runtime_environment(item)
            values.update(
                {
                    "ME_AGENT_BASE_DIR": "/workspace",
                    "CCFOUNDRY_AGENT_BASE_DIR": "/workspace",
                    "HOME": "/workspace",
                    "FOUNDRY_BOOTSTRAP_STATE_PATH": "/workspace/.foundry_bootstrap.json",
                    "AGENT_SECRET": secret,
                    "CCFOUNDRY_DEBUG_SECRET": secret,
                }
            )
            if any("\n" in v or "\r" in v for v in values.values()):
                raise ValueError("Container runtime settings cannot contain newlines")
            env_file = instance / ".container.env"
            private_runtime_file(
                env_file, "\n".join(f"{k}={v}" for k, v in values.items()) + "\n"
            )
            if os.getuid() == 0:
                for parent, dirs, files in os.walk(instance, followlinks=False):
                    for p in [
                        Path(parent),
                        *(Path(parent) / n for n in [*dirs, *files]),
                    ]:
                        os.chown(p, uid, gid, follow_symlinks=False)
            cid = docker(
                [
                    "create",
                    "--name",
                    "ccfoundry-dev-" + item["source_id"],
                    "--label",
                    "ai.foundry.dev.source=" + item["source_id"],
                    "--user",
                    f"{uid}:{gid}",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=64m",
                    "--cpus",
                    "2",
                    "--memory",
                    "1g",
                    "--pids-limit",
                    "256",
                    "--log-opt",
                    "max-size=10m",
                    "--log-opt",
                    "max-file=2",
                    "--env-file",
                    str(env_file),
                    "--mount",
                    docker_csv("type=bind", f"src={instance}", "dst=/workspace"),
                    "-p",
                    f"127.0.0.1:{item['port']}:8080",
                    record["image_id"],
                ],
                timeout=60,
            )
            if not re.fullmatch(r"[0-9a-f]{64}", cid):
                raise RuntimeError("Docker did not return a container ID")
            item.update(
                {
                    "runtime_kind": "container",
                    "container_id": cid,
                    "container_image_id": record["image_id"],
                    "status": "stopped",
                    "pid": None,
                }
            )
            self.agents._replace_agent(registry, item)
            docker(["container", "start", cid], timeout=60)
            item["status"] = self.status(item)
            self.agents._replace_agent(registry, item)
            return item

    def stop(self, name: str):
        with self.lock:
            registry, item = self._item(name)
            if self._inspect(item):
                docker(
                    ["container", "stop", "--time", "10", item["container_id"]],
                    timeout=30,
                )
                docker(["container", "rm", item["container_id"]])
            item.update({"status": "stopped", "container_id": "", "pid": None})
            self.agents._replace_agent(registry, item)
            return item

    def debug_secret(self, name: str, url: str) -> str:
        _, item = self._item(name)
        if item.get("runtime_kind") != "container" or item.get("base_url", "").rstrip(
            "/"
        ) != url.rstrip("/"):
            return ""
        path = self.agents._validated_instance_dir(item) / ".container-secret"
        return path.read_text().strip() if path.is_file() else ""

    def receipt(self, name: str):
        _, item = self._item(name)
        if not item.get("project_build_id"):
            return {}
        record, directory = self._record(item)
        return {
            **record,
            "manifest_path": str(directory / "agentservice.yaml"),
            "log_path": str(directory / "build.log"),
        }

    def export(self, name: str):
        with self.lock:
            _, item = self._item(name)
            record, directory = self._record(item)
            archive = directory / "image.tar"
            docker(
                ["image", "save", "--output", str(archive), record["image_id"]],
                timeout=300,
                log=directory / "export.log",
            )
            archive.chmod(0o600)
            checksum = self._file_hash(archive)
            result = {
                "archive_path": str(archive),
                "sha256": checksum,
                "image_id": record["image_id"],
                "platform": record["platform"],
                "manifest_path": str(directory / "agentservice.yaml"),
            }
            (directory / "export.json").write_text(json.dumps(result, indent=2))
            return result

    @staticmethod
    def _file_hash(path):
        h = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    def push(self, name: str, target_ref: str):
        with self.lock:
            _, item = self._item(name)
            record, directory = self._record(item)
            host = target_ref.split("/", 1)[0]
            if (
                not IMAGE.fullmatch(target_ref)
                or len(target_ref) > 255
                or "/" not in target_ref
                or not ("." in host or ":" in host or host == "localhost")
            ):
                raise ValueError("Enter an explicit registry host/repository:tag")
            docker(["image", "tag", record["image_id"], target_ref])
            if (
                json.loads(docker(["image", "inspect", target_ref]))[0]["Id"]
                != record["image_id"]
            ):
                raise RuntimeError(
                    "Registry tag changed before push; no push was attempted"
                )
            push_log = directory / ("push-" + uuid.uuid4().hex + ".log")
            docker(["image", "push", target_ref], timeout=900, log=push_log)
            info = json.loads(docker(["image", "inspect", record["image_id"]]))[0]
            repository = target_ref.rsplit(":", 1)[0]
            if (
                json.loads(docker(["image", "inspect", target_ref]))[0]["Id"]
                != record["image_id"]
            ):
                raise RuntimeError(
                    "Registry tag changed during push; verify the remote tag before use"
                )
            pushed = re.findall(
                r"\bdigest:\s+(sha256:[0-9a-f]{64})\b", push_log.read_text()
            )
            digest = repository + "@" + pushed[-1] if pushed else ""
            if not digest or digest not in (info.get("RepoDigests") or []):
                raise RuntimeError(
                    "Registry push completed without a verifiable repository digest"
                )
            result = {
                "image_ref": target_ref,
                "image_id": record["image_id"],
                "repo_digest": digest,
                "platform": record["platform"],
                "log_path": str(push_log),
            }
            (directory / "push.json").write_text(json.dumps(result, indent=2))
            return result
