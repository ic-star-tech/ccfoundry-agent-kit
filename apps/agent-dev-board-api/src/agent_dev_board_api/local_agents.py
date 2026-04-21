from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:64].strip("_")


def _display_label(name: str) -> str:
    parts = [item for item in re.split(r"[_\-\s]+", name.strip()) if item]
    if not parts:
        return "Local Agent"
    return " ".join(part.capitalize() for part in parts)


def _is_pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_for_pid_exit(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.time() + max(0.1, timeout_seconds)
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return True
        time.sleep(0.1)
    return not _is_pid_running(pid)


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, preferred_port: int, reserved: set[int]) -> int:
    port = max(1024, int(preferred_port or 0) or 8085)
    while port < 65535:
        if port not in reserved and _port_is_free(host, port):
            return port
        port += 1
    raise RuntimeError(f"Could not find a free port starting from {preferred_port}")


class LocalAgentManager:
    def __init__(
        self,
        *,
        repo_root: Path,
        runtime_dir: Path,
        agents_file: Path,
        venv_python: Path,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runtime_dir = runtime_dir.resolve()
        self.agents_file = agents_file.resolve()
        self.venv_python = venv_python.expanduser()
        self.registry_path = self.runtime_dir / "local_agents.json"
        self.instances_dir = self.runtime_dir / "agents"
        self.templates = {
            "me_agent": {
                "id": "me_agent",
                "label": "Me Agent Template",
                "description": "Self-hosted personal agent example with Foundry bootstrap, sandbox helpers, and demo skills.",
                "agent_space_dir": self.repo_root / "examples" / "me_agent" / "agent_space",
                "app_dir": self.repo_root / "examples" / "me_agent" / "src",
                "app_module": "me_agent_example.app:app",
            }
        }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.instances_dir.mkdir(parents=True, exist_ok=True)

    def ensure_runtime_files(self) -> None:
        if not self.registry_path.exists():
            self._save_registry({"agents": []})
        self._sync_agents_file()

    def _load_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"agents": []}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return {"agents": []}
        if not isinstance(payload, dict):
            return {"agents": []}
        entries = payload.get("agents")
        if not isinstance(entries, list):
            payload["agents"] = []
        return payload

    def _save_registry(self, payload: dict[str, Any]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _sync_agents_file(self) -> None:
        registry = self._load_registry()
        entries: list[dict[str, Any]] = []
        for item in registry.get("agents", []):
            name = str(item.get("name") or "").strip()
            label = str(item.get("label") or "").strip()
            base_url = str(item.get("base_url") or "").strip()
            if not name or not label or not base_url:
                continue
            entries.append({
                "name": name,
                "label": label,
                "base_url": base_url,
            })
        self.agents_file.parent.mkdir(parents=True, exist_ok=True)
        self.agents_file.write_text(
            yaml.safe_dump({"agents": entries}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def list_templates(self) -> list[dict[str, str]]:
        return [
            {
                "id": value["id"],
                "label": value["label"],
                "description": value["description"],
            }
            for value in self.templates.values()
        ]

    def _refresh_agent_state(self, item: dict[str, Any]) -> dict[str, Any]:
        pid = int(item.get("pid") or 0) or None
        status = str(item.get("status") or "").strip().lower() or "stopped"
        if status == "running" and not _is_pid_running(pid):
            item["status"] = "stopped"
            item["pid"] = None
        if status == "starting" and not _is_pid_running(pid):
            item["status"] = "stopped"
            item["pid"] = None
        return item

    def list_agents(self) -> list[dict[str, Any]]:
        registry = self._load_registry()
        changed = False
        items: list[dict[str, Any]] = []
        for raw in registry.get("agents", []):
            item = dict(raw)
            before = json.dumps(item, sort_keys=True, ensure_ascii=False)
            item = self._refresh_agent_state(item)
            after = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if before != after:
                changed = True
            items.append(item)
        if changed:
            registry["agents"] = items
            self._save_registry(registry)
            self._sync_agents_file()
        return items

    def _find_agent(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        registry = self._load_registry()
        normalized = _slugify(name)
        for item in registry.get("agents", []):
            if _slugify(item.get("name")) == normalized:
                refreshed = self._refresh_agent_state(dict(item))
                return registry, refreshed
        raise ValueError(f"Unknown local agent '{name}'")

    def _replace_agent(self, registry: dict[str, Any], updated: dict[str, Any]) -> None:
        normalized = _slugify(updated.get("name"))
        items: list[dict[str, Any]] = []
        replaced = False
        for item in registry.get("agents", []):
            if _slugify(item.get("name")) == normalized:
                items.append(updated)
                replaced = True
            else:
                items.append(item)
        if not replaced:
            items.append(updated)
        registry["agents"] = items
        self._save_registry(registry)
        self._sync_agents_file()

    def _instance_dir(self, name: str) -> Path:
        return self.instances_dir / _slugify(name)

    def _template(self, template_id: str) -> dict[str, Any]:
        template = self.templates.get(str(template_id or "").strip())
        if not template:
            raise ValueError(f"Unknown template '{template_id}'")
        return template

    def _copy_template(self, template: dict[str, Any], destination: Path) -> None:
        agent_space_src = Path(template["agent_space_dir"]).resolve()
        if not agent_space_src.exists():
            raise RuntimeError(f"Template agent_space not found: {agent_space_src}")
        shutil.copytree(
            agent_space_src,
            destination / "agent_space",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".foundry_bootstrap.json",
                ".foundry_bootstrap.*.json",
                "runtime",
                "__pycache__",
                "*.pyc",
                "*.pyo",
                "*.jsonl",
            ),
        )
        workspace_dir = destination / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        gitkeep = workspace_dir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")

    def _write_env_file(self, item: dict[str, Any]) -> Path:
        instance_dir = Path(item["instance_dir"])
        env_path = instance_dir / ".env"
        foundry_url = str(item.get("foundry_url") or "").strip()
        lines = [
            f"ME_AGENT_NAME={item['name']}",
            f"ME_AGENT_LABEL={item['label']}",
            "FOUNDRY_DISCOVERY_ENABLE=true",
            f"FOUNDRY_BASE_URL={foundry_url}",
            f"FOUNDRY_AGENT_PUBLIC_URL={item['base_url']}",
            "FOUNDRY_RUNTIME_TRANSPORT=pull",
            "FOUNDRY_BOOTSTRAP_DELIVERY=poll",
            f"FOUNDRY_BOOTSTRAP_STATE_PATH={instance_dir / '.foundry_bootstrap.json'}",
        ]
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return env_path

    def _spawn_agent(self, item: dict[str, Any]) -> dict[str, Any]:
        instance_dir = Path(item["instance_dir"])
        template = self._template(item["template_id"])
        logs_dir = instance_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "agent.log"
        env_path = self._write_env_file(item)
        pythonpath_entries = [
            str(Path(template["app_dir"]).resolve()),
            str((self.repo_root / "packages" / "python-sdk" / "src").resolve()),
        ]
        existing_pythonpath = os.environ.get("PYTHONPATH", "").strip()
        if existing_pythonpath:
            pythonpath_entries.append(existing_pythonpath)
        log_handle = log_path.open("ab")
        process = subprocess.Popen(
            [
                str(self.venv_python),
                "-m",
                "uvicorn",
                str(template["app_module"]),
                "--app-dir",
                str(template["app_dir"]),
                "--host",
                str(item.get("host") or "127.0.0.1"),
                "--port",
                str(item.get("port")),
            ],
            cwd=str(self.repo_root),
            env={
                **os.environ,
                "ME_AGENT_BASE_DIR": str(instance_dir),
                "VIRTUAL_ENV": str(self.venv_python.parent.parent),
                "PATH": f"{self.venv_python.parent}:{os.environ.get('PATH', '')}",
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": os.pathsep.join(entry for entry in pythonpath_entries if entry),
            },
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_handle.close()
        item["pid"] = process.pid
        item["status"] = "running"
        item["env_path"] = str(env_path)
        item["log_path"] = str(log_path)
        item["started_at"] = _utcnow_iso()
        return item

    def create_agent(
        self,
        *,
        template_id: str,
        name: str,
        label: str = "",
        preferred_port: int | None = None,
        foundry_url: str = "",
    ) -> dict[str, Any]:
        template = self._template(template_id)
        normalized_name = _slugify(name)
        if not normalized_name:
            raise ValueError("Agent name is required")

        registry = self._load_registry()
        for existing in registry.get("agents", []):
            if _slugify(existing.get("name")) == normalized_name:
                raise ValueError(f"Local agent '{normalized_name}' already exists")

        reserved_ports = {
            int(item.get("port") or 0)
            for item in registry.get("agents", [])
            if int(item.get("port") or 0) > 0
        }
        port = _pick_port("127.0.0.1", int(preferred_port or 8085), reserved_ports)
        instance_dir = self._instance_dir(normalized_name)
        instance_dir.mkdir(parents=True, exist_ok=True)
        self._copy_template(template, instance_dir)

        item = {
            "name": normalized_name,
            "label": str(label or "").strip() or _display_label(normalized_name),
            "template_id": template["id"],
            "host": "127.0.0.1",
            "port": port,
            "base_url": f"http://127.0.0.1:{port}",
            "instance_dir": str(instance_dir),
            "foundry_url": str(foundry_url or "").strip(),
            "created_at": _utcnow_iso(),
            "pid": None,
            "status": "stopped",
        }
        item = self._spawn_agent(item)
        registry.setdefault("agents", []).append(item)
        self._save_registry(registry)
        self._sync_agents_file()
        return item

    def start_agent(self, name: str) -> dict[str, Any]:
        registry, item = self._find_agent(name)
        if item.get("status") == "running" and _is_pid_running(int(item.get("pid") or 0)):
            return item
        item = self._spawn_agent(item)
        self._replace_agent(registry, item)
        return item

    def stop_agent(self, name: str) -> dict[str, Any]:
        registry, item = self._find_agent(name)
        pid = int(item.get("pid") or 0)
        if pid > 0 and _is_pid_running(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            if not _wait_for_pid_exit(pid, timeout_seconds=5.0):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except Exception:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
                _wait_for_pid_exit(pid, timeout_seconds=2.0)
        item["pid"] = None
        item["status"] = "stopped"
        item["stopped_at"] = _utcnow_iso()
        self._replace_agent(registry, item)
        return item
