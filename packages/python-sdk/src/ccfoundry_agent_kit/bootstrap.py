from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .models import AgentManifest

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_bootstrap_delivery(value: str | None) -> str:
    normalized = str(value or "push").strip().lower()
    if normalized not in {"push", "poll", "hybrid"}:
        return "push"
    return normalized


def _default_execution_contract(network_zone: str | None) -> dict[str, Any]:
    if str(network_zone or "EXTERNAL").strip().upper() != "EXTERNAL":
        return {}
    return {
        "execution_mode": "remote_brain_foundry_workspace",
        "agent_space_location": "external",
        "workspace_location": "foundry_sandbox",
        "skill_delivery_mode": "descriptor_only",
        "data_retention_policy": "no_raw_copy_by_default",
        "network_egress_policy": "no_lan_scoped_egress",
        "allowed_export_policy": "summary_only",
    }


def _deep_merge_dicts(base: dict[str, Any] | None, overlay: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged.get(key), value)
        else:
            merged[key] = value
    return merged


class FoundryInvitePayload(BaseModel):
    invite_id: str | None = None
    invite_code: str
    expected_name: str
    discovery_id: str | None = None
    discovery_nonce: str | None = None
    network_zone: str = "EXTERNAL"
    tenant_name: str | None = None
    expires_at: str | None = None


class FoundryApprovalPayload(BaseModel):
    agent_name: str
    agent_secret: str | None = None
    owner_pseudonym: str | None = None
    env_vars: dict[str, Any] = Field(default_factory=dict)
    allocated_resources: dict[str, Any] = Field(default_factory=dict)
    security_pre_check: dict[str, Any] = Field(default_factory=dict)


class FoundryDeveloperClaimPayload(BaseModel):
    discovery_claim_token: str
    bootstrap_delivery: str | None = None
    foundry_base_url: str | None = None
    public_base_url: str | None = None
    developer_identity: dict[str, Any] = Field(default_factory=dict)
    force_rediscover: bool = False


class FoundryBootstrapConfig(BaseModel):
    enabled: bool = False
    foundry_base_url: str = ""
    public_base_url: str = ""
    network_zone: str = "EXTERNAL"
    runtime_transport: str = "http_push"
    heartbeat_interval_seconds: int = 30
    bootstrap_delivery: str = "push"
    discovery_nonce: str | None = None
    discovery_claim_token: str | None = None
    callback_secret: str | None = None
    state_path: str = ""
    developer_identity: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    resource_requests: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    governance: dict[str, Any] = Field(default_factory=dict)
    system_manifest: dict[str, Any] = Field(default_factory=dict)
    runtime_state: dict[str, Any] = Field(default_factory=dict)
    profile_metadata: dict[str, Any] = Field(default_factory=dict)
    resource_contract_preview: dict[str, Any] = Field(default_factory=dict)
    workspace_features: list[str] = Field(default_factory=lambda: ["chat"])
    interaction_modes: list[str] = Field(default_factory=lambda: ["chat"])
    terminal_provider: str = "none"


class FoundryBootstrapState(BaseModel):
    foundry_base_url: str | None = None
    public_base_url: str | None = None
    discovery_id: str | None = None
    discovery_nonce: str | None = None
    discovery_status: str | None = None
    discovery_claim_token: str | None = None
    last_discovery_at: str | None = None
    last_polled_at: str | None = None
    last_claimed_at: str | None = None
    invite_id: str | None = None
    invite_status: str | None = None
    invite_expected_name: str | None = None
    registration_agent_id: str | None = None
    registration_status: str | None = None
    registered_agent_name: str | None = None
    approved_at: str | None = None
    has_agent_secret: bool = False
    owner_pseudonym: str | None = None
    developer_identity: dict[str, Any] = Field(default_factory=dict)
    allocated_resources: dict[str, Any] = Field(default_factory=dict)
    env_vars: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None


