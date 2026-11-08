"""Short-lived local-account developer sessions, held only by this process."""

from __future__ import annotations

import json
import ipaddress
import os
import secrets
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException


def canonical_foundry_url(value: str) -> str:
    """Bind credentials to an explicit scheme, host, port and installation path."""
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or any(ord(char) < 33 for char in value)
            or "\\" in value
            or "%" in parts.netloc
            or any(segment in {".", ".."} for segment in parts.path.split("/"))
        ):
            raise ValueError
        port = parts.port
        host = parts.hostname.lower()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if parts.scheme == "http" and not loopback and os.getenv("CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP", "").lower() not in {"1", "true", "yes", "on"}:
            raise HTTPException(status_code=400, detail="Use HTTPS for Foundry, or explicitly enable CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP for your private network")
        if ":" in host:
            host = f"[{host}]"
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            host += f":{port}"
        return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Enter an explicit http:// or https:// Foundry URL without credentials, query or fragment") from None


@dataclass(frozen=True)
class FoundryDeveloperSession:
    foundry_url: str
    developer_token: str = field(repr=False)
    identity: dict
    expires_at: float


class FoundrySessions:
    def __init__(self, capacity: int = 128) -> None:
        self._sessions: dict[str, FoundryDeveloperSession] = {}
        self.capacity = capacity

    def _prune(self) -> None:
        now = time.time()
        for handle, session in list(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(handle, None)

    def add(self, session: FoundryDeveloperSession) -> str:
        self._prune()
        if len(self._sessions) >= self.capacity:
            raise HTTPException(status_code=429, detail="Too many developer sessions; sign out or wait for a session to expire")
        handle = secrets.token_urlsafe(32)
        self._sessions[handle] = session
        return handle

    def resolve(self, handle: str, foundry_url: str) -> FoundryDeveloperSession:
        self._prune()
        session = self._sessions.get(handle)
        if session is None:
            raise HTTPException(status_code=401, detail="Foundry developer session expired; sign in again")
        if session.foundry_url != canonical_foundry_url(foundry_url):
            raise HTTPException(status_code=409, detail="Foundry URL changed; sign in to the selected Foundry")
        return session

    def remove(self, handle: str) -> None:
        self._sessions.pop(handle, None)


def foundry_http_client(*, timeout: float = 12.0) -> httpx.AsyncClient:
    ca_bundle = os.getenv("CCFOUNDRY_DEV_BOARD_CA_BUNDLE", "").strip()
    try:
        verify = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
    except (OSError, ssl.SSLError):
        raise HTTPException(status_code=503, detail="Dev Board's Foundry CA bundle could not be loaded") from None
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, verify=verify)


async def _post_json(client: httpx.AsyncClient, url: str, **kwargs: object) -> dict:
    # Never replay a credential exchange or forward credentials through redirects.
    try:
        async with client.stream("POST", url, **kwargs) as response:
            if not response.is_success:
                if response.status_code in {401, 403}:
                    raise HTTPException(status_code=401, detail="Foundry sign-in was rejected; check your account or sign in again")
                if response.status_code in {404, 405}:
                    raise HTTPException(status_code=409, detail="This Foundry does not support local developer sessions yet")
                raise HTTPException(status_code=502, detail=f"Foundry sign-in failed (status {response.status_code})")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
            return result
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not contact Foundry securely; check its URL, connectivity and TLS certificate") from None
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=502, detail="Foundry returned an invalid sign-in response") from None


async def login_to_foundry(foundry_url: str, username: str, password: str) -> FoundryDeveloperSession:
    target = canonical_foundry_url(foundry_url)
    if not username.strip() or not password or len(username) > 256 or len(password) > 4096:
        raise HTTPException(status_code=400, detail="Enter your Foundry username and password")
    async with foundry_http_client() as client:
        login = await _post_json(client, f"{target}/api/auth/login", json={"username": username.strip(), "password": password})
        access = login.get("token")
        if login.get("ok") is not True or not isinstance(access, str) or not access:
            raise HTTPException(status_code=502, detail="Foundry returned an invalid login response")
        result = await _post_json(client, f"{target}/api/developer/session", headers={"Authorization": f"Bearer {access}"}, json={})
    try:
        token = result["developer_token"]
        identity = result["developer_identity"]
        expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
        if (
            result.get("ok") is not True
            or not isinstance(token, str)
            or not token.startswith("sk-foundry-local-dev-")
            or len(token) > 16384
            or any(char.isspace() for char in token)
            or not isinstance(identity, dict)
            or identity.get("provider") != "foundry"
            or not isinstance(identity.get("user_id"), str)
            or not identity["user_id"]
            or expiry.tzinfo is None
            or not time.time() < expiry.timestamp() <= time.time() + 901
        ):
            raise ValueError
        # Select identity fields: never retain arbitrary upstream response data.
        safe_identity = {key: identity[key] for key in ("provider", "user_id", "username", "developer_id", "developer_label") if isinstance(identity.get(key), str)}
        return FoundryDeveloperSession(target, token, safe_identity, expiry.timestamp())
    except (KeyError, TypeError, AttributeError, ValueError):
        raise HTTPException(status_code=502, detail="Foundry returned an invalid developer session") from None
