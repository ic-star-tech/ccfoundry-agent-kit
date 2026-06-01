from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _normalize_invocation_id(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return normalized if normalized > 0 else 0


def foundry_llm_metadata(
    billing_context: Any,
    *,
    agent_name: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build safe LiteLLM metadata for Foundry resource-cost accounting."""
    context = _model_dump(billing_context)
    metadata = dict(extra or {})

    invocation_id = _normalize_invocation_id(
        metadata.get("foundry_invocation_id")
        or metadata.get("invocation_id")
        or context.get("invocation_id")
    )
    requirement_id = str(
        metadata.get("foundry_requirement_id")
        or metadata.get("requirement_id")
        or context.get("requirement_id")
        or ""
    ).strip()
    resolved_agent_name = str(
        agent_name
        or metadata.get("foundry_agent_name")
        or metadata.get("agent_name")
        or context.get("agent_name")
        or ""
    ).strip()

    if invocation_id:
        metadata["foundry_invocation_id"] = invocation_id
    if requirement_id:
        metadata["foundry_requirement_id"] = requirement_id
    if resolved_agent_name:
        metadata["foundry_agent_name"] = resolved_agent_name

    sanitized_context = {
        key: value
        for key, value in context.items()
        if key in {"invocation_id", "requirement_id", "job_name", "agent_name"}
        and value not in (None, "")
    }
    if sanitized_context:
        if invocation_id:
            sanitized_context["invocation_id"] = invocation_id
        if requirement_id:
            sanitized_context["requirement_id"] = requirement_id
        if resolved_agent_name:
            sanitized_context["agent_name"] = resolved_agent_name
        metadata["billing_context"] = sanitized_context

    return metadata