class FoundryBootstrap:
    def __init__(
        self,
        *,
        manifest: AgentManifest,
        agent_space_dir: str | Path,
        config: FoundryBootstrapConfig,
    ) -> None:
        self.manifest = manifest
        self.agent_space_dir = Path(agent_space_dir)
        self.config = config
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.config.bootstrap_delivery = _normalize_bootstrap_delivery(self.config.bootstrap_delivery)
        self.state_path = Path(config.state_path).expanduser() if config.state_path else self.agent_space_dir / "agent_space" / ".foundry_bootstrap.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        state_foundry_base_url = str(self.state.foundry_base_url or "").strip()
        if not str(self.config.foundry_base_url or "").strip() and state_foundry_base_url:
            self.config.foundry_base_url = state_foundry_base_url
        state_public_base_url = str(self.state.public_base_url or "").strip()
        if not str(self.config.public_base_url or "").strip() and state_public_base_url:
            self.config.public_base_url = state_public_base_url
        self.state.discovery_nonce = (
            str(config.discovery_nonce or self.state.discovery_nonce or secrets.token_hex(16)).strip()
        )
        if str(self.config.foundry_base_url or "").strip():
            self.state.foundry_base_url = self.config.foundry_base_url.strip().rstrip("/")
        if str(self.config.public_base_url or "").strip():
            self.state.public_base_url = self.config.public_base_url.strip().rstrip("/")
        configured_claim = str(config.discovery_claim_token or "").strip()
        if configured_claim and not str(self.state.discovery_claim_token or "").strip():
            self.state.discovery_claim_token = configured_claim
        configured_identity = dict(config.developer_identity or {})
        if configured_identity and not dict(self.state.developer_identity or {}):
            self.state.developer_identity = configured_identity
        self.callback_secret = str(config.callback_secret or self.state.env_vars.get("_bootstrap_callback_secret") or secrets.token_hex(24)).strip()
        self.state.env_vars["_bootstrap_callback_secret"] = self.callback_secret
        self._save_state()

    def _load_state(self) -> FoundryBootstrapState:
        if not self.state_path.exists():
            return FoundryBootstrapState()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return FoundryBootstrapState()
        if not isinstance(payload, dict):
            return FoundryBootstrapState()
        try:
            return FoundryBootstrapState.model_validate(payload)
        except Exception:
            return FoundryBootstrapState()

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def build_agent_card(self) -> dict[str, Any]:
        public_url = self.config.public_base_url.rstrip("/")
        tags = list(dict.fromkeys([*self.config.tags, *self.manifest.capabilities]))
        skills = [
            {
                "id": capability,
                "name": capability.replace("_", " ").title(),
                "description": f"{self.manifest.label} supports {capability}",
                "tags": list(dict.fromkeys([capability, *tags])),
            }
            for capability in self.manifest.capabilities
        ]
        provider = self.manifest.metadata.get("provider") if isinstance(self.manifest.metadata, dict) else {}
        if not isinstance(provider, dict) or not provider:
            provider = {"organization": "CCFoundry Agent Kit"}
        return {
            "name": self.manifest.name,
            "description": self.manifest.description,
            "version": self.manifest.version,
            "url": public_url,
            "preferredTransport": "REST",
            "supportedInterfaces": [{"url": public_url, "transport": "REST"}],
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "provider": provider,
            "capabilities": {
                "streaming": False,
                "pushNotifications": True,
            },
            "skills": skills,
            "supportsAuthenticatedExtendedCard": False,
        }

    def build_foundry_envelope(self) -> dict[str, Any]:
        governance = dict(self.config.governance or {})
        governance.setdefault("bootstrap_callback_secret", self.callback_secret)
        governance.setdefault("bootstrap_delivery", self.config.bootstrap_delivery)
        governance.setdefault("runtime_transport", self.config.runtime_transport)
        profile_metadata = dict(self.config.profile_metadata or {})
        profile_metadata.setdefault("agent_name", self.manifest.name)
        profile_metadata.setdefault("agent_label", self.manifest.label)
        profile_metadata.setdefault("sdk", "ccfoundry-agent-kit")
        execution_contract = _deep_merge_dicts(
            _default_execution_contract(self.config.network_zone),
            dict((self.config.requirements or {}).get("execution_contract") or {}),
        )
        requirements = dict(self.config.requirements or {})
        if execution_contract:
            requirements["execution_contract"] = execution_contract
        resource_requests = dict(self.config.resource_requests or {})
        if execution_contract.get("workspace_location") == "foundry_sandbox":
            resource_requests["sandbox_workspace"] = _deep_merge_dicts(
                {
                    "requested": True,
                    "workspace_profile": "green",
                    "agent_space_mount": "none",
                    "delivery_mode": "ephemeral_task_workspace",
                },
                dict(resource_requests.get("sandbox_workspace") or {}),
            )
        system_manifest = dict(self.config.system_manifest or {})
        manifest_payload = self.manifest.manifest.model_dump(mode="json")
        if manifest_payload:
            system_manifest.setdefault("manifest", manifest_payload)
        developer_access: dict[str, Any] = {
            "bootstrap_delivery": self.config.bootstrap_delivery,
        }
        discovery_claim_token = str(self.state.discovery_claim_token or self.config.discovery_claim_token or "").strip()
        if discovery_claim_token:
            developer_access["discovery_claim_token"] = discovery_claim_token
        developer_identity = dict(self.state.developer_identity or self.config.developer_identity or {})
        if developer_identity:
            developer_access["developer_identity"] = developer_identity
        return {
            "discovery_nonce": self.state.discovery_nonce,
            "network_zone": self.config.network_zone,
            "system_manifest": {
                "sdk": "ccfoundry-agent-kit",
                "python_version": sys.version.split()[0],
                **system_manifest,
            },
            "runtime_state": {
                "pid": os.getpid(),
                "runtime_transport": self.config.runtime_transport,
                **(self.config.runtime_state or {}),
            },
            "profile_metadata": profile_metadata,
            "requirements": requirements,
            "resource_requests": resource_requests,
            "budget": dict(self.config.budget or {}),
            "governance": governance,
            "developer_access": developer_access,
            "tags": list(dict.fromkeys([*self.config.tags, *self.manifest.capabilities])),
            "resource_contract_preview": dict(self.config.resource_contract_preview or {}),
        }

    def build_registration_payload(self, invite: FoundryInvitePayload) -> dict[str, Any]:
        workspace_features = [
            str(item).strip()
            for item in (self.config.workspace_features or ["chat"])
            if str(item).strip()
        ] or ["chat"]
        interaction_modes = [
            str(item).strip()
            for item in (self.config.interaction_modes or ["chat"])
            if str(item).strip()
        ] or ["chat"]
        capabilities = {
            "label": self.manifest.label,
            "description": self.manifest.description,
            "manifest_capabilities": list(self.manifest.capabilities),
            "metadata": dict(self.manifest.metadata or {}),
            "supported_interfaces": [{"url": self.config.public_base_url.rstrip("/"), "transport": "REST"}],
            "preferred_transport": "REST",
            "features": list(dict.fromkeys([*workspace_features, *self.manifest.features])),
            "interaction_modes": interaction_modes,
            "terminal_provider": str(self.config.terminal_provider or "none").strip().lower() or "none",
            "runtime_transport": str(self.config.runtime_transport or "http_push").strip().lower() or "http_push",
            "loaded_skills": list(self.manifest.loaded_skills),
            "slash_commands": [item.model_dump(mode="json") for item in self.manifest.slash_commands],
            "privacy": dict(self.manifest.privacy or {}),
            "billing": dict(self.manifest.billing or {}),
        }
        envelope = self.build_foundry_envelope()
        return {
            "invite_code": invite.invite_code,
            "expected_name": invite.expected_name,
            "url": self.config.public_base_url.rstrip("/"),
            "discovery_nonce": invite.discovery_nonce or self.state.discovery_nonce,
            "network_zone": invite.network_zone or self.config.network_zone,
            "system_manifest": envelope["system_manifest"],
            "runtime_state": envelope["runtime_state"],
            "profile_metadata": envelope["profile_metadata"],
            "developer_access": envelope.get("developer_access", {}),
            "capabilities": capabilities,
            "core_files": {},
        }

    async def start(self) -> None:
        if not self.config.enabled:
            return
        await self._announce_or_heartbeat()
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._task = None

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.heartbeat_interval_seconds)
                break
            except asyncio.TimeoutError:
                await self._announce_or_heartbeat()

    def _extract_bootstrap_actions(self, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []

        actions: list[dict[str, Any]] = []
        raw_actions = payload.get("bootstrap_actions")
        if isinstance(raw_actions, dict):
            raw_actions = [raw_actions]
        if isinstance(raw_actions, list):
            for item in raw_actions:
                if isinstance(item, dict):
                    actions.append(item)

        bootstrap_action = payload.get("bootstrap_action")
        if isinstance(bootstrap_action, dict):
            actions.append(bootstrap_action)

        pending_invite = payload.get("pending_invite")
        if isinstance(pending_invite, dict):
            actions.append({"type": "invite", "payload": pending_invite})

        approval_bundle = payload.get("approval_bundle")
        if isinstance(approval_bundle, dict):
            actions.append({"type": "approved", "payload": approval_bundle})

        return actions

    async def _apply_bootstrap_actions(self, payload: dict[str, Any] | None) -> None:
        for action in self._extract_bootstrap_actions(payload):
            action_type = str(action.get("type") or action.get("kind") or "").strip().lower()
            action_payload = action.get("payload")
            if not isinstance(action_payload, dict):
                action_payload = dict(action)
                action_payload.pop("type", None)
                action_payload.pop("kind", None)

            if action_type in {"invite", "bootstrap_invite"}:
                await self.handle_invite(FoundryInvitePayload.model_validate(action_payload))
                continue
            if action_type in {"approved", "approval", "bootstrap_approved"}:
                await self.handle_approval(FoundryApprovalPayload.model_validate(action_payload))

    async def _poll_bootstrap_actions(self) -> None:
        if not self.state.discovery_id:
            return
        poll_url = f"{self.config.foundry_base_url.rstrip('/')}/api/registry/discover/actions"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    poll_url,
                    json={
                        "discovery_id": self.state.discovery_id,
                        "discovery_nonce": self.state.discovery_nonce,
                        "url": self.config.public_base_url.rstrip("/"),
                    },
                )
            if response.status_code == 404:
                return
            response.raise_for_status()
            payload = response.json()
            self.state.last_polled_at = _utcnow_iso()
            self.state.last_error = None
            self._save_state()
            if isinstance(payload, dict):
                await self._apply_bootstrap_actions(payload)
        except Exception as exc:
            logger.warning("Bootstrap action polling failed", exc_info=exc)
            self.state.last_error = "bootstrap_poll_failed"
            self._save_state()

    async def _announce_or_heartbeat(self) -> None:
        if not self.config.enabled:
            return
        if not self.config.foundry_base_url.strip() or not self.config.public_base_url.strip():
            return

        try:
            if self.state.discovery_id:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        f"{self.config.foundry_base_url.rstrip('/')}/api/registry/discover/heartbeat",
                        json={
                            "discovery_id": self.state.discovery_id,
                            "discovery_nonce": self.state.discovery_nonce,
                            "url": self.config.public_base_url.rstrip("/"),
                        },
                    )
                if response.status_code == 404:
                    self.state.discovery_id = None
                    self.state.discovery_status = None
                    self._save_state()
                    return await self._announce_or_heartbeat()
                response.raise_for_status()
                payload = response.json()
                self.state.discovery_status = str(payload.get("status") or "DISCOVERED")
                self.state.last_discovery_at = _utcnow_iso()
                self.state.last_error = None
                self._save_state()
                if isinstance(payload, dict):
                    await self._apply_bootstrap_actions(payload)
                if self.config.bootstrap_delivery in {"poll", "hybrid"}:
                    await self._poll_bootstrap_actions()
                return

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.config.foundry_base_url.rstrip('/')}/api/registry/discover",
                    json={
                        "agent_card": self.build_agent_card(),
                        "x_foundry": self.build_foundry_envelope(),
                    },
                )
            response.raise_for_status()
            payload = response.json()
            self.state.discovery_id = str(payload.get("discovery_id") or "").strip() or None
            self.state.discovery_status = str(payload.get("status") or "DISCOVERED")
            self.state.last_discovery_at = _utcnow_iso()
            self.state.last_error = None
            self._save_state()
            if isinstance(payload, dict):
                await self._apply_bootstrap_actions(payload)
            if self.config.bootstrap_delivery in {"poll", "hybrid"}:
                await self._poll_bootstrap_actions()
        except Exception as exc:
            logger.warning("Bootstrap announce/heartbeat failed", exc_info=exc)
            self.state.last_error = "bootstrap_discovery_failed"
            self._save_state()

    def public_state(self) -> dict[str, Any]:
        has_llm_api_key = bool(
            str(self.state.env_vars.get("LLM_API_KEY") or self.state.env_vars.get("OPENAI_API_KEY") or "").strip()
        )
        has_llm_api_base = bool(
            str(self.state.env_vars.get("LLM_API_BASE") or self.state.env_vars.get("OPENAI_BASE_URL") or "").strip()
        )
        approval_mode = "pending"
        if self.state.registration_status == "APPROVED" and self.state.approved_at:
            approval_mode = "callback"
        elif self.state.registration_status == "APPROVED":
            approval_mode = "inline_register"

        return {
            "enabled": self.config.enabled,
            "foundry_base_url": self.config.foundry_base_url.rstrip("/"),
            "public_base_url": self.config.public_base_url.rstrip("/"),
            "network_zone": self.config.network_zone,
            "runtime_transport": self.config.runtime_transport,
            "bootstrap_delivery": self.config.bootstrap_delivery,
            "discovery_id": self.state.discovery_id,
            "discovery_nonce": self.state.discovery_nonce,
            "discovery_status": self.state.discovery_status,
            "has_discovery_claim": bool(str(self.state.discovery_claim_token or "").strip()),
            "last_polled_at": self.state.last_polled_at,
            "last_discovery_at": self.state.last_discovery_at,
            "last_claimed_at": self.state.last_claimed_at,
            "invite_id": self.state.invite_id,
            "invite_status": self.state.invite_status,
            "invite_expected_name": self.state.invite_expected_name,
            "registration_agent_id": self.state.registration_agent_id,
            "registration_status": self.state.registration_status,
            "registered_agent_name": self.state.registered_agent_name,
            "approved_at": self.state.approved_at,
            "has_agent_secret": self.state.has_agent_secret,
            "has_llm_api_key": has_llm_api_key,
            "has_llm_api_base": has_llm_api_base,
            "approval_mode": approval_mode,
            "owner_pseudonym": self.state.owner_pseudonym,
            "developer_identity": dict(self.state.developer_identity or {}),
            "allocated_resources": self.state.allocated_resources,
            "last_error": self.state.last_error,
        }

    def sandbox_client(self, *, timeout: float = 20.0) -> "FoundrySandboxClient":
        from .sandbox_client import FoundrySandboxClient

        return FoundrySandboxClient.from_bootstrap(self, timeout=timeout)

    def verify_callback_token(self, token: str | None) -> None:
        expected = str(self.callback_secret or "").strip()
        if expected and str(token or "").strip() != expected:
            raise PermissionError("Invalid Foundry bootstrap token")

    async def install_developer_claim(self, payload: FoundryDeveloperClaimPayload) -> dict[str, Any]:
        claim_token = str(payload.discovery_claim_token or "").strip()
        if not claim_token:
            raise ValueError("discovery_claim_token is required")

        async with self._lock:
            foundry_base_url = str(payload.foundry_base_url or "").strip().rstrip("/")
            if foundry_base_url:
                self.config.foundry_base_url = foundry_base_url
                self.state.foundry_base_url = foundry_base_url
            public_base_url = str(payload.public_base_url or "").strip().rstrip("/")
            if public_base_url:
                self.config.public_base_url = public_base_url
                self.state.public_base_url = public_base_url
            self.state.discovery_claim_token = claim_token
            self.state.last_claimed_at = _utcnow_iso()
            if payload.developer_identity:
                self.state.developer_identity = dict(payload.developer_identity)
            if payload.bootstrap_delivery:
                self.config.bootstrap_delivery = _normalize_bootstrap_delivery(payload.bootstrap_delivery)
            if payload.force_rediscover:
                # Force the next bootstrap cycle down the discover path so a fresh
                # developer claim ticket can be consumed even for already-approved agents.
                self.state.discovery_id = None
                self.state.discovery_status = None
                self.state.last_discovery_at = None
                self.state.invite_id = None
                self.state.invite_status = None
                self.state.invite_expected_name = None
                self.state.last_polled_at = None
            self.state.last_error = None
            self._save_state()

        if payload.force_rediscover:
            await self._announce_or_heartbeat()
        return self.public_state()

    async def fetch_agent_env_config(self, *, agent_name: str, agent_secret: str) -> dict[str, Any]:
        foundry_base_url = self.config.foundry_base_url.rstrip("/")
        if not foundry_base_url:
            raise ValueError("foundry_base_url is not configured")
        if not agent_name.strip():
            raise ValueError("agent_name is required")
        if not agent_secret.strip():
            raise ValueError("agent_secret is required")

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{foundry_base_url}/api/agents/{agent_name}/config",
                headers={"Authorization": f"Bearer {agent_secret}"},
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def handle_invite(self, invite: FoundryInvitePayload) -> dict[str, Any]:
        async with self._lock:
            if invite.discovery_nonce and invite.discovery_nonce != self.state.discovery_nonce:
                raise ValueError("Invite discovery_nonce mismatch")
            self.state.invite_id = invite.invite_id
            self.state.invite_status = "ISSUED"
            self.state.invite_expected_name = invite.expected_name
            self.state.last_error = None
            self._save_state()

        if (
            self.state.registration_status in {"PENDING", "APPROVED"}
            and self.state.registered_agent_name == invite.expected_name
        ):
            return {
                "ok": True,
                "status": self.state.registration_status,
                "agent_name": self.state.registered_agent_name,
                "message": "Invite already handled for this agent",
            }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.config.foundry_base_url.rstrip('/')}/api/registry/register",
                    json=self.build_registration_payload(invite),
                )
            response.raise_for_status()
            payload = response.json()
            async with self._lock:
                self.state.registration_agent_id = str(payload.get("agent_id") or "").strip() or None
                self.state.registration_status = str(payload.get("status") or "PENDING")
                self.state.registered_agent_name = invite.expected_name
                inline_agent_secret = str(payload.get("agent_secret") or "").strip()
                if inline_agent_secret:
                    self.state.env_vars["AGENT_SECRET"] = inline_agent_secret
                    self.state.has_agent_secret = True
                owner_pseudonym = str(payload.get("owner_pseudonym") or "").strip()
                if owner_pseudonym:
                    self.state.owner_pseudonym = owner_pseudonym
                if self.state.registration_status in {"PENDING", "APPROVED"}:
                    self.state.invite_status = "REDEEMED"
                    self.state.discovery_status = "REGISTERED"
                if self.state.registration_status == "APPROVED":
                    self.state.approved_at = self.state.approved_at or _utcnow_iso()
                self.state.last_error = None
                self._save_state()

            if self.state.registration_status == "APPROVED" and inline_agent_secret:
                try:
                    env_config = await self.fetch_agent_env_config(
                        agent_name=invite.expected_name,
                        agent_secret=inline_agent_secret,
                    )
                    async with self._lock:
                        merged_env = dict(self.state.env_vars)
                        for key, value in env_config.items():
                            merged_env[key] = value
                        merged_env["_bootstrap_callback_secret"] = self.callback_secret
                        self.state.env_vars = merged_env
                        self.state.has_agent_secret = bool(merged_env.get("AGENT_SECRET"))
                        self.state.last_error = None
                        self._save_state()
                except Exception as exc:
                    logger.warning("Fetching approved agent env config failed", exc_info=exc)
                    async with self._lock:
                        self.state.last_error = "bootstrap_fetch_config_failed"
                        self._save_state()
            return {
                "ok": True,
                "status": self.state.registration_status,
                "agent_id": self.state.registration_agent_id,
                "agent_name": invite.expected_name,
            }
        except Exception as exc:
            logger.warning("Registering bootstrap invite failed", exc_info=exc)
            async with self._lock:
                self.state.last_error = "bootstrap_register_failed"
                self._save_state()
            raise

    async def handle_approval(self, approval: FoundryApprovalPayload) -> dict[str, Any]:
        async with self._lock:
            merged_env = dict(self.state.env_vars)
            for key, value in approval.env_vars.items():
                merged_env[key] = value
            merged_env["_bootstrap_callback_secret"] = self.callback_secret
            self.state.env_vars = merged_env
            self.state.registered_agent_name = approval.agent_name
            self.state.registration_status = "APPROVED"
            self.state.invite_status = "REDEEMED"
            self.state.discovery_status = "REGISTERED"
            self.state.approved_at = _utcnow_iso()
            self.state.has_agent_secret = bool(approval.agent_secret or approval.env_vars.get("AGENT_SECRET"))
            self.state.owner_pseudonym = approval.owner_pseudonym
            self.state.allocated_resources = dict(approval.allocated_resources or {})
            self.state.last_error = None
            self._save_state()
        return {
            "ok": True,
            "status": "APPROVED",
            "agent_name": approval.agent_name,
            "has_agent_secret": self.state.has_agent_secret,
        }

    async def sync_registry_files(self, files: dict[str, str]) -> dict[str, Any]:
        agent_name = str(self.state.registered_agent_name or self.manifest.name).strip()
        foundry_base_url = self.config.foundry_base_url.rstrip("/")
        agent_secret = str(self.state.env_vars.get("AGENT_SECRET") or "").strip()

        if not agent_name:
            raise ValueError("registered agent name is not available")
        if not foundry_base_url:
            raise ValueError("foundry_base_url is not configured")
        if not agent_secret:
            raise ValueError("AGENT_SECRET is not available")
        if not files:
            raise ValueError("files payload is required")

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{foundry_base_url}/api/agents/{agent_name}/registry/sync",
                headers={"Authorization": f"Bearer {agent_secret}"},
                json={"files": files},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {"value": payload}

        allocated_resources = dict(payload.get("allocated_resources") or {})
        if allocated_resources:
            self.state.allocated_resources = _deep_merge_dicts(self.state.allocated_resources, allocated_resources)
            self._save_state()
        return payload

    async def sync_core_files(
        self,
        *,
        soul_content: str | None = None,
        config_content: str | None = None,
        extra_files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        files = dict(extra_files or {})
        if soul_content is not None:
            files["SOUL.md"] = soul_content
        if config_content is not None:
            files["config.yaml"] = config_content
        return await self.sync_registry_files(files)
