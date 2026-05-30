from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_command(args: list[str], *, timeout: float = 12.0) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"
    except Exception as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _json_command(args: list[str], *, timeout: float = 12.0) -> Any:
    code, stdout, _ = _run_command(args, timeout=timeout)
    if code != 0 or not stdout:
        return None
    try:
        return json.loads(stdout)
    except Exception:
        return None


def _read_gcloud_config() -> dict[str, str]:
    config_root = Path(os.getenv("CLOUDSDK_CONFIG", "") or Path.home() / ".config" / "gcloud")
    active_name = "default"
    active_path = config_root / "active_config"
    if active_path.exists():
        try:
            active_name = active_path.read_text(encoding="utf-8").strip() or active_name
        except Exception:
            active_name = "default"
    config_path = config_root / "configurations" / f"config_{active_name}"
    if not config_path.exists():
        return {}
    parser = ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
    except Exception:
        return {}
    result = {"active_config": active_name}
    if parser.has_section("core"):
        result["account"] = parser.get("core", "account", fallback="").strip()
        result["project"] = parser.get("core", "project", fallback="").strip()
    if parser.has_section("run"):
        result["region"] = parser.get("run", "region", fallback="").strip()
    return result


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _tail_logs(logs: list[str], limit: int = 500) -> list[str]:
    if len(logs) <= limit:
        return logs
    return logs[-limit:]


