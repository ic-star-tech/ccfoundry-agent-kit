from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
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


def _is_missing_resource_output(*parts: str) -> bool:
    text = "\n".join(str(part or "") for part in parts).lower()
    missing_markers = (
        "not found",
        "not_found",
        "notfound",
        "does not exist",
        "could not find",
        "no such",
    )
    return any(marker in text for marker in missing_markers)


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


def _metadata_service_account_email() -> str:
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
        headers={"Metadata-Flavor": "Google"},
    )
    try:
        with urllib.request.urlopen(request, timeout=0.8) as response:
            return response.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


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
        self.auth_dir = self.runtime_dir / "cloud_run_auth"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._auth_sessions: dict[str, dict[str, Any]] = {}
        self._auth_processes: dict[str, subprocess.Popen[str]] = {}
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
            _, gcloud_version, version_err = _run_command(["gcloud", "--version"], timeout=12)
            if version_err and version_err != "command timed out":
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
            if not active_account:
                metadata_account = _metadata_service_account_email()
                if metadata_account:
                    active_account = metadata_account
                    accounts = [{"account": metadata_account, "status": "ACTIVE", "source": "gce_metadata"}]
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
                "login_no_browser": "gcloud auth login --no-launch-browser --quiet",
                "set_project": "gcloud config set project <project-id>",
                "configure_docker": "gcloud auth configure-docker <region>-docker.pkg.dev --quiet",
            },
            "errors": errors,
        }

    def start_auth_session(self) -> dict[str, Any]:
        if not shutil.which("gcloud"):
            raise RuntimeError("gcloud CLI is not installed or not on PATH")
        session_id = uuid.uuid4().hex[:12]
        command = ["gcloud", "auth", "login", "--no-launch-browser", "--quiet"]
        session = {
            "id": session_id,
            "status": "starting",
            "created_at": _utcnow_iso(),
            "updated_at": _utcnow_iso(),
            "command": command,
            "auth_url": "",
            "logs": [],
            "return_code": None,
            "error": "",
        }
        process = subprocess.Popen(
            command,
            cwd=str(self.repo_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._auth_sessions[session_id] = session
            self._auth_processes[session_id] = process
            self._save_auth_session(session_id, session)
        thread = threading.Thread(target=self._run_auth_session, args=(session_id,), daemon=True)
        thread.start()
        return self.get_auth_session(session_id)

    def get_auth_session(self, session_id: str) -> dict[str, Any]:
        normalized = str(session_id or "").strip()
        with self._lock:
            session = self._auth_sessions.get(normalized)
            if session:
                return dict(session)
        path = self._auth_path(normalized)
        if not path.exists():
            raise ValueError(f"Unknown Cloud Run auth session '{normalized}'")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Cloud Run auth session '{normalized}' is unreadable") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Cloud Run auth session '{normalized}' is invalid")
        return payload

    def submit_auth_code(self, session_id: str, code: str) -> dict[str, Any]:
        normalized = str(session_id or "").strip()
        auth_code = str(code or "").strip()
        if not auth_code:
            raise ValueError("Authorization code is required")
        with self._lock:
            process = self._auth_processes.get(normalized)
        if not process or process.poll() is not None or not process.stdin:
            raise RuntimeError("Cloud Run auth session is not waiting for input")
        try:
            process.stdin.write(f"{auth_code}\n")
            process.stdin.flush()
        except Exception as exc:
            raise RuntimeError("Failed to send authorization code to gcloud") from exc
        self._update_auth_session(normalized, {"code_submitted_at": _utcnow_iso()})
        return self.get_auth_session(normalized)

    def cancel_auth_session(self, session_id: str) -> dict[str, Any]:
        normalized = str(session_id or "").strip()
        with self._lock:
            process = self._auth_processes.get(normalized)
        if process and process.poll() is None:
            process.terminate()
        self._update_auth_session(normalized, {"status": "canceled", "updated_at": _utcnow_iso()})
        return self.get_auth_session(normalized)

    def list_deployments(self, *, limit: int = 20) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for path in sorted(self.jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                payload = self._with_extracted_result(payload)
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
        return self._with_extracted_result(payload)

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
        clean_schedule = str(poll_schedule or "").strip() or "*/5 * * * *"
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

    def cleanup_agent(self, agent_name: str, *, delete_images: bool = True) -> dict[str, Any]:
        clean_agent_name = str(agent_name or "").strip()
        if not clean_agent_name:
            raise ValueError("agent_name is required")

        deployments = [
            job
            for job in self.list_deployments(limit=500)
            if str(job.get("agent_name") or "").strip() == clean_agent_name and not bool(job.get("dry_run"))
        ]

        with self._lock:
            for job in deployments:
                job_id = str(job.get("id") or "").strip()
                process = self._processes.get(job_id)
                if process and process.poll() is None:
                    process.terminate()
                    self._append_log(job_id, "[dev-board] cleanup requested; deployment process terminated")
                    self._update_job(job_id, status="cancelled")

        targets: dict[tuple[str, str, str], dict[str, str]] = {}
        for job in deployments:
            result = dict(job.get("result") or {})
            service_name = str(job.get("service_name") or "").strip()
            project = str(job.get("project") or "").strip()
            region = str(job.get("region") or "").strip()
            if not service_name or not project or not region:
                continue
            key = (project, region, service_name)
            targets.setdefault(
                key,
                {
                    "project": project,
                    "region": region,
                    "service_name": service_name,
                    "scheduler_job": str(result.get("scheduler_job") or "").strip(),
                    "image_tag": str(result.get("image_tag") or "").strip(),
                },
            )

        actions: list[dict[str, Any]] = []
        for target in targets.values():
            project = target["project"]
            region = target["region"]
            service_name = target["service_name"]
            scheduler_job = target.get("scheduler_job", "")
            image_tag = target.get("image_tag", "")

            if scheduler_job:
                actions.append(
                    self._cleanup_command(
                        kind="scheduler_job",
                        name=scheduler_job,
                        args=[
                            "gcloud",
                            "scheduler",
                            "jobs",
                            "delete",
                            scheduler_job,
                            "--location",
                            region,
                            "--project",
                            project,
                            "--quiet",
                        ],
                        timeout=60,
                    )
                )

            actions.append(
                self._cleanup_command(
                    kind="cloud_run_service",
                    name=service_name,
                    args=[
                        "gcloud",
                        "run",
                        "services",
                        "delete",
                        service_name,
                        "--region",
                        region,
                        "--project",
                        project,
                        "--quiet",
                    ],
                    timeout=90,
                )
            )

            if delete_images and image_tag:
                actions.append(
                    self._cleanup_command(
                        kind="artifact_image",
                        name=image_tag,
                        args=[
                            "gcloud",
                            "artifacts",
                            "docker",
                            "images",
                            "delete",
                            image_tag,
                            "--delete-tags",
                            "--project",
                            project,
                            "--quiet",
                        ],
                        timeout=90,
                    )
                )

        ok = all(bool(action.get("ok")) for action in actions)
        cleanup = {
            "ok": ok,
            "agent_name": clean_agent_name,
            "targets": list(targets.values()),
            "actions": actions,
            "cleaned_at": _utcnow_iso(),
        }
        for job in deployments:
            job_id = str(job.get("id") or "").strip()
            if job_id:
                try:
                    self._update_job(job_id, cleanup=cleanup)
                except Exception:
                    pass
        return cleanup

    def _cleanup_command(
        self,
        *,
        kind: str,
        name: str,
        args: list[str],
        timeout: float,
    ) -> dict[str, Any]:
        code, stdout, stderr = _run_command(args, timeout=timeout)
        missing = code != 0 and _is_missing_resource_output(stdout, stderr)
        return {
            "kind": kind,
            "name": name,
            "ok": code == 0 or missing,
            "missing": missing,
            "return_code": code,
            "stdout": stdout[-1200:] if stdout else "",
            "stderr": stderr[-1200:] if stderr else "",
        }

    def _auth_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(session_id or ""))
        return self.auth_dir / f"{safe}.json"

    def _save_auth_session(self, session_id: str, session: dict[str, Any]) -> None:
        session["logs"] = _tail_logs(list(session.get("logs") or []), 160)
        session["updated_at"] = _utcnow_iso()
        with self._lock:
            self._auth_sessions[session_id] = dict(session)
        self._auth_path(session_id).write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _update_auth_session(self, session_id: str, updates: dict[str, Any]) -> None:
        with self._lock:
            session = dict(self._auth_sessions.get(session_id) or self.get_auth_session(session_id))
            session.update(updates)
        self._save_auth_session(session_id, session)

    def _append_auth_log(self, session_id: str, line: str) -> None:
        with self._lock:
            session = dict(self._auth_sessions.get(session_id) or self.get_auth_session(session_id))
            logs = list(session.get("logs") or [])
            clean_line = line.rstrip("\n")
            logs.append(clean_line)
            session["logs"] = logs
            match = re.search(r"https://accounts\.google\.com/[^\s]+", clean_line)
            if match:
                session["auth_url"] = match.group(0)
        self._save_auth_session(session_id, session)

    def _run_auth_session(self, session_id: str) -> None:
        with self._lock:
            process = self._auth_processes.get(session_id)
        if not process:
            self._update_auth_session(session_id, {"status": "failed", "error": "gcloud process was not started"})
            return
        self._update_auth_session(session_id, {"status": "running"})
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if line:
                    self._append_auth_log(session_id, line)
            return_code = process.wait()
        except Exception as exc:
            self._update_auth_session(session_id, {"status": "failed", "error": str(exc), "return_code": 1})
            return
        finally:
            with self._lock:
                self._auth_processes.pop(session_id, None)

        latest = self.get_auth_session(session_id)
        if latest.get("status") == "canceled":
            self._update_auth_session(session_id, {"return_code": return_code})
            return
        self._update_auth_session(
            session_id,
            {
                "status": "succeeded" if return_code == 0 else "failed",
                "return_code": return_code,
                "error": "" if return_code == 0 else f"gcloud auth login exited with {return_code}",
            },
        )

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

    def _with_extracted_result(self, job: dict[str, Any]) -> dict[str, Any]:
        service_url = self._extract_service_url(list(job.get("logs") or []))
        if not service_url:
            return job
        normalized = dict(job)
        result = dict(normalized.get("result") or {})
        result["service_url"] = service_url
        result["health_url"] = f"{service_url.rstrip('/')}/health"
        result["poll_url"] = f"{service_url.rstrip('/')}/foundry/poll"
        normalized["result"] = result
        return normalized

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
        normalized_latest = self._with_extracted_result(latest)
        result.update(dict(normalized_latest.get("result") or {}))
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
            re.compile(r"\[deploy\]\s+Service URL:\s*(https://\S+)"),
            re.compile(r"Service URL:\s*(https://\S+)"),
            re.compile(r"URL:\s*(https://\S+)"),
        ]
        for line in reversed(logs):
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    return match.group(1).rstrip()
        return ""
