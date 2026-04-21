from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .models import AgentManifest, ChatRequest, ChatResponse, HealthResponse


class AgentClient:
    def __init__(self, base_url: str, *, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health(self) -> HealthResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return HealthResponse.model_validate(response.json())

    async def manifest(self) -> AgentManifest:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/manifest")
            response.raise_for_status()
            return AgentManifest.model_validate(response.json())

    async def chat(self, request: ChatRequest) -> ChatResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat", json=request.model_dump(mode="json"))
            response.raise_for_status()
            return ChatResponse.model_validate(response.json())

    async def workspace_tree(self, *, path: str = "", depth: int = 3) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/api/workspace/tree", params={"path": path, "depth": depth})
            response.raise_for_status()
            return response.json()

    async def workspace_read(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/api/workspace/read", params={"path": path})
            response.raise_for_status()
            return response.json()

    async def workspace_write(self, path: str, content: str = "") -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.put(
                f"{self.base_url}/api/workspace/write",
                json={"path": path, "content": content},
            )
            response.raise_for_status()
            return response.json()

    async def reflections(self, *, date: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/reflections",
                params={"date": date, "limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else []

    async def stream_foundry_chat(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            last_error: Exception | None = None
            for endpoint in ("api/chat", "chat"):
                try:
                    async with client.stream("POST", f"{self.base_url}/{endpoint}", json=payload) as response:
                        if response.status_code in {404, 405}:
                            last_error = httpx.HTTPStatusError(
                                f"{endpoint} returned {response.status_code}",
                                request=response.request,
                                response=response,
                            )
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "")
                        if "text/event-stream" not in content_type:
                            raw = await response.aread()
                            data = json.loads(raw.decode("utf-8"))
                            if isinstance(data, dict):
                                yield "message", data
                            else:
                                yield "message", {"content": str(data)}
                            yield "done", {}
                            return

                        event_type = "message"
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            if line.startswith("event:"):
                                event_type = line[6:].strip() or "message"
                                continue
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            try:
                                payload_obj = json.loads(data_str)
                            except json.JSONDecodeError:
                                payload_obj = {"content": data_str}
                            if not isinstance(payload_obj, dict):
                                payload_obj = {"value": payload_obj}
                            yield event_type, payload_obj
                        return
                except Exception as exc:
                    last_error = exc
            if last_error:
                raise last_error