class CloudRunManager:
    """Small Cloud Run deployment adapter for Agent Dev Board."""

    def __init__(self, *, repo_root: Path, runtime_dir: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.runtime_dir = runtime_dir.resolve()
        self.script_path = self.repo_root / "scripts" / "deploy-cloudrun.sh"
        self.jobs_dir = self.runtime_dir / "cloud_run_deployments"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        gcloud_path = shutil.which("gcloud")
        docker_path = shutil.which("docker")
        gcloud_version = ""
        docker_version = ""
        active_account = ""
        accounts: list[dict[str, Any]] = []
        project = ""
        region = ""
        errors: list[str] = []

        if gcloud_path:
            _, gcloud_version, version_err = _run_command(["gcloud", "--version"], timeout=5)
            if version_err:
                errors.append(version_err)
            file_config = _read_gcloud_config()
            active_account = file_config.get("account", "")
            project = file_config.get("project", "")
            region = file_config.get("region", "")
            if active_account:
                accounts = [{"account": active_account, "status": "ACTIVE"}]
            else:
                raw_accounts = _json_command(["gcloud", "auth", "list", "--format=json"], timeout=5)
                if isinstance(raw_accounts, list):
                    accounts = [item for item in raw_accounts if isinstance(item, dict)]
                    active = next((item for item in accounts if item.get("status") == "ACTIVE"), {})
                    active_account = str(active.get("account") or "").strip()
        else:
            errors.append("gcloud CLI is not installed or not on PATH")

        if docker_path:
            _, docker_version, docker_err = _run_command(["docker", "--version"], timeout=8)
            if docker_err:
                errors.append(docker_err)
        else:
            errors.append("docker is not installed or not on PATH")

        return {
            "ok": bool(gcloud_path and docker_path and active_account),
            "gcloud": {
                "installed": bool(gcloud_path),
                "path": gcloud_path or "",
                "version": gcloud_version.splitlines()[0] if gcloud_version else "",
                "active_account": active_account,
                "accounts": accounts,
                "project": project,
                "region": region,
                "authenticated": bool(active_account),
            },
            "docker": {
                "installed": bool(docker_path),
                "path": docker_path or "",
                "version": docker_version,
            },
            "defaults": {
                "project": project or "glassy-fort-497911-u3",
                "region": region or "us-central1",
                "artifact_repo": "ccfoundry-agents",
            },
            "commands": {
                "login": "gcloud auth login",
                "set_project": "gcloud config set project <project-id>",
                "configure_docker": "gcloud auth configure-docker <region>-docker.pkg.dev --quiet",
            },
            "errors": errors,
        }

    def list_deployments(self, *, limit: int = 20) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for path in sorted(self.jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                payload["logs"] = _tail_logs(list(payload.get("logs") or []), 80)
                jobs.append(payload)
            if len(jobs) >= limit:
                break
        return jobs

    def get_deployment(self, job_id: str) -> dict[str, Any]:
        normalized = str(job_id or "").strip()
        with self._lock:
            job = self._jobs.get(normalized)
            if job:
                return dict(job)
        path = self._job_path(normalized)
        if not path.exists():
            raise ValueError(f"Unknown Cloud Run deployment '{normalized}'")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Cloud Run deployment '{normalized}' is unreadable") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Cloud Run deployment '{normalized}' is invalid")
        return payload

    def start_deployment(
        self,
        *,
        agent_name: str,
        instance_dir: Path,
        foundry_url: str,
        project: str,
        region: str,
        min_instances: int,
        memory: str,
        cpu: str,
        poll_schedule: str,
        skip_scheduler: bool,
        dry_run: bool,
    ) -> dict[str, Any]:
        if not self.script_path.exists():
            raise RuntimeError("Cloud Run deploy script is missing")
        agent_space = instance_dir / "agent_space"
        if not agent_space.exists():
            raise RuntimeError("Agent instance does not have an agent_space directory")

        clean_agent_name = str(agent_name or "").strip()
        if not clean_agent_name:
            raise ValueError("agent_name is required")

        clean_project = str(project or "").strip() or "glassy-fort-497911-u3"
        clean_region = str(region or "").strip() or "us-central1"
        clean_memory = str(memory or "").strip() or "512Mi"
        clean_cpu = str(cpu or "").strip() or "1"
        clean_schedule = str(poll_schedule or "").strip() or "* * * * *"
        clean_foundry_url = str(foundry_url or "").strip()
        min_instances = max(0, _safe_int(min_instances, 0))

        command = [
            str(self.script_path),
            clean_agent_name,
            "--project",
            clean_project,
            "--region",
            clean_region,
            "--agent-space",
            str(agent_space),
            "--min-instances",
            str(min_instances),
            "--memory",
            clean_memory,
            "--cpu",
            clean_cpu,
            "--poll-schedule",
            clean_schedule,
        ]
        if clean_foundry_url:
            command.extend(["--foundry-url", clean_foundry_url])
        if skip_scheduler:
            command.append("--skip-scheduler")
        if dry_run:
            command.append("--dry-run")

        job_id = uuid.uuid4().hex[:12]
        service_name = re.sub(r"[^a-z0-9-]+", "-", clean_agent_name.lower().replace("_", "-")).strip("-")[:63]
        image_tag = f"{clean_region}-docker.pkg.dev/{clean_project}/ccfoundry-agents/{service_name}:latest"
        job = {
            "id": job_id,
            "agent_name": clean_agent_name,
            "service_name": service_name,
            "status": "queued",
            "dry_run": dry_run,
            "created_at": _utcnow_iso(),
            "updated_at": _utcnow_iso(),
            "project": clean_project,
            "region": clean_region,
            "foundry_url": clean_foundry_url,
            "command": command,
            "logs": [],
            "return_code": None,
            "result": {
                "image_tag": image_tag,
                "scheduler_job": "" if skip_scheduler else f"poll-{service_name}",
                "service_url": "",
                "health_url": "",
                "poll_url": "",
            },
        }
        self._save_job(job)
        thread = threading.Thread(target=self._run_deployment, args=(job_id,), daemon=True)
        thread.start()
        return self.get_deployment(job_id)

    def cancel_deployment(self, job_id: str) -> dict[str, Any]:
        normalized = str(job_id or "").strip()
        with self._lock:
            process = self._processes.get(normalized)
            job = self._jobs.get(normalized)
        if not process or process.poll() is not None:
            if not job:
                raise ValueError(f"Unknown Cloud Run deployment '{normalized}'")
            return dict(job)
        process.terminate()
        self._append_log(normalized, "[dev-board] cancel requested")
        self._update_job(normalized, status="cancelled")
        return self.get_deployment(normalized)

    def _job_path(self, job_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(job_id or ""))
        return self.jobs_dir / f"{safe}.json"

    def _save_job(self, job: dict[str, Any]) -> None:
        job["logs"] = _tail_logs(list(job.get("logs") or []))
        job["updated_at"] = _utcnow_iso()
        with self._lock:
            self._jobs[str(job["id"])] = dict(job)
        self._job_path(str(job["id"])).write_text(
            json.dumps(job, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = dict(self._jobs.get(job_id) or self.get_deployment(job_id))
            job.update(updates)
        self._save_job(job)

    def _append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            job = dict(self._jobs.get(job_id) or self.get_deployment(job_id))
            logs = list(job.get("logs") or [])
            logs.append(line.rstrip("\n"))
            job["logs"] = logs
        self._save_job(job)

    def _run_deployment(self, job_id: str) -> None:
        job = self.get_deployment(job_id)
        command = list(job.get("command") or [])
        self._update_job(job_id, status="running", started_at=_utcnow_iso())
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ},
            )
        except Exception as exc:
            self._update_job(job_id, status="failed", error=str(exc), return_code=1, finished_at=_utcnow_iso())
            return

        with self._lock:
            self._processes[job_id] = process
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self._append_log(job_id, line)
            return_code = process.wait()
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

        latest = self.get_deployment(job_id)
        result = dict(latest.get("result") or {})
        service_url = self._extract_service_url(list(latest.get("logs") or []))
        if service_url:
            result["service_url"] = service_url
            result["health_url"] = f"{service_url.rstrip('/')}/health"
            result["poll_url"] = f"{service_url.rstrip('/')}/foundry/poll"
        next_status = "succeeded" if return_code == 0 else "failed"
        self._update_job(
            job_id,
            status=next_status,
            return_code=return_code,
            result=result,
            finished_at=_utcnow_iso(),
        )

    @staticmethod
    def _extract_service_url(logs: list[str]) -> str:
        patterns = [
            re.compile(r"Service URL:\s*(https://\S+)"),
            re.compile(r"URL:\s*(https://\S+)"),
        ]
        for line in logs:
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    return match.group(1).rstrip()
        return ""
