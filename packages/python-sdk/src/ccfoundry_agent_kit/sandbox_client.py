from __future__ import annotations

from typing import Any

import httpx


class FoundrySandboxClientError(RuntimeError):
    pass


class FoundrySandboxClient:
    def __init__(
        self,
        *,
        foundry_base_url: str,
        agent_name: str,
        agent_secret: str,
        control_plane: dict[str, Any],
        timeout: float = 20.0,
    ) -> None:
        self.foundry_base_url = str(foundry_base_url or "").rstrip("/")
        self.agent_name = str(agent_name or "").strip()
        self.agent_secret = str(agent_secret or "").strip()
        self.control_plane = dict(control_plane or {})
        self.timeout = timeout

        if not self.foundry_base_url:
            raise FoundrySandboxClientError("foundry_base_url is required")
        if not self.agent_name:
            raise FoundrySandboxClientError("agent_name is required")
        if not self.agent_secret:
            raise FoundrySandboxClientError("agent_secret is required")
        if not self.control_plane:
            raise FoundrySandboxClientError("sandbox control_plane is required")

    @classmethod
    def from_bootstrap(cls, bootstrap: Any, *, timeout: float = 20.0) -> "FoundrySandboxClient":
        foundry_base_url = str(getattr(getattr(bootstrap, "config", None), "foundry_base_url", "") or "").strip()
        state = getattr(bootstrap, "state", None)
        if state is None:
            raise FoundrySandboxClientError("bootstrap state is not available")

        agent_name = str(getattr(state, "registered_agent_name", "") or "").strip()
        env_vars = dict(getattr(state, "env_vars", {}) or {})
        allocated_resources = dict(getattr(state, "allocated_resources", {}) or {})
        sandbox_workspace = dict(allocated_resources.get("sandbox_workspace") or {})
        control_plane = dict(sandbox_workspace.get("control_plane") or {})
        agent_secret = str(env_vars.get("AGENT_SECRET") or "").strip()

        return cls(
            foundry_base_url=foundry_base_url,
            agent_name=agent_name,
            agent_secret=agent_secret,
            control_plane=control_plane,
            timeout=timeout,
        )

    def _resolve_url(self, key: str) -> str:
        raw = str(self.control_plane.get(key) or "").strip()
        if not raw:
            raise FoundrySandboxClientError(f"Missing control-plane URL: {key}")
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return f"{self.foundry_base_url}{raw}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.agent_secret}"}

    async def _get(self, key: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(self._resolve_url(key), headers=self._headers(), params=params)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}

    async def _post(self, key: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self._resolve_url(key), headers=self._headers(), json=json)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}

    async def _put(self, key: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.put(self._resolve_url(key), headers=self._headers(), json=json)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}

    async def status(self) -> dict[str, Any]:
        return await self._get("status_url")

    async def start(self) -> dict[str, Any]:
        return await self._post("start_url")

    async def stop(self) -> dict[str, Any]:
        return await self._post("stop_url")

    async def terminal_state(self, *, capture_lines: int = 80) -> dict[str, Any]:
        return await self._get("terminal_state_url", params={"capture_lines": capture_lines})

    async def terminal_exec(
        self,
        command: str,
        *,
        wait_ms: int = 250,
        capture_lines: int = 80,
        clear_line: bool = False,
        enter: bool = True,
    ) -> dict[str, Any]:
        return await self._post(
            "terminal_exec_url",
            json={
                "command": command,
                "wait_ms": wait_ms,
                "capture_lines": capture_lines,
                "clear_line": clear_line,
                "enter": enter,
            },
        )

    async def workspace_tree(self, *, depth: int = 3) -> dict[str, Any]:
        return await self._get("workspace_tree_url", params={"depth": depth})

    async def workspace_read(self, path: str) -> dict[str, Any]:
        return await self._get("workspace_read_url", params={"path": path})

    async def workspace_read_text(self, path: str) -> str:
        payload = await self.workspace_read(path)
        return str(payload.get("content") or "")

    async def workspace_write(self, path: str, content: str = "") -> dict[str, Any]:
        return await self._put("workspace_write_url", json={"path": path, "content": content})
