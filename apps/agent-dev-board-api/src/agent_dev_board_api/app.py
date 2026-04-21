from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ccfoundry_agent_kit import AgentClient, ChatRequest, ContextMode
from agent_dev_board_api.local_agents import LocalAgentManager


APP_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
AGENTS_FILE = Path(os.getenv("CCFOUNDRY_AGENTS_FILE", str(APP_DIR / "agents.yaml"))).resolve()
RUNTIME_DIR = AGENTS_FILE.parent
VENV_PYTHON = Path(os.getenv("CCFOUNDRY_DEV_VENV_PYTHON", str(REPO_ROOT / ".venv" / "bin" / "python"))).expanduser()
TRANSCRIPTS: dict[str, list[dict[str, str]]] = {}


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


class LocalAgentCreateRequest(BaseModel):
    template_id: str = "me_agent"
    name: str
    label: str = ""
    preferred_port: int | None = None
    foundry_url: str = ""


LOCAL_AGENT_MANAGER = LocalAgentManager(
    repo_root=REPO_ROOT,
    runtime_dir=RUNTIME_DIR,
    agents_file=AGENTS_FILE,
    venv_python=VENV_PYTHON,
)


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
        value = f"http://{value}"
    return value


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
            return {
                "authenticated": False,
                "status_code": response.status_code,
                "detail": str((payload or {}).get("message") or response.text[:200]).strip(),
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
        return {
            "authenticated": False,
            "detail": str(exc),
        }


async def _agent_bootstrap_state(agent: LiteAgentConfig) -> dict[str, Any]:
    url = f"{agent.base_url.rstrip('/')}/foundry/bootstrap/state"
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        payload, _ = await _probe_json(client, url)
    return payload if isinstance(payload, dict) else {"enabled": False}


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


async def _probe_json(client: httpx.AsyncClient, url: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        response = await client.get(url)
    except Exception as exc:
        return None, {"ok": False, "url": url, "detail": str(exc)}

    summary: dict[str, Any] = {
        "ok": response.is_success,
        "url": url,
        "status_code": response.status_code,
    }
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text[:400]}
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
        return {"url": url, "method": method, "ok": False, "detail": str(exc)}

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
    allow_origins=["*"],
    allow_credentials=True,
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
async def list_local_agents() -> list[LocalAgentRuntime]:
    return [LocalAgentRuntime.model_validate(item) for item in LOCAL_AGENT_MANAGER.list_agents()]


@app.post("/api/local-agents")
async def create_local_agent(request: LocalAgentCreateRequest) -> LocalAgentRuntime:
    try:
        item = LOCAL_AGENT_MANAGER.create_agent(
            template_id=request.template_id,
            name=request.name,
            label=request.label,
            preferred_port=request.preferred_port,
            foundry_url=request.foundry_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    LOCAL_AGENT_MANAGER.ensure_runtime_files()
    return LocalAgentRuntime.model_validate(item)


@app.post("/api/local-agents/{agent_name}/start")
async def start_local_agent(agent_name: str) -> LocalAgentRuntime:
    try:
        item = LOCAL_AGENT_MANAGER.start_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    LOCAL_AGENT_MANAGER.ensure_runtime_files()
    return LocalAgentRuntime.model_validate(item)


@app.post("/api/local-agents/{agent_name}/stop")
async def stop_local_agent(agent_name: str) -> LocalAgentRuntime:
    try:
        item = LOCAL_AGENT_MANAGER.stop_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    LOCAL_AGENT_MANAGER.ensure_runtime_files()
    return LocalAgentRuntime.model_validate(item)


@app.get("/api/agents")
async def list_agents() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for agent in _load_agents().values():
        manifest = None
        error = ""
        try:
            manifest = (await AgentClient(agent.base_url).manifest()).model_dump(mode="json")
        except Exception as exc:
            error = str(exc)
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
    agent = agents.get(request.agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent_state_url = f"{agent.base_url.rstrip('/')}/foundry/bootstrap/state"
    agent_card_url = f"{agent.base_url.rstrip('/')}/.well-known/agent-card.json"

    normalized_foundry_url = _normalize_url(request.foundry_url)
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        bootstrap_state, bootstrap_probe = await _probe_json(client, agent_state_url)
        agent_card, agent_card_probe = await _probe_json(client, agent_card_url)

        if not normalized_foundry_url and isinstance(bootstrap_state, dict):
            normalized_foundry_url = _normalize_url(str(bootstrap_state.get("foundry_base_url") or ""))

        foundry_health_payload: dict[str, Any] | None = None
        foundry_health_probe: dict[str, Any] | None = None
        foundry_routes: dict[str, Any] = {}
        if normalized_foundry_url:
            foundry_health_payload, foundry_health_probe = await _probe_json(client, f"{normalized_foundry_url}/health")
            foundry_routes = {
                "discover": await _probe_route(
                    client,
                    f"{normalized_foundry_url}/api/registry/discover",
                    method="POST",
                    json_body={},
                ),
                "register": await _probe_route(
                    client,
                    f"{normalized_foundry_url}/api/registry/register",
                    method="POST",
                    json_body={},
                ),
            }

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

    git_context = _git_context()
    github_token, github_token_source = _discover_github_token(request.github_token)
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
                route_attempts.append({"url": url, "ok": False, "detail": str(exc)})
                continue

            entry = {
                "url": url,
                "status_code": response.status_code,
                "ok": response.is_success,
            }
            detail_text = response.text[:400].strip()
            if detail_text:
                entry["detail"] = detail_text
            route_attempts.append(entry)
            if response.status_code in {404, 405}:
                continue
            if not response.is_success:
                failure_message = detail_text or f"status={response.status_code}"
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
    if claim_token:
        apply_payload = {
            "discovery_claim_token": claim_token,
            "bootstrap_delivery": str(ticket_response.get("bootstrap_delivery") or request.bootstrap_delivery or "poll"),
            "foundry_base_url": normalized_foundry_url,
            "public_base_url": agent.base_url,
            "developer_identity": {
                **developer_identity,
                "developer_id": ticket_response.get("developer_id"),
                "developer_label": ticket_response.get("developer_label"),
                "ticket_id": ticket_response.get("ticket_id"),
            },
            "force_rediscover": bool(request.force_rediscover),
        }
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.post(
                f"{agent.base_url.rstrip('/')}/foundry/bootstrap/developer-claim",
                json=apply_payload,
            )
        if not response.is_success:
            raise HTTPException(status_code=502, detail=f"Failed to apply discovery claim to agent: {response.text[:400]}")
        raw_apply = response.json()
        apply_result = raw_apply if isinstance(raw_apply, dict) else {"value": raw_apply}

    env_lines = [
        "FOUNDRY_DISCOVERY_ENABLE=true",
        f"FOUNDRY_BASE_URL={normalized_foundry_url}",
        f"FOUNDRY_AGENT_PUBLIC_URL={agent.base_url}",
        f"FOUNDRY_BOOTSTRAP_DELIVERY={str(ticket_response.get('bootstrap_delivery') or request.bootstrap_delivery or 'poll')}",
    ]
    if claim_token:
        env_lines.append(f"FOUNDRY_DISCOVERY_CLAIM_TOKEN={claim_token}")
    if developer_identity:
        env_lines.append(f"FOUNDRY_DEVELOPER_IDENTITY_JSON={json.dumps(developer_identity, ensure_ascii=False)}")

    return {
        "ok": True,
        "foundry_url": normalized_foundry_url,
        "ticket_route": ticket_route,
        "route_attempts": route_attempts,
        "git": git_context,
        "github": github_identity,
        "developer_identity": developer_identity,
        "ticket": ticket_response,
        "claim_applied": bool(apply_result),
        "apply_result": apply_result,
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
            yield _sse_event("error", {"detail": str(exc)})
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
