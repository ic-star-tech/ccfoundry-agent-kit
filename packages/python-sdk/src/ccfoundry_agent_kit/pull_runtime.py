from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any, Awaitable, Callable

import httpx

from .bootstrap import FoundryBootstrap
from .models import ChatRequest, ChatResponse

log = logging.getLogger(__name__)

NormalizePayload = Callable[[dict[str, Any]], ChatRequest]
RunChat = Callable[[ChatRequest], Awaitable[ChatResponse]]
RunBounty = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class FoundryPullRuntime:
    def __init__(
        self,
        *,
        bootstrap: FoundryBootstrap,
        normalize_payload: NormalizePayload,
        run_chat: RunChat,
        run_bounty: RunBounty | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.bootstrap = bootstrap
        self.normalize_payload = normalize_payload
        self.run_chat = run_chat
        self.run_bounty = run_bounty
        self.poll_interval_seconds = max(float(poll_interval_seconds or 1.0), 0.25)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._worker_id = f"pull-agent:{socket.gethostname()}:{os.getpid()}"

    @staticmethod
    def _is_cloud_deploy() -> bool:
        return str(os.getenv("AGENT_DEPLOY_MODE", "local")).strip().lower() == "cloud_run"

    def enabled(self) -> bool:
        return str(self.bootstrap.config.runtime_transport or "").strip().lower() == "pull"

    def ready(self) -> bool:
        return (
            self.enabled()
            and str(self.bootstrap.config.foundry_base_url or "").strip()
            and str(self.bootstrap.state.registered_agent_name or "").strip()
            and str(self.bootstrap.state.env_vars.get("AGENT_SECRET") or "").strip()
            and str(self.bootstrap.state.registration_status or "").strip().upper() == "APPROVED"
        )

    async def poll_once(self) -> dict[str, Any]:
        """Execute a single claim+process cycle.

        Designed for external triggers such as Cloud Scheduler so the
        agent container does not need a persistent internal loop.
        """
        if not self.ready():
            return {"status": "not_ready", "processed": 0}
        try:
            claimed = await self._claim()
        except Exception as exc:
            log.warning("poll_once claim failed: %s", exc)
            return {"status": "claim_error", "processed": 0, "error": str(exc)}
        processed = 0
        for invocation in claimed:
            await self._process_invocation(invocation)
            processed += 1
        return {"status": "ok", "processed": processed}

    async def start(self) -> None:
        if not self.enabled() or self._task is not None:
            return
        if self._is_cloud_deploy():
            log.info("AGENT_DEPLOY_MODE=cloud_run — skipping internal pull loop")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="foundry-pull-runtime")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _post_json(self, path: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        foundry_base_url = str(self.bootstrap.config.foundry_base_url or "").strip().rstrip("/")
        agent_secret = str(self.bootstrap.state.env_vars.get("AGENT_SECRET") or "").strip()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{foundry_base_url}{path}",
                headers={"Authorization": f"Bearer {agent_secret}"},
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def _claim(self) -> list[dict[str, Any]]:
        agent_name = str(self.bootstrap.state.registered_agent_name or self.bootstrap.manifest.name).strip()
        payload = {
            "agent_name": agent_name,
            "worker_id": self._worker_id,
            "limit": 1,
            "lane": "foreground",
        }
        response = await self._post_json("/api/agent-runtime/claim", payload, timeout=20.0)
        items = response.get("items")
        return list(items) if isinstance(items, list) else []

    async def _emit_events(self, invocation_id: int, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        payload = {"events": events}
        await self._post_json(f"/api/agent-runtime/{int(invocation_id)}/events", payload, timeout=20.0)

    async def _complete(self, invocation_id: int, payload: dict[str, Any]) -> None:
        await self._post_json(f"/api/agent-runtime/{int(invocation_id)}/complete", payload, timeout=30.0)

    async def _process_chat_turn(self, invocation: dict[str, Any]) -> None:
        invocation_id = int(invocation.get("id") or 0)
        payload = invocation.get("payload") if isinstance(invocation.get("payload"), dict) else {}
        agent_payload = payload.get("agent_payload") if isinstance(payload.get("agent_payload"), dict) else {}
        request = self.normalize_payload(agent_payload)

        await self._emit_events(
            invocation_id,
            [{"event_type": "step", "message": "Processing request", "payload": {"status": "running", "text": "Processing request..."}}],
        )
        result = await self.run_chat(request)
        compact_value = str(
            getattr(result, "compact", "")
            or (result.metadata.get("compact") if isinstance(result.metadata, dict) else "")
            or ""
        )

        stream_requested = bool(payload.get("stream"))
        events: list[dict[str, Any]] = [
            {"event_type": "step", "message": "Request completed", "payload": {"status": "done", "text": "Request completed."}},
        ]
        if stream_requested:
            chunk_size = 80
            for index in range(0, len(result.reply), chunk_size):
                chunk = result.reply[index : index + chunk_size]
                if chunk:
                    events.append({"event_type": "token", "message": "token", "payload": {"content": chunk}})
        await self._emit_events(invocation_id, events)
        await self._complete(
            invocation_id,
            {
                "status": "succeeded",
                "reply": result.reply,
                "service_fee": float(result.service_fee or 0.0),
                "service_fee_detail": str(result.service_fee_detail or ""),
                "compact": compact_value,
                "metadata": dict(result.metadata or {}),
            },
        )

    async def _process_bounty_execute(self, invocation: dict[str, Any]) -> None:
        invocation_id = int(invocation.get("id") or 0)
        payload = invocation.get("payload") if isinstance(invocation.get("payload"), dict) else {}
        if isinstance(payload.get("agent_payload"), dict):
            bounty_payload = dict(payload)
            bounty_payload.update(dict(payload.get("agent_payload") or {}))
        else:
            bounty_payload = payload
        billing_context = bounty_payload.get("billing_context")
        if not isinstance(billing_context, dict):
            billing_context = {}
        billing_context = {
            **dict(billing_context),
            "invocation_id": invocation_id,
            "requirement_id": str(
                billing_context.get("requirement_id")
                or bounty_payload.get("job_id")
                or payload.get("job_id")
                or ""
            ),
            "job_name": str(
                billing_context.get("job_name")
                or bounty_payload.get("job_name")
                or payload.get("job_name")
                or ""
            ),
        }
        bounty_payload["billing_context"] = billing_context

        if not self.run_bounty:
            await self._complete(
                invocation_id,
                {
                    "status": "cancelled",
                    "error": "Agent has no bounty execution handler configured",
                    "metadata": {
                        "invocation_type": "bounty_execute",
                        "billing_context": billing_context,
                    },
                },
            )
            return

        await self._emit_events(
            invocation_id,
            [
                {
                    "event_type": "step",
                    "message": "Executing bounty",
                    "payload": {
                        "status": "running",
                        "job_id": str(bounty_payload.get("job_id") or payload.get("job_id") or ""),
                    },
                }
            ],
        )
        result = await self.run_bounty(dict(bounty_payload))
        ok = bool(result.get("ok"))
        deliverable = result.get("deliverable") if isinstance(result.get("deliverable"), dict) else {}
        status_text = str(deliverable.get("status") or ("succeeded" if ok else "failed"))
        await self._emit_events(
            invocation_id,
            [
                {
                    "event_type": "bounty_result",
                    "message": f"Bounty execution finished with {status_text}",
                    "payload": result,
                }
            ],
        )
        await self._complete(
            invocation_id,
            {
                "status": "succeeded" if ok else "failed",
                "reply": str(result.get("summary") or status_text or ""),
                "error": "" if ok else str(result.get("error") or "Bounty execution failed"),
                "metadata": {
                    "invocation_type": "bounty_execute",
                    "billing_context": billing_context,
                    "bounty_result": result,
                    "deliverable": deliverable,
                },
            },
        )

    async def _process_invocation(self, invocation: dict[str, Any]) -> None:
        invocation_id = int(invocation.get("id") or 0)
        invocation_type = str(invocation.get("invocation_type") or "").strip().lower()
        try:
            if invocation_type == "chat_turn":
                await self._process_chat_turn(invocation)
                return
            if invocation_type in {"bounty_execute", "requirement_job"}:
                await self._process_bounty_execute(invocation)
                return
            await self._complete(
                invocation_id,
                {
                    "status": "cancelled",
                    "error": f"Unsupported invocation_type: {invocation_type or 'unknown'}",
                    "metadata": {"invocation_type": invocation_type},
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Pull runtime invocation %s failed: %s", invocation_id, exc)
            await self._emit_events(
                invocation_id,
                [{"event_type": "error", "message": str(exc), "payload": {"detail": str(exc)}}],
            )
            await self._complete(
                invocation_id,
                {
                    "status": "failed",
                    "error": str(exc),
                    "reply": f"> Runtime request failed: {str(exc)}",
                    "metadata": {"exception": str(exc)},
                },
            )

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.ready():
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                claimed = await self._claim()
                if not claimed:
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                for invocation in claimed:
                    if self._stop.is_set():
                        return
                    await self._process_invocation(invocation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.bootstrap.state.last_error = f"pull_runtime_failed: {exc}"
                self.bootstrap._save_state()
                log.warning("Foundry pull runtime loop error: %s", exc)
                await asyncio.sleep(max(self.poll_interval_seconds, 1.0))
