from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("foundry_litellm_usage")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _metadata_value(metadata: dict[str, Any], key: str) -> Any:
    if metadata.get(key) not in (None, ""):
        return metadata.get(key)
    billing_context = metadata.get("billing_context")
    if isinstance(billing_context, dict):
        return billing_context.get(key)
    return None


class FoundryUsageCallback(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        endpoint = str(os.getenv("FOUNDRY_LITELLM_USAGE_URL") or "").strip()
        secret = str(os.getenv("FOUNDRY_LITELLM_USAGE_SECRET") or "").strip()
        if not endpoint or not secret:
            return

        standard = _as_dict((kwargs or {}).get("standard_logging_object"))
        metadata = _as_dict(standard.get("metadata"))
        if not metadata:
            litellm_params = _as_dict((kwargs or {}).get("litellm_params"))
            metadata = _as_dict(litellm_params.get("metadata"))

        if metadata.get("foundry_skip_usage_callback") is True:
            return

        invocation_id = _safe_int(
            metadata.get("foundry_invocation_id")
            or metadata.get("invocation_id")
            or _metadata_value(metadata, "invocation_id")
        )
        if invocation_id <= 0:
            return

        event_id = str(standard.get("id") or (kwargs or {}).get("litellm_call_id") or "").strip()
        if not event_id:
            trace_id = str(standard.get("trace_id") or "").strip()
            event_id = f"{trace_id}:{invocation_id}:{standard.get('model') or ''}".strip(":")
        if not event_id:
            return

        prompt_tokens = _safe_int(standard.get("prompt_tokens"))
        completion_tokens = _safe_int(standard.get("completion_tokens"))
        total_tokens = _safe_int(standard.get("total_tokens") or (prompt_tokens + completion_tokens))
        payload = {
            "litellm_event_id": event_id,
            "trace_id": str(standard.get("trace_id") or "").strip(),
            "invocation_id": invocation_id,
            "agent_name": str(
                metadata.get("foundry_agent_name")
                or metadata.get("agent_name")
                or _metadata_value(metadata, "agent_name")
                or ""
            ).strip(),
            "requirement_id": str(
                metadata.get("foundry_requirement_id")
                or metadata.get("requirement_id")
                or _metadata_value(metadata, "requirement_id")
                or ""
            ).strip(),
            "model": str(standard.get("model") or "").strip(),
            "model_group": str(standard.get("model_group") or "").strip(),
            "provider": str(standard.get("custom_llm_provider") or "litellm").strip() or "litellm",
            "response_cost": _safe_float(standard.get("response_cost")),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "metadata": metadata,
        }

        timeout = _safe_float(os.getenv("FOUNDRY_LITELLM_USAGE_TIMEOUT_SECONDS")) or 5.0
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        }
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(endpoint, json=payload, headers=headers)
                if 200 <= response.status_code < 300:
                    return
                log.warning(
                    "Foundry LiteLLM usage callback returned %s: %s",
                    response.status_code,
                    response.text[:240],
                )
            except Exception as exc:
                log.warning("Foundry LiteLLM usage callback failed: %s", exc)
            if attempt == 0:
                await asyncio.sleep(0.25)


foundry_usage_callback = FoundryUsageCallback()

