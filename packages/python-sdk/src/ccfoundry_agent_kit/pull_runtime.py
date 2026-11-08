"""Foundry pull runtime: claim invocations, report events, complete them.

Delivery guarantees:

- ``complete`` is retried with bounded exponential backoff on connection
  errors and HTTP 429/5xx (``Retry-After`` is honoured). Foundry answers a
  repeated completion of an already terminal invocation with 200 and
  "Invocation already completed", so a retry after a lost response is safe.
- ``events`` are retried once: Foundry appends every event it receives, so a
  retry after a lost response can duplicate an informational event row.
- ``claim`` is never retried. A lost claim response leaves the invocation
  claimed on the Foundry side, and Foundry's stale-claim reaper requeues it;
  retrying could claim a second invocation.
- Other 4xx responses are not retried.
- One failing invocation never aborts the others in the same ``poll_once``
  cycle; failures are counted in the result and recorded in
  ``bootstrap.state.last_error``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
from typing import Any, Awaitable, Callable

import httpx

from .errors import raise_for_foundry_status

from .bootstrap import FoundryBootstrap
from .models import ChatRequest, ChatResponse

log = logging.getLogger(__name__)

NormalizePayload = Callable[[dict[str, Any]], ChatRequest]
RunChat = Callable[[ChatRequest], Awaitable[ChatResponse]]
RunBounty = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
Sleep = Callable[[float], Awaitable[None]]

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_EVENT_ATTEMPTS = 2


class FoundryPullRuntime:
    def __init__(
        self,
        *,
        bootstrap: FoundryBootstrap,
        normalize_payload: NormalizePayload,
        run_chat: RunChat,
        run_bounty: RunBounty | None = None,
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 4,
        retry_base_delay_seconds: float = 0.5,
        retry_max_delay_seconds: float = 8.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        self.bootstrap = bootstrap
        self.normalize_payload = normalize_payload
        self.run_chat = run_chat
        self.run_bounty = run_bounty
        self.poll_interval_seconds = max(float(poll_interval_seconds or 1.0), 0.25)
        self.max_attempts = max(int(max_attempts or 1), 1)
        self.retry_base_delay_seconds = max(float(retry_base_delay_seconds or 0.0), 0.0)
        self.retry_max_delay_seconds = max(float(retry_max_delay_seconds or 0.0), 0.0)
        self._http_transport = http_transport
        self._sleep: Sleep = sleep or asyncio.sleep
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._worker_id = f"pull-agent:{socket.gethostname()}:{os.getpid()}"

    @staticmethod
    def _is_cloud_deploy() -> bool:
        return str(os.getenv("AGENT_DEPLOY_MODE", "local")).strip().lower() == "cloud_run"

    def enabled(self) -> bool:
        return str(self.bootstrap.config.runtime_transport or "").strip().lower() == "pull"

    def ready(self) -> bool:
        return bool(
            self.enabled()
            and str(self.bootstrap.config.foundry_base_url or "").strip()
            and str(self.bootstrap.state.registered_agent_name or "").strip()
            and str(self.bootstrap.state.env_vars.get("AGENT_SECRET") or "").strip()
            and str(self.bootstrap.state.registration_status or "").strip().upper() == "APPROVED"
        )

    async def poll_once(self) -> dict[str, Any]:
        """Execute a single claim+process cycle.

        Designed for external triggers such as Cloud Scheduler so the
        agent container does not need a persistent internal loop. A failing
        invocation is counted in ``failed`` instead of aborting the cycle.
        """
        if not self.ready():
            return {"status": "not_ready", "processed": 0}
        try:
            claimed = await self._claim()
        except Exception as exc:
            log.warning("poll_once claim failed: %s", exc)
            return {"status": "claim_error", "processed": 0, "error": str(exc)}
        processed = 0
        failed_ids: list[Any] = []
        for invocation in claimed:
            try:
                ok = await self._process_invocation(invocation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # _process_invocation contains its own errors; defensive only
                log.warning("poll_once invocation %s raised: %s", invocation.get("id"), exc)
                ok = False
            processed += 1
            if not ok:
                failed_ids.append(invocation.get("id"))
        result: dict[str, Any] = {"status": "ok", "processed": processed}
        if failed_ids:
            result["failed"] = len(failed_ids)
            result["failed_invocation_ids"] = failed_ids
        return result

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

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                value = float(retry_after)
            except ValueError:
                value = -1.0
            if value >= 0:
                return min(value, self.retry_max_delay_seconds)
        delay = self.retry_base_delay_seconds * (2 ** (attempt - 1))
        delay *= 1.0 + random.uniform(0.0, 0.1)
        return min(delay, self.retry_max_delay_seconds)

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
        attempts: int = 1,
    ) -> dict[str, Any]:
        foundry_base_url = str(self.bootstrap.config.foundry_base_url or "").strip().rstrip("/")
        agent_secret = str(self.bootstrap.state.env_vars.get("AGENT_SECRET") or "").strip()
        attempts = max(int(attempts or 1), 1)
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self._http_transport) as client:
                    response = await client.post(
                        f"{foundry_base_url}{path}",
                        headers={"Authorization": f"Bearer {agent_secret}"},
                        json=payload,
                    )
            except httpx.TransportError as exc:
                if attempt >= attempts:
                    raise
                delay = self._retry_delay(attempt, None)
                log.warning(
                    "POST %s failed with %s; retry %d/%d in %.1fs",
                    path, exc.__class__.__name__, attempt, attempts - 1, delay,
                )
                await self._sleep(delay)
                continue
            if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                delay = self._retry_delay(attempt, response.headers.get("Retry-After"))
                log.warning(
                    "POST %s returned HTTP %d; retry %d/%d in %.1fs",
                    path, response.status_code, attempt, attempts - 1, delay,
                )
                await self._sleep(delay)
                continue
            raise_for_foundry_status(response)
            data = response.json()
            return data if isinstance(data, dict) else {}
        raise RuntimeError("unreachable: retry loop exited without a response")

    async def _claim(self) -> list[dict[str, Any]]:
        agent_name = str(self.bootstrap.state.registered_agent_name or self.bootstrap.manifest.name).strip()
        payload = {
            "agent_name": agent_name,
            "worker_id": self._worker_id,
            "limit": 1,
            "lane": "foreground",
        }
        # Never retried: see module docstring.
        response = await self._post_json("/api/agent-runtime/claim", payload, timeout=20.0, attempts=1)
        items = response.get("items")
        return list(items) if isinstance(items, list) else []

    async def _emit_events(self, invocation_id: int, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        payload = {"events": events}
        await self._post_json(
            f"/api/agent-runtime/{int(invocation_id)}/events",
            payload,
            timeout=20.0,
            attempts=min(_EVENT_ATTEMPTS, self.max_attempts),
        )

    async def _complete(self, invocation_id: int, payload: dict[str, Any]) -> None:
        await self._post_json(
            f"/api/agent-runtime/{int(invocation_id)}/complete",
            payload,
            timeout=30.0,
            attempts=self.max_attempts,
        )

    def _record_error(self, message: str) -> None:
        state = getattr(self.bootstrap, "state", None)
        if state is None:
            return
        try:
            state.last_error = message
            save = getattr(self.bootstrap, "_save_state", None)
            if callable(save):
                save()
        except Exception as exc:  # recording diagnostics must never break processing
            log.debug("could not record pull runtime error: %s", exc)

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

    async def _process_invocation(self, invocation: dict[str, Any]) -> bool:
        """Process one invocation. Returns False when it could not be completed normally."""
        invocation_id = int(invocation.get("id") or 0)
        invocation_type = str(invocation.get("invocation_type") or "").strip().lower()
        try:
            if invocation_type == "chat_turn":
                await self._process_chat_turn(invocation)
                return True
            if invocation_type in {"bounty_execute", "requirement_job"}:
                await self._process_bounty_execute(invocation)
                return True
            await self._complete(
                invocation_id,
                {
                    "status": "cancelled",
                    "error": f"Unsupported invocation_type: {invocation_type or 'unknown'}",
                    "metadata": {"invocation_type": invocation_type},
                },
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Pull runtime invocation %s failed: %s", invocation_id, exc)
            try:
                await self._emit_events(
                    invocation_id,
                    [{"event_type": "error", "message": str(exc), "payload": {"detail": str(exc)}}],
                )
            except asyncio.CancelledError:
                raise
            except Exception as emit_exc:
                log.warning("Could not report error events for invocation %s: %s", invocation_id, emit_exc)
            try:
                await self._complete(
                    invocation_id,
                    {
                        "status": "failed",
                        "error": str(exc),
                        "reply": f"> Runtime request failed: {str(exc)}",
                        "metadata": {"exception": str(exc)},
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as complete_exc:
                message = f"pull_runtime_complete_failed: invocation {invocation_id}: {complete_exc}"
                self._record_error(message)
                log.error(
                    "Could not mark invocation %s failed; Foundry will requeue it after its stale-claim timeout: %s",
                    invocation_id, complete_exc,
                )
            return False

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
                self._record_error(f"pull_runtime_failed: {exc}")
                log.warning("Foundry pull runtime loop error: %s", exc)
                await asyncio.sleep(max(self.poll_interval_seconds, 1.0))
