from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ccfoundry_agent_kit import AgentClient, ChatRequest, ContextMode
from agent_dev_board_api.cloud_run import DEFAULT_CLOUD_RUN_REGION, CloudRunManager
from agent_dev_board_api.local_agents import LocalAgentManager
from agent_dev_board_api.skill_store import SkillStore


APP_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
AGENTS_FILE = Path(os.getenv("CCFOUNDRY_AGENTS_FILE", str(APP_DIR / "agents.yaml"))).resolve()
RUNTIME_DIR = AGENTS_FILE.parent
VENV_PYTHON = Path(os.getenv("CCFOUNDRY_DEV_VENV_PYTHON", str(REPO_ROOT / ".venv" / "bin" / "python"))).expanduser()
TRANSCRIPTS: dict[str, list[dict[str, str]]] = {}
logger = logging.getLogger(__name__)
SKILL_STORE = SkillStore()


_LOCALHOST_ORIGIN_RE = r"^https?://(localhost|127(?:\.[0-9]{1,3}){3}|\[::1\])(:[0-9]+)?$"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_TRUSTED_FOUNDRY_HOSTS = {"foundry.cochiper.com", "foundry.cochiper.ai"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _is_loopback_host(host: str | None) -> bool:
    normalized = str(host or "").strip().strip("[]").lower()
    if normalized in _LOOPBACK_HOSTS:
        return True
    return normalized.startswith("127.")


def _default_url_scheme(value: str) -> str:
    netloc = value.split("/", 1)[0].split("@")[-1]
    host = netloc.rsplit(":", 1)[0].strip("[]")
    return "http" if _is_loopback_host(host) else "https"


def _cors_allowed_origins() -> list[str]:
    return _env_csv("CCFOUNDRY_DEV_BOARD_ALLOWED_ORIGINS")


def _cors_allowed_origin_regex() -> str | None:
    explicit_regex = os.getenv("CCFOUNDRY_DEV_BOARD_ALLOWED_ORIGIN_REGEX", "").strip()
    if explicit_regex:
        return explicit_regex
    if _cors_allowed_origins():
        return None
    return _LOCALHOST_ORIGIN_RE


class LiteAgentConfig(BaseModel):
    name: str
    label: str
    base_url: str


class LiteChatRequest(BaseModel):
    agent_name: str
    message: str
    mode: ContextMode = ContextMode.DIRECT
    username: str = "local-user"
    conversation_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    dev_overrides: dict[str, str] = Field(default_factory=dict)


class LiteChatResponse(BaseModel):
    agent_name: str
    conversation_id: str
    reply: str
    transcript: list[dict[str, str]]
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiteHandshakeProbeRequest(BaseModel):
    agent_name: str
    foundry_url: str = ""


class DeveloperContextRequest(BaseModel):
    foundry_url: str = ""


class DeveloperBootstrapTicketRequest(BaseModel):
    agent_name: str
    foundry_url: str = ""
    developer_token: str = ""
    github_token: str = ""
    bootstrap_delivery: str = "poll"
    force_rediscover: bool = True
    runtime_target: str = "local"


class DeveloperNotificationPreferencesSyncRequest(BaseModel):
    agent_name: str
    foundry_url: str = ""
    developer_token: str = ""
    github_token: str = ""
    email: str = ""
    bounty_success_email_enabled: bool = True


class LocalAgentTemplate(BaseModel):
    id: str
    label: str
    description: str


class LocalAgentRuntime(BaseModel):
    name: str
    label: str
    template_id: str
    host: str
    port: int
    base_url: str
    instance_dir: str
    foundry_url: str = ""
    created_at: str = ""
    started_at: str = ""
    stopped_at: str = ""
    status: str = "stopped"
    pid: int | None = None
    env_path: str = ""
    log_path: str = ""
    retired_at: str = ""


class LocalAgentCreateRequest(BaseModel):
    template_id: str = "me_agent"
    name: str
    label: str = ""
    preferred_port: int | None = None
    foundry_url: str = ""
    auto_start: bool = False


class CloudRunDeployRequest(BaseModel):
    agent_name: str
    project: str = ""
    region: str = DEFAULT_CLOUD_RUN_REGION
    foundry_url: str = ""
    min_instances: int = 0
    memory: str = "512Mi"
    cpu: str = "1"
    poll_schedule: str = "* * * * *"
    skip_scheduler: bool = False
    dry_run: bool = False


class CloudRunAuthCodeRequest(BaseModel):
    code: str


class RetireAgentRequest(BaseModel):
    foundry_url: str = ""
    developer_token: str = ""
    github_token: str = ""
    reason: str = "dev_board_retire"
    stop_local: bool = True


LOCAL_AGENT_MANAGER = LocalAgentManager(
    repo_root=REPO_ROOT,
    runtime_dir=RUNTIME_DIR,
    agents_file=AGENTS_FILE,
    venv_python=VENV_PYTHON,
)
CLOUD_RUN_MANAGER = CloudRunManager(repo_root=REPO_ROOT, runtime_dir=RUNTIME_DIR)


def _load_agents() -> dict[str, LiteAgentConfig]:
    LOCAL_AGENT_MANAGER.ensure_runtime_files()
    if not AGENTS_FILE.exists():
        return {}
    raw = yaml.safe_load(AGENTS_FILE.read_text(encoding="utf-8")) or {}
    entries = raw.get("agents") or []
    result: dict[str, LiteAgentConfig] = {}
    for entry in entries:
        model = LiteAgentConfig.model_validate(entry)
        result[model.name] = model
    return result


def _agent_config_from_local_registry(agent_name: str) -> LiteAgentConfig | None:
    try:
        _, item = LOCAL_AGENT_MANAGER._find_agent(agent_name)
    except Exception:
        return None
    name = str(item.get("name") or "").strip()
    label = str(item.get("label") or name).strip()
    base_url = str(item.get("base_url") or "").strip()
    if not name or not label or not base_url:
        return None
    return LiteAgentConfig.model_validate({"name": name, "label": label, "base_url": base_url})


def _render_context(history: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for item in history[-20:]:
        lines.append(f"[user] {item['user']}")
        lines.append(f"[assistant] {item['assistant']}")
    return "\n".join(lines)


def _history_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in history[-20:]:
        messages.append({"role": "user", "content": item["user"]})
        messages.append({"role": "assistant", "content": item["assistant"]})
    return messages


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalized_dev_overrides(raw: dict[str, str] | None) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    allowed = {"model", "base_url", "api_key"}
    return {
        key: str(value).strip()
        for key, value in raw.items()
        if key in allowed and str(value).strip()
    }


def _normalize_url(raw_url: str) -> str:
    value = str(raw_url or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        if ":" in value and not value.split(":", 1)[1].split("/", 1)[0].isdigit():
            return ""
        value = f"{_default_url_scheme(value)}://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if (
        parsed.scheme == "http"
        and not _is_loopback_host(parsed.hostname)
        and not _env_truthy("CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP")
    ):
        return ""
    return value.rstrip("/")


def _normalize_local_agent_url(raw_url: str) -> str:
    value = _normalize_url(raw_url)
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "http" or not _is_loopback_host(parsed.hostname):
        return ""
    return value


def _trusted_foundry_hosts() -> set[str]:
    return {
        item.lower()
        for item in [*_DEFAULT_TRUSTED_FOUNDRY_HOSTS, *_env_csv("CCFOUNDRY_TRUSTED_FOUNDRY_HOSTS")]
        if item
    }


def _is_trusted_foundry_url(foundry_url: str) -> bool:
    parsed = urlparse(foundry_url)
    host = str(parsed.hostname or "").strip().lower()
    return host in _trusted_foundry_hosts()


def _discover_github_token_for_foundry(explicit_token: str, foundry_url: str) -> tuple[str, str]:
    if str(explicit_token or "").strip():
        return _discover_github_token(explicit_token)
    if _is_trusted_foundry_url(foundry_url):
        return _discover_github_token("")
    return "", "custom_foundry_requires_explicit_token"


def _run_command(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd or REPO_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return completed.stdout.strip()


def _parse_github_repo(raw_url: str) -> dict[str, str]:
    value = str(raw_url or "").strip()
    if not value:
        return {}
    normalized = value
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    normalized = normalized.replace("git@github.com:", "https://github.com/")
    if "github.com/" not in normalized:
        return {}
    suffix = normalized.split("github.com/", 1)[1].strip("/")
    parts = [item for item in suffix.split("/") if item]
    if len(parts) < 2:
        return {}
    return {
        "host": "github.com",
        "owner": parts[0],
        "repo": parts[1],
        "slug": f"{parts[0]}/{parts[1]}",
        "url": f"https://github.com/{parts[0]}/{parts[1]}",
    }


def _git_context() -> dict[str, Any]:
    root = _run_command(["git", "rev-parse", "--show-toplevel"], cwd=REPO_ROOT)
    branch = _run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT)
    head = _run_command(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT)
    dirty = bool(_run_command(["git", "status", "--porcelain"], cwd=REPO_ROOT))
    remotes_raw = _run_command(["git", "remote", "-v"], cwd=REPO_ROOT)

    remotes_map: dict[str, dict[str, str]] = {}
    for line in remotes_raw.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2].strip("()")
        entry = remotes_map.setdefault(name, {"name": name})
        entry[kind] = url

    remotes = list(remotes_map.values())
    origin = next((item for item in remotes if item.get("name") == "origin"), {})
    upstream = next((item for item in remotes if item.get("name") == "upstream"), {})
    origin_repo = _parse_github_repo(str(origin.get("fetch") or origin.get("push") or ""))
    upstream_repo = _parse_github_repo(str(upstream.get("fetch") or upstream.get("push") or ""))
    fork_signal = bool(
        origin_repo
        and upstream_repo
        and origin_repo.get("repo") == upstream_repo.get("repo")
        and origin_repo.get("owner") != upstream_repo.get("owner")
    )

    return {
        "root": root or str(REPO_ROOT),
        "branch": branch or "",
        "head": head or "",
        "dirty": dirty,
        "remotes": remotes,
        "origin": origin,
        "upstream": upstream,
        "origin_repo": origin_repo,
        "upstream_repo": upstream_repo,
        "fork_signal": fork_signal,
    }


def _discover_github_token(explicit_token: str = "") -> tuple[str, str]:
    if str(explicit_token or "").strip():
        return str(explicit_token).strip(), "user_input"
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.getenv(name, "").strip()
        if value:
            return value, f"env:{name}"
    if shutil.which("gh"):
        token = _run_command(["gh", "auth", "token"], cwd=REPO_ROOT)
        if token:
            return token, "gh_cli"
        status_output = _run_command(["gh", "auth", "status", "--show-token"], cwd=REPO_ROOT)
        for line in status_output.splitlines():
            marker = "Token:"
            if marker not in line:
                continue
            candidate = line.split(marker, 1)[1].strip()
            if candidate:
                return candidate, "gh_cli_status"
    gh_config_dir = os.getenv("GH_CONFIG_DIR", "").strip()
    if gh_config_dir:
        hosts_path = Path(gh_config_dir) / "hosts.yml"
    else:
        hosts_path = Path.home() / ".config" / "gh" / "hosts.yml"
    if hosts_path.exists():
        try:
            payload = yaml.safe_load(hosts_path.read_text(encoding="utf-8")) or {}
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            github_host = payload.get("github.com")
            if isinstance(github_host, dict):
                candidate = str(github_host.get("oauth_token") or "").strip()
                if candidate:
                    return candidate, "gh_hosts_yml"
    return "", "missing"


async def _github_identity(token: str) -> dict[str, Any]:
    if not str(token or "").strip():
        return {"authenticated": False, "token_source": "missing"}

    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ccfoundry-agent-kit-dev-board",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get("https://api.github.com/user", headers=headers)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if not response.is_success:
            logger.warning("GitHub identity lookup failed with status %s", response.status_code)
            return {
                "authenticated": False,
                "status_code": response.status_code,
                "detail": "GitHub API request failed",
            }
        if not isinstance(payload, dict):
            payload = {}
        return {
            "authenticated": True,
            "status_code": response.status_code,
            "login": str(payload.get("login") or "").strip(),
            "id": payload.get("id"),
            "name": str(payload.get("name") or "").strip(),
            "avatar_url": str(payload.get("avatar_url") or "").strip(),
            "html_url": str(payload.get("html_url") or "").strip(),
        }
    except Exception as exc:
        logger.warning("GitHub identity lookup failed", exc_info=exc)
        return {
            "authenticated": False,
            "detail": "GitHub API request failed",
        }


async def _agent_bootstrap_state(agent: LiteAgentConfig) -> dict[str, Any]:
    url = f"{agent.base_url.rstrip('/')}/foundry/bootstrap/state"
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        payload, probe = await _probe_json(client, url)
    if isinstance(payload, dict) and bool((probe or {}).get("ok")):
        return payload

    source_state, _ = _read_agent_source_bootstrap_state(agent.name)
    cloud_run_state = await _agent_cloud_run_bootstrap_state(agent.name, source_state)
    if cloud_run_state:
        return _public_bootstrap_state(cloud_run_state)
    if source_state:
        return _public_bootstrap_state(source_state)
    return {"enabled": False}


async def _metadata_identity_token(audience: str) -> str:
    if _env_truthy("CCFOUNDRY_DEV_BOARD_DISABLE_GCE_METADATA_AUTH"):
        return ""
    normalized_audience = str(audience or "").strip().rstrip("/")
    if not normalized_audience:
        return ""
    metadata_url = (
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
        f"?audience={quote(normalized_audience, safe='')}&format=full"
    )
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(metadata_url, headers={"Metadata-Flavor": "Google"})
        if response.is_success:
            return response.text.strip()
    except Exception:
        return ""
    return ""


async def _gcloud_identity_token(audience: str) -> str:
    normalized_audience = str(audience or "").strip().rstrip("/")
    if not normalized_audience:
        return ""

    def _gcloud_token() -> str:
        for command in (
            ["gcloud", "auth", "print-identity-token", f"--audiences={normalized_audience}"],
            ["gcloud", "auth", "print-identity-token"],
        ):
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(REPO_ROOT),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except Exception:
                continue
            token = completed.stdout.strip()
            if token:
                return token
        return ""

    return await asyncio.to_thread(_gcloud_token)


async def _cloud_run_request(method: str, url: str, *, audience: str, timeout: float) -> httpx.Response:
    metadata_token = await _metadata_identity_token(audience)
    gcloud_token = await _gcloud_identity_token(audience)
    attempts: list[tuple[str, str]] = [("none", "")]
    if metadata_token:
        attempts.append(("metadata", metadata_token))
    if gcloud_token and gcloud_token != metadata_token:
        attempts.append(("gcloud", gcloud_token))

    last_response: httpx.Response | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for index, (token_source, token) in enumerate(attempts):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await client.request(method, url, headers=headers)
            response.extensions["ccfoundry_token_source"] = token_source
            has_more_attempts = index < len(attempts) - 1
            if response.is_success or response.status_code not in {401, 403, 404} or not has_more_attempts:
                return response
            last_response = response
    return last_response or response


async def _agent_cloud_run_bootstrap_state(agent_name: str, source_state: dict[str, Any]) -> dict[str, Any]:
    try:
        deployments = CLOUD_RUN_MANAGER.list_deployments(limit=50)
    except Exception:
        return {}
    normalized_agent_name = str(agent_name or "").strip()
    for deployment in deployments:
        if str(deployment.get("agent_name") or "").strip() != normalized_agent_name:
            continue
        if bool(deployment.get("dry_run")):
            continue
        result = deployment.get("result") if isinstance(deployment.get("result"), dict) else {}
        service_url = str((result or {}).get("service_url") or "").strip().rstrip("/")
        if not service_url:
            continue
        try:
            response = await _cloud_run_request(
                "GET",
                f"{service_url}/foundry/bootstrap/state",
                audience=service_url,
                timeout=4.0,
            )
        except Exception as exc:
            logger.info("Cloud Run bootstrap state probe failed for %s: %s", service_url, exc)
            continue
        if not response.is_success:
            logger.info("Cloud Run bootstrap state probe returned %s for %s", response.status_code, service_url)
            continue
        try:
            remote_state = response.json()
        except Exception:
            continue
        if not isinstance(remote_state, dict) or not remote_state.get("enabled"):
            continue
        merged = dict(source_state or {})
        for key, value in remote_state.items():
            if value is not None:
                merged[key] = value
        if source_state.get("discovery_claim_token"):
            merged["discovery_claim_token"] = source_state["discovery_claim_token"]
        if source_state.get("last_claimed_at") and not merged.get("last_claimed_at"):
            merged["last_claimed_at"] = source_state["last_claimed_at"]
        if isinstance(source_state.get("developer_identity"), dict) and source_state.get("developer_identity"):
            merged.setdefault("developer_identity", source_state["developer_identity"])
        source_path = _agent_source_state_path(normalized_agent_name)
        if source_path:
            try:
                source_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.info("Failed to cache Cloud Run bootstrap state at %s: %s", source_path, exc)
        return merged
    return {}


def _latest_cloud_run_deployment(agent_name: str) -> dict[str, Any]:
    normalized_agent_name = str(agent_name or "").strip()
    if not normalized_agent_name:
        return {}
    try:
        deployments = CLOUD_RUN_MANAGER.list_deployments(limit=50)
    except Exception:
        return {}
    for deployment in deployments:
        if str(deployment.get("agent_name") or "").strip() != normalized_agent_name:
            continue
        if bool(deployment.get("dry_run")):
            continue
        result = deployment.get("result") if isinstance(deployment.get("result"), dict) else {}
        if str((result or {}).get("service_url") or "").strip():
            return deployment
    return {}


def _cloud_run_env_arg(env_vars: dict[str, str]) -> str:
    entries = [f"{key}={value}" for key, value in env_vars.items() if str(value or "").strip()]
    for delimiter in ("|", "~", "%", ";", "#"):
        if all(delimiter not in entry for entry in entries):
            return f"^{delimiter}^" + delimiter.join(entries)
    raise ValueError("Cloud Run environment values contain every supported gcloud delimiter")


async def _apply_developer_claim_to_cloud_run(
    agent_name: str,
    foundry_url: str,
    apply_payload: dict[str, Any],
) -> dict[str, Any]:
    deployment = _latest_cloud_run_deployment(agent_name)
    if not deployment:
        return {
            "ok": False,
            "status": "not_deployed",
            "message": "No existing Cloud Run deployment found; the claim will be carried by the next deploy.",
        }

    result = deployment.get("result") if isinstance(deployment.get("result"), dict) else {}
    service_url = str((result or {}).get("service_url") or "").strip().rstrip("/")
    service_name = str(deployment.get("service_name") or "").strip()
    project = str(deployment.get("project") or "").strip()
    region = str(deployment.get("region") or "").strip()
    if not service_url or not service_name or not project or not region:
        return {
            "ok": False,
            "status": "incomplete_deployment",
            "message": "Cloud Run deployment metadata is incomplete; redeploy after the claim is installed.",
        }

    developer_identity = apply_payload.get("developer_identity")
    env_vars = {
        "FOUNDRY_DISCOVERY_ENABLE": "true",
        "FOUNDRY_BASE_URL": foundry_url,
        "FOUNDRY_AGENT_PUBLIC_URL": service_url,
        "FOUNDRY_BOOTSTRAP_DELIVERY": str(apply_payload.get("bootstrap_delivery") or "poll"),
        "FOUNDRY_DISCOVERY_CLAIM_TOKEN": str(apply_payload.get("discovery_claim_token") or ""),
    }
    if isinstance(developer_identity, dict) and developer_identity:
        env_vars["FOUNDRY_DEVELOPER_IDENTITY_JSON"] = json.dumps(
            developer_identity,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    try:
        env_arg = _cloud_run_env_arg(env_vars)
    except ValueError as exc:
        return {
            "ok": False,
            "status": "invalid_env",
            "message": str(exc),
            "service_name": service_name,
            "project": project,
            "region": region,
            "service_url": service_url,
        }

    command = [
        "gcloud",
        "run",
        "services",
        "update",
        service_name,
        "--project",
        project,
        "--region",
        region,
        f"--update-env-vars={env_arg}",
        "--quiet",
    ]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=str(REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "update_failed",
            "message": str(exc),
            "service_name": service_name,
            "project": project,
            "region": region,
            "service_url": service_url,
        }

    if completed.returncode != 0:
        return {
            "ok": False,
            "status": "update_failed",
            "return_code": completed.returncode,
            "stderr": (completed.stderr or completed.stdout or "")[-1200:],
            "service_name": service_name,
            "project": project,
            "region": region,
            "service_url": service_url,
        }

    poll_result: dict[str, Any] = {}
    try:
        response = await _cloud_run_request(
            "POST",
            f"{service_url}/foundry/poll",
            audience=service_url,
            timeout=30.0,
        )
        poll_result = {
            "ok": response.is_success,
            "status_code": response.status_code,
            "token_source": str(response.extensions.get("ccfoundry_token_source") or ""),
        }
    except Exception as exc:
        poll_result = {"ok": False, "status": "poll_failed", "message": str(exc)}

    source_state, _ = _read_agent_source_bootstrap_state(agent_name)
    synced_state = await _agent_cloud_run_bootstrap_state(agent_name, source_state)
    return {
        "ok": True,
        "status": "updated",
        "service_name": service_name,
        "project": project,
        "region": region,
        "service_url": service_url,
        "poll": poll_result,
        "synced": bool(synced_state),
        "registration_status": str(synced_state.get("registration_status") or ""),
        "registered_agent_name": str(synced_state.get("registered_agent_name") or ""),
    }


def _log_cloud_run_claim_apply_result(task: asyncio.Task[dict[str, Any]]) -> None:
    try:
        result = task.result()
    except Exception:
        logger.exception("Cloud Run claim apply task failed")
        return
    if not bool(result.get("ok")):
        logger.warning("Cloud Run claim apply did not complete: %s", result)
        return
    logger.info(
        "Cloud Run claim apply completed for %s (%s)",
        result.get("service_name") or result.get("service_url") or "unknown service",
        result.get("registration_status") or result.get("status") or "updated",
    )


def _agent_source_item(agent_name: str) -> dict[str, Any]:
    _, item = LOCAL_AGENT_MANAGER._find_agent(agent_name)
    return item


def _agent_source_state_path(agent_name: str) -> Path | None:
    try:
        item = _agent_source_item(agent_name)
    except Exception:
        return None
    instance_dir = Path(str(item.get("instance_dir") or "")).expanduser()
    if not str(instance_dir):
        return None
    return instance_dir / ".foundry_bootstrap.json"


def _agent_source_notification_path(agent_name: str) -> Path | None:
    try:
        item = _agent_source_item(agent_name)
    except Exception:
        return None
    instance_dir = Path(str(item.get("instance_dir") or "")).expanduser()
    if not str(instance_dir):
        return None
    return instance_dir / ".foundry_notifications.json"


def _read_agent_notification_preferences(agent_name: str) -> dict[str, Any]:
    path = _agent_source_notification_path(agent_name)
    if not path or not path.exists():
        return {
            "email": "",
            "bounty_success_email_enabled": True,
            "status": "not_configured",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read notification preferences from %s", path, exc_info=exc)
        return {
            "email": "",
            "bounty_success_email_enabled": True,
            "status": "invalid",
        }
    return payload if isinstance(payload, dict) else {}


def _write_agent_notification_preferences(agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _agent_source_notification_path(agent_name)
    if not path:
        raise RuntimeError("Agent source notification path is unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**payload, "path": str(path)}


def _read_agent_source_bootstrap_state(agent_name: str) -> tuple[dict[str, Any], Path | None]:
    candidates: list[Path] = []
    source_path = _agent_source_state_path(agent_name)
    if source_path:
        candidates.append(source_path)
    candidates.extend(
        [
            RUNTIME_DIR / "agents" / agent_name / ".foundry_bootstrap.json",
            APP_DIR / "agents" / agent_name / ".foundry_bootstrap.json",
        ]
    )
    seen: set[Path] = set()
    for state_path in candidates:
        resolved = state_path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.exists():
            continue
        try:
            disk_payload = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read bootstrap state from %s", resolved, exc_info=exc)
            continue
        if isinstance(disk_payload, dict):
            return disk_payload, resolved
    return {}, None


def _public_bootstrap_state(state: dict[str, Any]) -> dict[str, Any]:
    safe = {
        key: value
        for key, value in dict(state or {}).items()
        if key not in {"discovery_claim_token", "env_vars"}
    }
    env_vars = state.get("env_vars")
    if not isinstance(env_vars, dict):
        env_vars = {}
    has_claim = bool(str(state.get("discovery_claim_token") or "").strip()) or bool(safe.get("has_discovery_claim"))
    safe["enabled"] = bool(safe.get("enabled") or safe.get("foundry_base_url") or has_claim)
    safe["has_discovery_claim"] = has_claim
    safe["has_agent_secret"] = bool(safe.get("has_agent_secret") or str(env_vars.get("AGENT_SECRET") or "").strip())
    safe["has_llm_api_key"] = bool(
        safe.get("has_llm_api_key")
        or str(env_vars.get("LLM_API_KEY") or "").strip()
        or str(env_vars.get("OPENAI_API_KEY") or "").strip()
    )
    safe["has_llm_api_base"] = bool(
        safe.get("has_llm_api_base")
        or str(env_vars.get("LLM_API_BASE") or "").strip()
        or str(env_vars.get("OPENAI_BASE_URL") or "").strip()
    )
    if safe.get("registration_status") == "APPROVED" and safe.get("approved_at"):
        safe["approval_mode"] = "callback"
    elif safe.get("registration_status") == "APPROVED":
        safe["approval_mode"] = "inline_register"
    else:
        safe.setdefault("approval_mode", "pending")
    return safe


def _install_developer_claim_to_source(agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    state_path = _agent_source_state_path(agent_name)
    if not state_path:
        raise RuntimeError("Agent source state path is unavailable")
    state: dict[str, Any] = {}
    try:
        raw_state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(raw_state, dict):
            state = raw_state
    except FileNotFoundError:
        state = {}
    except Exception as exc:
        logger.warning("Failed to read bootstrap state from %s before claim install", state_path, exc_info=exc)
        state = {}

    foundry_base_url = str(payload.get("foundry_base_url") or "").strip().rstrip("/")
    public_base_url = str(payload.get("public_base_url") or "").strip().rstrip("/")
    bootstrap_delivery = str(payload.get("bootstrap_delivery") or "poll").strip().lower() or "poll"
    developer_identity = payload.get("developer_identity")
    state["enabled"] = True
    if foundry_base_url:
        state["foundry_base_url"] = foundry_base_url
    if public_base_url:
        state["public_base_url"] = public_base_url
    state["discovery_claim_token"] = str(payload.get("discovery_claim_token") or "").strip()
    state["bootstrap_delivery"] = bootstrap_delivery
    state["last_claimed_at"] = _utcnow_iso()
    if isinstance(developer_identity, dict) and developer_identity:
        state["developer_identity"] = developer_identity
    if bool(payload.get("force_rediscover")):
        for key in (
            "discovery_id",
            "discovery_status",
            "last_discovery_at",
            "invite_id",
            "invite_status",
            "invite_expected_name",
            "last_polled_at",
        ):
            state[key] = None
    state["last_error"] = None

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "mode": "source_state",
        "state_path": str(state_path),
        "last_claimed_at": state["last_claimed_at"],
    }


async def _developer_route_probes(foundry_url: str) -> dict[str, dict[str, Any]]:
    normalized = _normalize_url(foundry_url)
    if not normalized:
        return {}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        return {
            "bootstrap_ticket": await _probe_route(
                client,
                f"{normalized}/api/developer/bootstrap-ticket",
                method="POST",
                json_body={},
            ),
            "bootstrap_ticket_alt": await _probe_route(
                client,
                f"{normalized}/api/dev/bootstrap-ticket",
                method="POST",
                json_body={},
            ),
            "discovery_actions": await _probe_route(
                client,
                f"{normalized}/api/registry/discover/actions",
                method="POST",
                json_body={},
            ),
        }


async def _retire_foundry_agent(
    *,
    agent: LiteAgentConfig,
    foundry_agent_name: str,
    foundry_url: str,
    request: RetireAgentRequest,
) -> dict[str, Any]:
    normalized = _normalize_url(foundry_url)
    if not normalized:
        raise HTTPException(status_code=400, detail="Foundry URL is required")

    developer_token = str(request.developer_token or "").strip()
    github_token = str(request.github_token or "").strip()
    if not developer_token and not github_token:
        raise HTTPException(status_code=400, detail="GitHub login is required before retiring a Foundry agent")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if developer_token:
        headers["Authorization"] = f"Bearer {developer_token}"
    if github_token:
        headers["X-GitHub-Token"] = github_token

    reason = str(request.reason or "dev_board_retire").strip() or "dev_board_retire"
    payload = {
        "reason": reason,
        "requested_by": "agent_dev_board",
        "source": "agent_dev_board",
    }
    target_agent_name = str(foundry_agent_name or "").strip() or agent.name
    encoded_name = quote(target_agent_name, safe="")
    route_attempts: list[dict[str, Any]] = []
    failure_message = ""
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        for method, route_path in (
            ("POST", f"/api/developer/agents/{encoded_name}/retire"),
            ("DELETE", f"/api/my/agents/{encoded_name}"),
            ("POST", f"/api/agents/{encoded_name}/retire"),
        ):
            url = f"{normalized}{route_path}"
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=payload if method != "DELETE" else None,
                )
            except Exception as exc:
                logger.warning("Foundry retire request failed for %s %s", method, url, exc_info=exc)
                route_attempts.append({"url": url, "method": method, "ok": False, "detail": "request_failed"})
                continue

            entry: dict[str, Any] = {
                "url": url,
                "method": method,
                "status_code": response.status_code,
                "ok": response.is_success or response.status_code == 410,
            }
            if response.status_code in {404, 405}:
                entry["detail"] = "route_not_available"
                route_attempts.append(entry)
                continue
            route_attempts.append(entry)

            if response.is_success or response.status_code == 410:
                try:
                    upstream_payload = response.json()
                except Exception:
                    upstream_payload = {}
                if not isinstance(upstream_payload, dict):
                    upstream_payload = {"value": upstream_payload}
                return {
                    "ok": True,
                    "foundry_url": normalized,
                    "agent_name": target_agent_name,
                    "route": url,
                    "route_attempts": route_attempts,
                    "upstream": upstream_payload,
                    "status": str(upstream_payload.get("status") or "RETIRED"),
                }

            try:
                upstream_error = response.json()
            except Exception:
                upstream_error = {"detail": response.text}
            if isinstance(upstream_error, dict):
                failure_message = str(upstream_error.get("detail") or upstream_error.get("message") or "").strip()
            failure_message = failure_message or f"Foundry retire request failed (status {response.status_code})"

    raise HTTPException(
        status_code=409,
        detail={
            "message": failure_message or "No supported Foundry retire endpoint was found",
            "foundry_url": normalized,
            "route_attempts": route_attempts,
        },
    )


def _foundry_retire_failure_result(
    *,
    foundry_url: str,
    foundry_agent_name: str,
    exc: Exception,
) -> dict[str, Any]:
    message = "Foundry remote retire failed; retired the local Dev Board runtime only."
    upstream_message = ""
    route_attempts: list[dict[str, Any]] = []
    status_code: int | None = None
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        detail = exc.detail
        if isinstance(detail, dict):
            raw_message = str(detail.get("message") or detail.get("detail") or "").strip()
            if raw_message:
                upstream_message = raw_message
                message = raw_message
            raw_attempts = detail.get("route_attempts")
            if isinstance(raw_attempts, list):
                route_attempts = [item for item in raw_attempts if isinstance(item, dict)]
        elif isinstance(detail, str) and detail.strip():
            upstream_message = detail.strip()
            message = upstream_message
    else:
        raw_message = str(exc).strip()
        if raw_message:
            upstream_message = raw_message
            message = raw_message

    if upstream_message == "User no longer exists":
        message = (
            "Foundry login session is stale or points to a removed user. "
            "The local runtime was retired; sign in again before retrying Foundry remote retire."
        )

    result = {
        "ok": False,
        "foundry_url": foundry_url,
        "agent_name": foundry_agent_name,
        "status": "REMOTE_RETIRE_FAILED",
        "message": message,
        "http_status_code": status_code,
        "route_attempts": route_attempts,
    }
    if upstream_message and upstream_message != message:
        result["upstream_message"] = upstream_message
    return result


def _developer_identity_from_context(git_context: dict[str, Any], github_identity: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    login = str(github_identity.get("login") or "").strip()
    if login:
        identity["github_login"] = login
    avatar_url = str(github_identity.get("avatar_url") or "").strip()
    if avatar_url:
        identity["github_avatar_url"] = avatar_url
    origin_repo = dict(git_context.get("origin_repo") or {})
    if origin_repo:
        identity["repo"] = origin_repo
    upstream_repo = dict(git_context.get("upstream_repo") or {})
    if upstream_repo:
        identity["upstream_repo"] = upstream_repo
    if git_context.get("fork_signal"):
        identity["fork_signal"] = True
    return identity


async def _sync_developer_notification_preferences(
    *,
    agent: LiteAgentConfig,
    foundry_url: str,
    developer_token: str,
    github_token: str,
    email: str,
    bounty_success_email_enabled: bool,
) -> dict[str, Any]:
    normalized_foundry_url = _normalize_url(foundry_url)
    if not normalized_foundry_url:
        raise HTTPException(status_code=400, detail="Foundry URL is required")

    normalized_email = str(email or "").strip().lower()
    enabled = bool(bounty_success_email_enabled)
    if enabled and not normalized_email:
        raise HTTPException(status_code=400, detail="Notification email is required")

    git_context = _git_context()
    resolved_github_token, github_token_source = _discover_github_token_for_foundry(
        github_token,
        normalized_foundry_url,
    )
    github_identity = await _github_identity(resolved_github_token)
    github_identity["token_source"] = github_token_source
    github_identity["has_token"] = bool(resolved_github_token)
    developer_identity = _developer_identity_from_context(git_context, github_identity)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    normalized_developer_token = str(developer_token or "").strip()
    if normalized_developer_token:
        headers["Authorization"] = f"Bearer {normalized_developer_token}"
    if resolved_github_token:
        headers["X-GitHub-Token"] = resolved_github_token
    if not normalized_developer_token and not resolved_github_token:
        raise HTTPException(status_code=400, detail="GitHub login is required before syncing notification email")

    payload = {
        "email": normalized_email,
        "bounty_success_email_enabled": enabled,
        "source": "dev_board",
        "developer_identity": developer_identity,
        "metadata": {
            "agent_name": agent.name,
            "agent_label": agent.label,
            "agent_base_url": agent.base_url,
            "foundry_url": normalized_foundry_url,
            "git": git_context,
            "github": {
                "login": github_identity.get("login"),
                "id": github_identity.get("id"),
                "name": github_identity.get("name"),
                "html_url": github_identity.get("html_url"),
                "token_source": github_token_source,
            },
        },
    }

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.post(
            f"{normalized_foundry_url}/api/developer/notification-preferences",
            headers=headers,
            json=payload,
        )
    if not response.is_success:
        detail = response.text
        try:
            raw_error = response.json()
            if isinstance(raw_error, dict):
                detail = str(raw_error.get("detail") or raw_error.get("message") or response.text)
        except Exception:
            pass
        raise HTTPException(
            status_code=502,
            detail=f"Foundry notification preference sync failed (status {response.status_code}): {detail}",
        )

    raw_result = response.json()
    upstream = raw_result if isinstance(raw_result, dict) else {"value": raw_result}
    local_preferences = _write_agent_notification_preferences(
        agent.name,
        {
            "email": normalized_email,
            "bounty_success_email_enabled": enabled,
            "foundry_url": normalized_foundry_url,
            "status": "synced",
            "synced_at": _utcnow_iso(),
            "developer_identity": developer_identity,
            "github": {
                "login": github_identity.get("login"),
                "id": github_identity.get("id"),
                "token_source": github_token_source,
                "has_token": bool(resolved_github_token),
            },
            "upstream": upstream,
        },
    )
    return {
        "ok": True,
        "foundry_url": normalized_foundry_url,
        "preferences": local_preferences,
        "upstream": upstream,
        "github": {
            "login": github_identity.get("login"),
            "id": github_identity.get("id"),
            "token_source": github_token_source,
            "has_token": bool(resolved_github_token),
        },
    }


async def _probe_json(client: httpx.AsyncClient, url: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        response = await client.get(url)
    except Exception as exc:
        logger.info("Probe request failed for %s: %s", url, exc)
        return None, {"ok": False, "url": url, "detail": "request_failed"}

    summary: dict[str, Any] = {
        "ok": response.is_success,
        "url": url,
        "status_code": response.status_code,
    }
    try:
        payload = response.json()
    except Exception:
        payload = {"non_json": True}
    return payload if isinstance(payload, dict) else {"value": payload}, summary


async def _probe_route(
    client: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.request(method, url, json=json_body)
    except Exception as exc:
        logger.warning("Route probe failed for %s %s", method, url, exc_info=exc)
        return {"url": url, "method": method, "ok": False, "detail": "request_failed"}

    return {
        "url": url,
        "method": method,
        "ok": response.status_code in {200, 400, 401, 403, 405, 409, 422},
        "status_code": response.status_code,
    }


def _handshake_checks(
    *,
    requested_foundry_url: str,
    bootstrap_state: dict[str, Any] | None,
    foundry_health: dict[str, Any] | None,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    state = bootstrap_state or {}

    if not state.get("enabled"):
        checks.append(
            {
                "level": "warn",
                "message": "Agent bootstrap is disabled. Restart the agent with FOUNDRY_DISCOVERY_ENABLE=true to test real onboarding.",
            }
        )
    else:
        checks.append(
            {
                "level": "info",
                "message": f"Agent bootstrap is enabled. discovery={state.get('discovery_status') or 'n/a'}, register={state.get('registration_status') or 'n/a'}.",
            }
        )

    configured_foundry_url = _normalize_url(str(state.get("foundry_base_url") or ""))
    if requested_foundry_url and configured_foundry_url and requested_foundry_url != configured_foundry_url:
        checks.append(
            {
                "level": "warn",
                "message": f"Requested Foundry URL {requested_foundry_url} does not match the agent bootstrap config {configured_foundry_url}.",
            }
        )

    if state.get("last_error"):
        checks.append(
            {
                "level": "error",
                "message": f"Agent bootstrap last_error: {state['last_error']}",
            }
        )

    if foundry_health and not foundry_health.get("ok"):
        checks.append(
            {
                "level": "error",
                "message": f"Foundry health probe failed: {foundry_health.get('detail') or foundry_health.get('status_code') or 'unknown error'}.",
            }
        )

    if state.get("registration_status") == "APPROVED":
        checks.append(
            {
                "level": "info",
                "message": "Agent reports APPROVED status. The invite/register/approval loop has completed at least once.",
            }
        )

    if not checks:
        checks.append({"level": "info", "message": "No handshake issues detected."})
    return checks


app = FastAPI(title="Foundry-lite API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_origin_regex=_cors_allowed_origin_regex(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_local_agent_manager() -> None:
    LOCAL_AGENT_MANAGER.ensure_runtime_files()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/api/local-agent-templates")
async def list_local_agent_templates() -> list[LocalAgentTemplate]:
    return [LocalAgentTemplate.model_validate(item) for item in LOCAL_AGENT_MANAGER.list_templates()]


@app.get("/api/local-agents")
async def list_local_agents(include_retired: bool = False) -> list[LocalAgentRuntime]:
    return [
        LocalAgentRuntime.model_validate(item)
        for item in LOCAL_AGENT_MANAGER.list_agents(include_retired=include_retired)
    ]


@app.get("/api/local-agents/{agent_name}/notification-preferences")
async def get_local_agent_notification_preferences(agent_name: str) -> dict[str, Any]:
    agents = _load_agents()
    if agent_name not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _read_agent_notification_preferences(agent_name)


@app.post("/api/local-agents")
async def create_local_agent(request: LocalAgentCreateRequest) -> LocalAgentRuntime:
    try:
        item = LOCAL_AGENT_MANAGER.create_agent(
            template_id=request.template_id,
            name=request.name,
            label=request.label,
            preferred_port=request.preferred_port,
            foundry_url=request.foundry_url,
            start=request.auto_start,
        )
    except ValueError as exc:
        logger.info("Invalid local agent create request for %s: %s", request.name, exc)
        raise HTTPException(status_code=400, detail=str(exc) or "Local agent request is invalid") from exc
    except RuntimeError as exc:
        logger.warning("Local agent creation failed for %s", request.name, exc_info=exc)
        raise HTTPException(status_code=409, detail="Local agent could not be created") from exc
    LOCAL_AGENT_MANAGER.ensure_runtime_files()
    return LocalAgentRuntime.model_validate(item)


@app.post("/api/local-agents/{agent_name}/start")
async def start_local_agent(agent_name: str) -> LocalAgentRuntime:
    try:
        item = LOCAL_AGENT_MANAGER.start_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except RuntimeError as exc:
        logger.warning("Local agent start failed for %s", agent_name, exc_info=exc)
        raise HTTPException(status_code=409, detail="Local agent configuration is invalid") from exc
    LOCAL_AGENT_MANAGER.ensure_runtime_files()
    return LocalAgentRuntime.model_validate(item)


@app.post("/api/local-agents/{agent_name}/stop")
async def stop_local_agent(agent_name: str) -> LocalAgentRuntime:
    try:
        item = LOCAL_AGENT_MANAGER.stop_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except RuntimeError as exc:
        logger.warning("Local agent stop failed for %s", agent_name, exc_info=exc)
        raise HTTPException(status_code=409, detail="Local agent configuration is invalid") from exc
    LOCAL_AGENT_MANAGER.ensure_runtime_files()
    return LocalAgentRuntime.model_validate(item)


@app.post("/api/local-agents/{agent_name}/retire")
async def retire_local_agent(agent_name: str, request: RetireAgentRequest) -> dict[str, Any]:
    agents = _load_agents()
    agent = agents.get(agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    bootstrap_state = await _agent_bootstrap_state(agent)
    foundry_url = _normalize_url(str(bootstrap_state.get("foundry_base_url") or "") or request.foundry_url)
    foundry_agent_name = str(
        bootstrap_state.get("registered_agent_name")
        or bootstrap_state.get("invite_expected_name")
        or agent.name
    ).strip()
    has_foundry_registration = bool(
        str(bootstrap_state.get("registered_agent_name") or "").strip()
        or str(bootstrap_state.get("registration_agent_id") or "").strip()
        or str(bootstrap_state.get("invite_expected_name") or "").strip()
    )
    if has_foundry_registration:
        try:
            remote_result = await _retire_foundry_agent(
                agent=agent,
                foundry_agent_name=foundry_agent_name,
                foundry_url=foundry_url,
                request=request,
            )
        except Exception as exc:
            remote_result = _foundry_retire_failure_result(
                foundry_url=foundry_url,
                foundry_agent_name=foundry_agent_name,
                exc=exc,
            )
            if isinstance(exc, HTTPException):
                logger.warning(
                    "Foundry remote retire failed for %s (%s): %s; continuing with local retire",
                    foundry_agent_name,
                    exc.status_code,
                    remote_result.get("message") or "remote retire failed",
                )
            else:
                logger.warning(
                    "Foundry remote retire failed for %s; continuing with local retire",
                    foundry_agent_name,
                    exc_info=exc,
                )
    else:
        remote_result = {
            "ok": True,
            "foundry_url": foundry_url,
            "agent_name": "",
            "status": "NOT_REGISTERED",
            "message": "No Foundry registration was observed; retired local runtime only.",
        }

    try:
        cloud_run_cleanup = CLOUD_RUN_MANAGER.cleanup_agent(agent_name)
    except Exception as exc:
        logger.warning("Cloud Run cleanup failed for retired agent %s", agent_name, exc_info=exc)
        cloud_run_cleanup = {
            "ok": False,
            "agent_name": agent_name,
            "error": str(exc),
            "actions": [],
            "targets": [],
        }

    local_runtime: dict[str, Any] | None = None
    if request.stop_local:
        try:
            local_runtime = LOCAL_AGENT_MANAGER.retire_agent(agent_name, remote_result=remote_result)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Agent not found") from exc
        except RuntimeError as exc:
            logger.warning("Local retire failed for %s", agent_name, exc_info=exc)
            raise HTTPException(status_code=409, detail="Local agent configuration is invalid") from exc
        LOCAL_AGENT_MANAGER.ensure_runtime_files()

    return {
        "ok": True,
        "agent_name": agent_name,
        "foundry": remote_result,
        "cloud_run": cloud_run_cleanup,
        "local_agent": local_runtime,
    }


# ---------------------------------------------------------------------------
# Cloud Run deployment endpoints
# ---------------------------------------------------------------------------


@app.get("/api/cloud-run/status")
async def cloud_run_status() -> dict[str, Any]:
    return await asyncio.to_thread(CLOUD_RUN_MANAGER.status)


@app.post("/api/cloud-run/auth/start")
async def start_cloud_run_auth() -> dict[str, Any]:
    try:
        return CLOUD_RUN_MANAGER.start_auth_session()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/cloud-run/auth/{session_id}")
async def get_cloud_run_auth(session_id: str) -> dict[str, Any]:
    try:
        return CLOUD_RUN_MANAGER.get_auth_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/cloud-run/auth/{session_id}/code")
async def submit_cloud_run_auth_code(session_id: str, request: CloudRunAuthCodeRequest) -> dict[str, Any]:
    try:
        return CLOUD_RUN_MANAGER.submit_auth_code(session_id, request.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/cloud-run/auth/{session_id}/cancel")
async def cancel_cloud_run_auth(session_id: str) -> dict[str, Any]:
    try:
        return CLOUD_RUN_MANAGER.cancel_auth_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/cloud-run/deployments")
async def list_cloud_run_deployments(limit: int = 20) -> list[dict[str, Any]]:
    return CLOUD_RUN_MANAGER.list_deployments(limit=limit)


@app.get("/api/cloud-run/runtimes")
async def list_cloud_run_runtimes(project: str = "", region: str = "") -> dict[str, Any]:
    return await asyncio.to_thread(CLOUD_RUN_MANAGER.list_live_runtimes, project=project, region=region)


@app.get("/api/cloud-run/deployments/{job_id}")
async def get_cloud_run_deployment(job_id: str) -> dict[str, Any]:
    try:
        return CLOUD_RUN_MANAGER.get_deployment(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/cloud-run/deploy")
async def deploy_cloud_run(request: CloudRunDeployRequest) -> dict[str, Any]:
    try:
        _, item = LOCAL_AGENT_MANAGER._find_agent(request.agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    instance_dir = Path(item.get("instance_dir", ""))
    foundry_url = request.foundry_url or str(item.get("foundry_url") or "")
    try:
        return CLOUD_RUN_MANAGER.start_deployment(
            agent_name=str(item.get("name") or request.agent_name),
            instance_dir=instance_dir,
            foundry_url=foundry_url,
            project=request.project,
            region=request.region,
            min_instances=request.min_instances,
            memory=request.memory,
            cpu=request.cpu,
            poll_schedule=request.poll_schedule,
            skip_scheduler=request.skip_scheduler,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("Cloud Run deployment failed to start for %s", request.agent_name, exc_info=exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/cloud-run/deployments/{job_id}/cancel")
async def cancel_cloud_run_deployment(job_id: str) -> dict[str, Any]:
    try:
        return CLOUD_RUN_MANAGER.cancel_deployment(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Skill Store endpoints
# ---------------------------------------------------------------------------


class InstallSkillRequest(BaseModel):
    skill_id: str


@app.get("/api/skill-store")
async def list_skill_store(
    category: str = "",
    tag: str = "",
) -> list[dict[str, Any]]:
    """Browse all available skills in the store."""
    return SKILL_STORE.list_store(category=category, tag=tag)


@app.get("/api/skill-store/{skill_id}")
async def get_store_skill(skill_id: str) -> dict[str, Any]:
    """Get full details for a store skill (including SKILL.md content)."""
    skill = SKILL_STORE.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found in store")
    return skill


@app.get("/api/local-agents/{agent_name}/skills")
async def list_agent_skills(agent_name: str) -> list[dict[str, Any]]:
    """List skills installed in a local agent."""
    try:
        _, item = LOCAL_AGENT_MANAGER._find_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    instance_dir = Path(item.get("instance_dir", ""))
    if not instance_dir.exists():
        raise HTTPException(status_code=404, detail="Agent instance directory not found")
    return SKILL_STORE.list_agent_skills(instance_dir)


@app.post("/api/local-agents/{agent_name}/skills/install")
async def install_agent_skill(agent_name: str, request: InstallSkillRequest) -> dict[str, Any]:
    """Install a skill from the store into a local agent."""
    try:
        _, item = LOCAL_AGENT_MANAGER._find_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    instance_dir = Path(item.get("instance_dir", ""))
    if not instance_dir.exists():
        raise HTTPException(status_code=404, detail="Agent instance directory not found")
    try:
        result = SKILL_STORE.install_skill(instance_dir, request.skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.delete("/api/local-agents/{agent_name}/skills/{skill_id}")
async def uninstall_agent_skill(agent_name: str, skill_id: str) -> dict[str, Any]:
    """Remove a skill from a local agent."""
    try:
        _, item = LOCAL_AGENT_MANAGER._find_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    instance_dir = Path(item.get("instance_dir", ""))
    if not instance_dir.exists():
        raise HTTPException(status_code=404, detail="Agent instance directory not found")
    try:
        result = SKILL_STORE.uninstall_skill(instance_dir, skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.get("/api/agents")
async def list_agents() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for agent in _load_agents().values():
        manifest = None
        error = ""
        try:
            manifest = (await AgentClient(agent.base_url).manifest()).model_dump(mode="json")
        except Exception as exc:
            logger.warning("Manifest probe failed for %s", agent.base_url, exc_info=exc)
            error = "Manifest unavailable"
        result.append(
            {
                "name": agent.name,
                "label": agent.label,
                "base_url": agent.base_url,
                "manifest": manifest,
                "status": "online" if manifest else "offline",
                "error": error,
            }
        )
    return result


@app.get("/api/agents/{agent_name}/manifest")
async def get_manifest(agent_name: str) -> dict[str, Any]:
    agents = _load_agents()
    if agent_name not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    manifest = await AgentClient(agents[agent_name].base_url).manifest()
    return manifest.model_dump(mode="json")


@app.post("/api/handshake/probe")
async def probe_handshake(request: LiteHandshakeProbeRequest) -> dict[str, Any]:
    agents = _load_agents()
    agent = agents.get(request.agent_name) or _agent_config_from_local_registry(request.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent_state_url = f"{agent.base_url.rstrip('/')}/foundry/bootstrap/state"
    agent_card_url = f"{agent.base_url.rstrip('/')}/.well-known/agent-card.json"

    normalized_foundry_url = _normalize_url(request.foundry_url)
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        bootstrap_state, bootstrap_probe = await _probe_json(client, agent_state_url)
        agent_card, agent_card_probe = await _probe_json(client, agent_card_url)
        source_state, source_state_path = _read_agent_source_bootstrap_state(agent.name)
        if not bool((bootstrap_probe or {}).get("ok")):
            if source_state:
                bootstrap_state = _public_bootstrap_state(source_state)
                bootstrap_probe = {
                    "ok": True,
                    "url": str(source_state_path or ""),
                    "detail": "source_state_fallback",
                }

        cloud_run_state = await _agent_cloud_run_bootstrap_state(agent.name, source_state)
        if cloud_run_state:
            bootstrap_state = _public_bootstrap_state(cloud_run_state)
            bootstrap_probe = {
                "ok": True,
                "url": "cloud_run_bootstrap_state",
                "detail": "cloud_run_state",
            }

        if not normalized_foundry_url and isinstance(bootstrap_state, dict):
            normalized_foundry_url = _normalize_url(str(bootstrap_state.get("foundry_base_url") or ""))

        foundry_health_payload: dict[str, Any] | None = None
        foundry_health_probe: dict[str, Any] | None = None
        foundry_routes: dict[str, Any] = {}
        if normalized_foundry_url:
            foundry_health_payload, foundry_health_probe = await _probe_json(client, f"{normalized_foundry_url}/health")

    return {
        "agent": {
            "name": agent.name,
            "label": agent.label,
            "base_url": agent.base_url,
            "bootstrap_state_url": agent_state_url,
            "agent_card_url": agent_card_url,
            "bootstrap_probe": bootstrap_probe,
            "bootstrap_state": bootstrap_state or {"enabled": False},
            "agent_card_probe": agent_card_probe,
            "agent_card": agent_card or {},
        },
        "foundry": {
            "url": normalized_foundry_url,
            "health_probe": foundry_health_probe or {},
            "health_payload": foundry_health_payload or {},
            "routes": foundry_routes,
        },
        "checks": _handshake_checks(
            requested_foundry_url=normalized_foundry_url,
            bootstrap_state=bootstrap_state,
            foundry_health=foundry_health_probe,
        ),
    }


@app.post("/api/developer/context")
async def developer_context(request: DeveloperContextRequest) -> dict[str, Any]:
    git_context = _git_context()
    token, token_source = _discover_github_token()
    github_identity = await _github_identity(token)
    github_identity["token_source"] = token_source
    github_identity["has_token"] = bool(token)

    normalized_foundry_url = _normalize_url(request.foundry_url)
    route_probes = await _developer_route_probes(normalized_foundry_url) if normalized_foundry_url else {}

    return {
        "git": git_context,
        "github": github_identity,
        "foundry": {
            "url": normalized_foundry_url,
            "routes": route_probes,
        },
        "developer_identity": _developer_identity_from_context(git_context, github_identity),
    }


@app.post("/api/developer/notification-preferences/sync")
async def sync_developer_notification_preferences(
    request: DeveloperNotificationPreferencesSyncRequest,
) -> dict[str, Any]:
    agents = _load_agents()
    agent = agents.get(request.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    bootstrap_state = await _agent_bootstrap_state(agent)
    normalized_foundry_url = _normalize_url(request.foundry_url or str(bootstrap_state.get("foundry_base_url") or ""))
    return await _sync_developer_notification_preferences(
        agent=agent,
        foundry_url=normalized_foundry_url,
        developer_token=request.developer_token,
        github_token=request.github_token,
        email=request.email,
        bounty_success_email_enabled=request.bounty_success_email_enabled,
    )


@app.post("/api/developer/bootstrap-ticket")
async def developer_bootstrap_ticket(request: DeveloperBootstrapTicketRequest) -> dict[str, Any]:
    agents = _load_agents()
    agent = agents.get(request.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    bootstrap_state = await _agent_bootstrap_state(agent)
    normalized_foundry_url = _normalize_url(request.foundry_url or str(bootstrap_state.get("foundry_base_url") or ""))
    if not normalized_foundry_url:
        raise HTTPException(status_code=400, detail="Foundry URL is required")
    runtime_target = str(request.runtime_target or "local").strip().lower() or "local"
    install_to_source_only = runtime_target in {"cloud_run", "source", "source_state"}

    git_context = _git_context()
    github_token, github_token_source = _discover_github_token_for_foundry(
        request.github_token,
        normalized_foundry_url,
    )
    github_identity = await _github_identity(github_token)
    github_identity["token_source"] = github_token_source
    github_identity["has_token"] = bool(github_token)
    developer_identity = _developer_identity_from_context(git_context, github_identity)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    developer_token = str(request.developer_token or "").strip()
    if developer_token:
        headers["Authorization"] = f"Bearer {developer_token}"
    if github_token:
        headers["X-GitHub-Token"] = github_token

    payload = {
        "agent_name": agent.name,
        "agent_label": agent.label,
        "agent_base_url": agent.base_url,
        "bootstrap_delivery": str(request.bootstrap_delivery or "poll").strip().lower() or "poll",
        "runtime_target": runtime_target,
        "developer_identity": developer_identity,
        "github": {
            "login": github_identity.get("login"),
            "id": github_identity.get("id"),
            "name": github_identity.get("name"),
            "html_url": github_identity.get("html_url"),
        },
        "git": git_context,
        "bootstrap_state": bootstrap_state,
    }

    route_attempts: list[dict[str, Any]] = []
    ticket_response: dict[str, Any] | None = None
    ticket_route = ""
    failure_message = ""
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        for route_path in ("/api/developer/bootstrap-ticket", "/api/dev/bootstrap-ticket"):
            url = f"{normalized_foundry_url}{route_path}"
            try:
                response = await client.post(url, headers=headers, json=payload)
            except Exception as exc:
                logger.warning("Bootstrap ticket request failed for %s", url, exc_info=exc)
                route_attempts.append({"url": url, "ok": False, "detail": "request_failed"})
                continue

            entry = {
                "url": url,
                "status_code": response.status_code,
                "ok": response.is_success,
            }
            if not response.is_success:
                entry["detail"] = "upstream_error"
            route_attempts.append(entry)
            if response.status_code in {404, 405}:
                continue
            if not response.is_success:
                failure_message = f"Foundry bootstrap ticket request failed (status {response.status_code})"
                continue
            raw_payload = response.json()
            ticket_response = raw_payload if isinstance(raw_payload, dict) else {"value": raw_payload}
            ticket_route = url
            break

    if not ticket_response:
        return {
            "ok": False,
            "foundry_url": normalized_foundry_url,
            "route_attempts": route_attempts,
            "git": git_context,
            "github": github_identity,
            "developer_identity": developer_identity,
            "message": failure_message or "No supported bootstrap-ticket endpoint was found on the target Foundry.",
        }

    claim_token = str(ticket_response.get("discovery_claim_token") or ticket_response.get("claim_token") or "").strip()
    apply_result: dict[str, Any] | None = None
    apply_mode = ""
    cloud_run_apply_result: dict[str, Any] = {}
    if claim_token:
        ticket_developer_identity = ticket_response.get("developer_identity")
        if not isinstance(ticket_developer_identity, dict):
            ticket_developer_identity = {}
        claim_developer_identity = {
            **developer_identity,
            **ticket_developer_identity,
            "developer_id": ticket_response.get("developer_id"),
            "developer_label": ticket_response.get("developer_label"),
            "ticket_id": ticket_response.get("ticket_id"),
        }
        apply_payload = {
            "discovery_claim_token": claim_token,
            "bootstrap_delivery": str(ticket_response.get("bootstrap_delivery") or request.bootstrap_delivery or "poll"),
            "foundry_base_url": normalized_foundry_url,
            "public_base_url": agent.base_url,
            "developer_identity": claim_developer_identity,
            "force_rediscover": bool(request.force_rediscover),
        }
        if install_to_source_only:
            try:
                apply_result = _install_developer_claim_to_source(agent.name, apply_payload)
                apply_mode = "source_state"
            except Exception as exc:
                logger.warning("Developer claim source install failed for %s", agent.name, exc_info=exc)
                raise HTTPException(status_code=502, detail="Failed to install discovery claim on agent source") from exc
            if runtime_target == "cloud_run":
                if _latest_cloud_run_deployment(agent.name):
                    task = asyncio.create_task(
                        _apply_developer_claim_to_cloud_run(
                            agent.name,
                            normalized_foundry_url,
                            apply_payload,
                        )
                    )
                    task.add_done_callback(_log_cloud_run_claim_apply_result)
                    cloud_run_apply_result = {
                        "ok": True,
                        "status": "scheduled",
                        "message": "Existing Cloud Run deployment will receive the claim in the background.",
                    }
                    apply_mode = "source_state+cloud_run_pending"
                else:
                    cloud_run_apply_result = {
                        "ok": False,
                        "status": "not_deployed",
                        "message": "No existing Cloud Run deployment found; the claim will be carried by the next deploy.",
                    }
        else:
            runtime_apply_failed = False
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                try:
                    response = await client.post(
                        f"{agent.base_url.rstrip('/')}/foundry/bootstrap/developer-claim",
                        json=apply_payload,
                    )
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    runtime_apply_failed = True
                    logger.warning("Developer claim apply failed for %s; falling back to source state", agent.base_url, exc_info=exc)
            if not runtime_apply_failed and response.is_success:
                raw_apply = response.json()
                apply_result = raw_apply if isinstance(raw_apply, dict) else {"value": raw_apply}
                apply_mode = "runtime"
            else:
                if not runtime_apply_failed:
                    logger.warning(
                        "Developer claim apply failed for %s with status %s; falling back to source state",
                        agent.base_url,
                        response.status_code,
                    )
                try:
                    apply_result = _install_developer_claim_to_source(agent.name, apply_payload)
                    apply_mode = "source_state"
                except Exception as exc:
                    logger.warning("Developer claim source fallback failed for %s", agent.name, exc_info=exc)
                    raise HTTPException(status_code=502, detail="Failed to apply discovery claim to runtime or source") from exc

    env_lines = [
        "FOUNDRY_DISCOVERY_ENABLE=true",
        f"FOUNDRY_BASE_URL={normalized_foundry_url}",
        f"FOUNDRY_AGENT_PUBLIC_URL={'<set by Cloud Run deploy>' if runtime_target == 'cloud_run' else agent.base_url}",
        f"FOUNDRY_BOOTSTRAP_DELIVERY={str(ticket_response.get('bootstrap_delivery') or request.bootstrap_delivery or 'poll')}",
    ]
    if claim_token:
        env_lines.append("FOUNDRY_DISCOVERY_CLAIM_TOKEN=<redacted>")
    if claim_token and isinstance(apply_payload.get("developer_identity"), dict):
        env_lines.append(f"FOUNDRY_DEVELOPER_IDENTITY_JSON={json.dumps(apply_payload['developer_identity'], ensure_ascii=False)}")
    safe_ticket_response = dict(ticket_response)
    for secret_key in ("discovery_claim_token", "claim_token", "agent_secret", "api_key", "token"):
        if secret_key in safe_ticket_response:
            safe_ticket_response[secret_key] = "<redacted>"

    return {
        "ok": True,
        "foundry_url": normalized_foundry_url,
        "ticket_route": ticket_route,
        "route_attempts": route_attempts,
        "git": git_context,
        "github": github_identity,
        "developer_identity": developer_identity,
        "ticket": safe_ticket_response,
        "claim_applied": bool(apply_result),
        "apply_mode": apply_mode,
        "apply_result": apply_result,
        "cloud_run_apply": cloud_run_apply_result,
        "env_snippet": "\n".join(env_lines),
    }


@app.post("/api/chat")
async def chat(request: LiteChatRequest) -> StreamingResponse:
    agents = _load_agents()
    agent = agents.get(request.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    conversation_id = request.conversation_id or str(uuid.uuid4())
    history = TRANSCRIPTS.setdefault(conversation_id, [])
    context = _render_context(history)
    metadata = dict(request.metadata)
    dev_overrides = _normalized_dev_overrides(request.dev_overrides)
    if dev_overrides:
        metadata["dev_overrides"] = dev_overrides
    payload = {
        "message": request.message,
        "content": request.message,
        "mode": request.mode.value,
        "username": request.username,
        "user_id": f"lite_{request.username}",
        "conversation_id": conversation_id,
        "session_id": conversation_id,
        "context": context,
        "history": _history_messages(history),
        "metadata": metadata,
        "stream": True,
    }

    async def event_generator():
        reply_buffer = ""
        final_emitted = False
        metadata: dict[str, Any] = {}
        client = AgentClient(agent.base_url)
        try:
            async for event_type, event_data in client.stream_foundry_chat(payload):
                if event_type == "step":
                    yield _sse_event("step", event_data)
                    continue
                if event_type == "token":
                    chunk = str(event_data.get("content") or "")
                    reply_buffer += chunk
                    yield _sse_event("token", {"content": chunk})
                    continue
                if event_type == "error":
                    yield _sse_event("error", event_data)
                    continue
                if event_type == "message":
                    reply = str(
                        event_data.get("content")
                        or event_data.get("reply")
                        or reply_buffer
                    )
                    if not final_emitted:
                        history.append({"user": request.message, "assistant": reply})
                        final_emitted = True
                    metadata = dict(event_data.get("metadata") or {})
                    yield _sse_event(
                        "message",
                        {
                            **event_data,
                            "agent_name": agent.name,
                            "conversation_id": conversation_id,
                            "reply": reply,
                            "content": reply,
                            "transcript": history,
                            "metadata": metadata,
                        },
                    )
                    continue
                if event_type == "done":
                    if not final_emitted and reply_buffer:
                        history.append({"user": request.message, "assistant": reply_buffer})
                        final_emitted = True
                        yield _sse_event(
                            "message",
                            {
                                "agent_name": agent.name,
                                "conversation_id": conversation_id,
                                "reply": reply_buffer,
                                "content": reply_buffer,
                                "transcript": history,
                                "metadata": metadata,
                            },
                        )
                    yield _sse_event("done", {})
                    return
            if not final_emitted and reply_buffer:
                history.append({"user": request.message, "assistant": reply_buffer})
                yield _sse_event(
                    "message",
                    {
                        "agent_name": agent.name,
                        "conversation_id": conversation_id,
                        "reply": reply_buffer,
                        "content": reply_buffer,
                        "transcript": history,
                        "metadata": metadata,
                    },
                )
            yield _sse_event("done", {})
        except Exception as exc:
            logger.exception("Chat stream failed for agent %s", agent.name, exc_info=exc)
            yield _sse_event("error", {"detail": "Chat stream failed"})
            yield _sse_event("done", {})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/sync", response_model=LiteChatResponse)
async def chat_sync(request: LiteChatRequest) -> LiteChatResponse:
    agents = _load_agents()
    agent = agents.get(request.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    conversation_id = request.conversation_id or str(uuid.uuid4())
    history = TRANSCRIPTS.setdefault(conversation_id, [])
    context = _render_context(history)
    metadata = dict(request.metadata)
    dev_overrides = _normalized_dev_overrides(request.dev_overrides)
    if dev_overrides:
        metadata["dev_overrides"] = dev_overrides
    response = await AgentClient(agent.base_url).chat(
        ChatRequest(
            message=request.message,
            mode=request.mode,
            username=request.username,
            conversation_id=conversation_id,
            session_id=conversation_id,
            context=context,
            history=_history_messages(history),
            metadata=metadata,
        )
    )
    history.append({"user": request.message, "assistant": response.reply})
    return LiteChatResponse(
        agent_name=agent.name,
        conversation_id=conversation_id,
        reply=response.reply,
        transcript=history,
        metadata=response.metadata,
    )


# ---------------------------------------------------------------------------
# Job Board – browse Foundry requirements / bounties
# ---------------------------------------------------------------------------

class FoundryJobsRequest(BaseModel):
    foundry_url: str = ""
    github_token: str = ""
    foundry_token: str = ""


class FoundryJobClaimRequest(BaseModel):
    foundry_url: str = ""
    agent_name: str = ""
    github_token: str = ""
    foundry_token: str = ""


@app.post("/api/foundry/jobs")
async def list_foundry_jobs(request: FoundryJobsRequest) -> dict[str, Any]:
    """Proxy to fetch open requirements/bounties from a Foundry instance."""
    normalized_foundry_url = _normalize_url(request.foundry_url)
    if not normalized_foundry_url:
        raise HTTPException(status_code=400, detail="Foundry URL is required")

    github_token, _token_source = _discover_github_token_for_foundry(
        request.github_token,
        normalized_foundry_url,
    )
    auth_token = request.foundry_token.strip() if request.foundry_token.strip() else github_token
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "ccfoundry-agent-kit-dev-board",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if github_token and not request.foundry_token.strip():
        headers["X-GitHub-Token"] = github_token

    jobs: list[dict[str, Any]] = []
    error_message = ""
    tried_routes: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        for route_path in (
            "/api/public/open-requirements",
            "/api/system/onboarding-requirements",
            "/api/system/discovery-policies",
        ):
            url = f"{normalized_foundry_url}{route_path}"
            try:
                response = await client.get(url, headers=headers)
            except Exception as exc:
                tried_routes.append({"url": url, "ok": False, "error": str(exc)})
                continue
            tried_routes.append({
                "url": url,
                "ok": response.is_success,
                "status_code": response.status_code,
            })
            if response.status_code in {404, 405}:
                continue
            if response.status_code == 429:
                error_message = "Rate limited by Foundry – try again shortly"
                continue
            if not response.is_success:
                error_message = f"Foundry returned status {response.status_code}"
                continue
            raw = response.json()
            if isinstance(raw, list):
                jobs = raw
            elif isinstance(raw, dict):
                jobs = raw.get("policies") or raw.get("items") or raw.get("requirements") or []
                if not isinstance(jobs, list):
                    jobs = [raw] if raw.get("id") else []
            break

    return {
        "ok": len(jobs) > 0 or not error_message,
        "foundry_url": normalized_foundry_url,
        "jobs": jobs,
        "count": len(jobs),
        "tried_routes": tried_routes,
        "error": error_message,
    }


@app.post("/api/foundry/jobs/{job_id}/claim")
async def claim_foundry_job(job_id: str, request: FoundryJobClaimRequest) -> dict[str, Any]:
    """Send a claim/bid for a specific bounty to Foundry on behalf of an agent."""
    normalized_foundry_url = _normalize_url(request.foundry_url)
    if not normalized_foundry_url:
        raise HTTPException(status_code=400, detail="Foundry URL is required")

    agents = _load_agents()
    agent = agents.get(request.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    github_token, _token_source = _discover_github_token_for_foundry(
        request.github_token,
        normalized_foundry_url,
    )
    auth_token = request.foundry_token.strip() if request.foundry_token.strip() else github_token
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ccfoundry-agent-kit-dev-board",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if github_token and not request.foundry_token.strip():
        headers["X-GitHub-Token"] = github_token

    claim_payload = {
        "policy_id": job_id,
        "agent_name": agent.name,
        "agent_label": agent.label,
        "agent_base_url": agent.base_url,
        "claim_message": f"Agent '{agent.label}' is ready to work on this requirement.",
    }

    claim_result: dict[str, Any] | None = None
    error_message = ""
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        for route_path in (
            f"/api/system/discovery-policies/{job_id}/claim",
            f"/api/registry/discover",
        ):
            url = f"{normalized_foundry_url}{route_path}"
            try:
                response = await client.post(url, headers=headers, json=claim_payload)
            except Exception as exc:
                error_message = str(exc)
                continue
            if response.status_code in {404, 405}:
                continue
            if not response.is_success:
                error_message = f"Foundry returned status {response.status_code}"
                try:
                    error_detail = response.json()
                    error_message += f": {error_detail}"
                except Exception:
                    pass
                continue
            claim_result = response.json() if isinstance(response.json(), dict) else {"ok": True}
            break

    if not claim_result:
        return {
            "ok": False,
            "job_id": job_id,
            "agent_name": agent.name,
            "error": error_message or "No supported claim endpoint found.",
        }

    return {
        "ok": True,
        "job_id": job_id,
        "agent_name": agent.name,
        "claim_result": claim_result,
    }


# ---------------------------------------------------------------------------
# Bounty Orchestration – proxy to agent bounty endpoints
# ---------------------------------------------------------------------------

class BountyScanRequest(BaseModel):
    foundry_url: str = ""
    agent_url: str = ""   # e.g. http://127.0.0.1:8088

class BountyExecuteRequest(BaseModel):
    foundry_url: str = ""
    agent_url: str = ""
    job_id: str = ""
    job_name: str = ""
    dry_run: bool = False


@app.post("/api/bounty/scan")
async def proxy_bounty_scan(request: BountyScanRequest) -> dict[str, Any]:
    """Proxy: ask an agent to scan Foundry for matching jobs."""
    agent_url = _normalize_local_agent_url(request.agent_url)
    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url must be a loopback http URL")
    foundry_url = _normalize_url(request.foundry_url)
    if request.foundry_url and not foundry_url:
        raise HTTPException(status_code=400, detail="foundry_url must be http(s); remote http requires opt-in")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{agent_url}/bounty/scan",
                json={"foundry_url": foundry_url},
            )
            return resp.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/bounty/execute")
async def proxy_bounty_execute(request: BountyExecuteRequest) -> dict[str, Any]:
    """Proxy: ask an agent to execute a bounty task."""
    agent_url = _normalize_local_agent_url(request.agent_url)
    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url must be a loopback http URL")
    foundry_url = _normalize_url(request.foundry_url)
    if request.foundry_url and not foundry_url:
        raise HTTPException(status_code=400, detail="foundry_url must be http(s); remote http requires opt-in")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{agent_url}/bounty/execute",
                json={
                    "foundry_url": foundry_url,
                    "job_id": request.job_id,
                    "job_name": request.job_name,
                    "dry_run": request.dry_run,
                },
            )
            return resp.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Settlement / Earnings proxy
# ---------------------------------------------------------------------------

class SettlementsRequest(BaseModel):
    foundry_url: str = ""
    agent_name: str = ""
    limit: int = 50


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _bootstrap_state_for_agent(agent_name: str) -> tuple[dict[str, Any], Path | None]:
    normalized = str(agent_name or "").strip()
    if not normalized:
        return {}, None

    candidates: list[Path] = []
    try:
        _, item = LOCAL_AGENT_MANAGER._find_agent(normalized)
        instance_dir = Path(str(item.get("instance_dir") or "")).expanduser()
        if str(instance_dir):
            candidates.append(instance_dir / ".foundry_bootstrap.json")
    except Exception:
        pass

    candidates.extend(
        [
            RUNTIME_DIR / "agents" / normalized / ".foundry_bootstrap.json",
            APP_DIR / "agents" / normalized / ".foundry_bootstrap.json",
        ]
    )
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        state = _read_json_dict(resolved)
        if state:
            return state, resolved
    return {}, None


def _lower_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _settlement_agent_name(settlement: dict[str, Any]) -> str:
    for key in ("agent_name", "agent", "payee_identity"):
        value = str(settlement.get(key) or "").strip()
        if value:
            return value
    for nested_key in ("settlement", "settlement_record", "verification_result", "accounting"):
        nested = settlement.get(nested_key)
        if isinstance(nested, dict):
            value = str(nested.get("agent_name") or nested.get("payee_identity") or "").strip()
            if value:
                return value
            for child_key in ("mandate", "settlement", "record"):
                child = nested.get(child_key)
                if isinstance(child, dict):
                    value = str(child.get("agent_name") or child.get("payee_identity") or "").strip()
                    if value:
                        return value
    return ""


def _fallback_foundry_agent_name(source_agent_name: str) -> str:
    normalized = str(source_agent_name or "").strip()
    if not normalized:
        return ""
    if normalized.endswith("_agent_ext"):
        return normalized
    return f"{normalized}_agent_ext"


@app.post("/api/settlements")
async def proxy_settlements(request: SettlementsRequest) -> dict[str, Any]:
    """Proxy: fetch settlement records from Foundry for an agent."""
    foundry_url = _normalize_url(request.foundry_url)
    foundry_agent_name = ""
    bootstrap_path = ""

    # Resolve the Foundry-registered identity from the selected source agent.
    # This is the same identity used by both local debug runtimes and Cloud Run
    # workers, so earnings are runtime-agnostic.
    if request.agent_name:
        bs, path = _bootstrap_state_for_agent(request.agent_name)
        bootstrap_path = str(path or "")
        foundry_agent_name = str(bs.get("registered_agent_name") or "").strip()
        if not foundry_url:
            foundry_url = _normalize_url(str(bs.get("foundry_base_url") or ""))

    if not foundry_url:
        return {
            "settlements": [],
            "count": 0,
            "error": "No foundry_url provided",
            "agent_name": request.agent_name,
            "foundry_agent_name": foundry_agent_name,
            "matched_agent_names": [],
        }

    try:
        requested_limit = max(1, min(int(request.limit or 50), 500))
        fetch_limit = max(requested_limit, 200) if request.agent_name else requested_limit
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{foundry_url}/api/public/settlements",
                params={"limit": fetch_limit},
            )
            if resp.status_code != 200:
                return {
                    "settlements": [],
                    "count": 0,
                    "error": f"Foundry returned {resp.status_code}: {resp.text[:200]}",
                    "agent_name": request.agent_name,
                    "foundry_agent_name": foundry_agent_name,
                    "matched_agent_names": [],
                }
            data = resp.json()
            all_settlements = data.get("settlements", [])
            if not isinstance(all_settlements, list):
                all_settlements = []

            # Build set of names to match: source name + Foundry-registered name.
            match_names: set[str] = set()
            if request.agent_name:
                match_names.add(_lower_name(request.agent_name))
                fallback_name = _fallback_foundry_agent_name(request.agent_name)
                if fallback_name:
                    match_names.add(_lower_name(fallback_name))
                    if not foundry_agent_name:
                        foundry_agent_name = fallback_name
            if foundry_agent_name:
                match_names.add(_lower_name(foundry_agent_name))

            if match_names:
                filtered = [
                    s for s in all_settlements
                    if isinstance(s, dict) and _lower_name(_settlement_agent_name(s)) in match_names
                ]
            else:
                filtered = [s for s in all_settlements if isinstance(s, dict)]

            limited = filtered[:requested_limit]
            return {
                "settlements": limited,
                "count": len(limited),
                "total_available": len(filtered),
                "agent_name": request.agent_name,
                "foundry_agent_name": foundry_agent_name,
                "matched_agent_names": sorted(name for name in match_names if name),
                "bootstrap_path": bootstrap_path,
                "foundry_url": foundry_url,
            }
    except Exception as exc:
        return {
            "settlements": [],
            "count": 0,
            "error": str(exc),
            "agent_name": request.agent_name,
            "foundry_agent_name": foundry_agent_name,
            "matched_agent_names": [],
        }
