from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI

from ccfoundry_agent_kit import (
    AgentManifest,
    ChatRequest,
    ChatResponse,
    ContextMode,
    FoundryBootstrap,
    FoundryBootstrapConfig,
    FoundrySandboxClientError,
    compute_skills_hash,
    create_agent_app,
    scan_loaded_skills,
    scan_slash_commands,
)
from ccfoundry_agent_kit.agent_space import AgentSpace


_configured_base_dir = os.getenv("ME_AGENT_BASE_DIR", "").strip()
BASE_DIR = (
    Path(_configured_base_dir).expanduser().resolve()
    if _configured_base_dir
    else Path(__file__).resolve().parents[2]
)
load_dotenv(BASE_DIR / ".env", override=False)


def _load_config() -> dict:
    path = BASE_DIR / "agent_space" / "config.yaml"
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


CONFIG = _load_config()
SKILLS_DIR = BASE_DIR / "agent_space" / "skills"


def _compute_skills_hash() -> str:
    return compute_skills_hash(SKILLS_DIR)


def _loaded_skills() -> list[str]:
    configured = CONFIG.get("loaded_skills")
    if isinstance(configured, list):
        values = [str(item).strip() for item in configured if str(item).strip()]
        if values:
            return values
    return scan_loaded_skills(SKILLS_DIR)


def _default_slash_commands() -> list[dict]:
    return scan_slash_commands(SKILLS_DIR)


def _manifest_config() -> dict:
    raw = CONFIG.get("manifest")
    if not isinstance(raw, dict):
        raw = {}
    dashboard = raw.get("dashboard") if isinstance(raw.get("dashboard"), dict) else {}
    infra = raw.get("infra") if isinstance(raw.get("infra"), dict) else {}
    mcp = raw.get("mcp") if isinstance(raw.get("mcp"), dict) else {}
    llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
    return {
        "llm": {
            "needs_gateway": bool(llm.get("needs_gateway", True)),
        },
        "dashboard": {
            "soul_visible": bool(dashboard.get("soul_visible", True)),
            "soul_editable": bool(dashboard.get("soul_editable", True)),
            "model_editable": bool(dashboard.get("model_editable", True)),
            "config_editable": bool(dashboard.get("config_editable", True)),
            "reflection_visible": bool(dashboard.get("reflection_visible", True)),
            "vault_visible": bool(dashboard.get("vault_visible", True)),
        },
        "infra": {
            "heartbeat_managed": bool(infra.get("heartbeat_managed", True)),
            "reflection_managed": bool(infra.get("reflection_managed", True)),
        },
        "mcp": {
            "accepts_foundry_mcp": bool(mcp.get("accepts_foundry_mcp", True)),
            "has_own_mcp": bool(mcp.get("has_own_mcp", False)),
        },
    }


def _manifest_capabilities() -> list[str]:
    raw = os.getenv("ME_AGENT_CAPABILITIES", "").strip()
    if raw:
        values = [item.strip() for item in raw.split(",") if item.strip()]
        if values:
            return values
    configured = CONFIG.get("capabilities")
    if isinstance(configured, list):
        values = [str(item).strip() for item in configured if str(item).strip()]
        if values:
            return values
    return ["chat", "notes", "inline", "self_hosted"]


def _build_manifest() -> AgentManifest:
    provider_org = (
        os.getenv("ME_AGENT_PROVIDER_ORG", "").strip()
        or str((CONFIG.get("metadata") or {}).get("provider", {}).get("organization") or "").strip()
        or "CCFoundry Agent Kit"
    )
    manifest_config = _manifest_config()
    slash_commands = CONFIG.get("slash_commands")
    if not isinstance(slash_commands, list) or not slash_commands:
        slash_commands = _default_slash_commands()
    features = CONFIG.get("features")
    if not isinstance(features, list):
        features = ["chat"]
    needs_gateway = bool(((manifest_config.get("llm") or {}).get("needs_gateway", True)))
    billing_model = "foundry_gateway" if needs_gateway else "self_hosted"
    billing_note = (
        "Uses Foundry-provided LLM credentials after approval; local env or dev-board overrides can still replace them."
        if needs_gateway
        else "Uses local or self-hosted model credentials."
    )
    return AgentManifest(
        name=os.getenv("ME_AGENT_NAME", "").strip() or str(CONFIG.get("name") or "me_agent"),
        label=os.getenv("ME_AGENT_LABEL", "").strip() or str(CONFIG.get("label") or "Me Agent"),
        version=os.getenv("ME_AGENT_VERSION", "").strip() or str(CONFIG.get("version") or "0.1.0"),
        description=(
            os.getenv("ME_AGENT_DESCRIPTION", "").strip()
            or str(CONFIG.get("description") or "Self-hosted personal agent example for local Foundry-style development.")
        ),
        capabilities=_manifest_capabilities(),
        manifest=manifest_config,
        features=[str(item).strip() for item in features if str(item).strip()],
        loaded_skills=_loaded_skills(),
        slash_commands=slash_commands,
        billing={"model": billing_model, "fee_note": billing_note},
        metadata={"provider": {"organization": provider_org}},
    )


MANIFEST = _build_manifest()


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, fallback: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(fallback)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_json(name: str, fallback: dict) -> dict:
    raw = os.getenv(name, "").strip()
    if not raw:
        return dict(fallback)
    try:
        payload = json.loads(raw)
    except Exception:
        return dict(fallback)
    return payload if isinstance(payload, dict) else dict(fallback)


def _env_list_or_config(name: str, fallback: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [str(item).strip() for item in fallback if str(item).strip()]


def _service_fee_settings() -> dict[str, float]:
    raw = CONFIG.get("service_fee") if isinstance(CONFIG.get("service_fee"), dict) else {}
    try:
        base = float(raw.get("base", 0.0) or 0.0)
    except (TypeError, ValueError):
        base = 0.0
    try:
        per_action = float(raw.get("per_action", 0.0) or 0.0)
    except (TypeError, ValueError):
        per_action = 0.0
    try:
        max_per_call = float(raw.get("max_per_call", 1.0) or 1.0)
    except (TypeError, ValueError):
        max_per_call = 1.0
    return {
        "base": max(0.0, base),
        "per_action": max(0.0, per_action),
        "max_per_call": max(0.0, max_per_call),
    }


def _compute_service_fee(action_labels: list[str]) -> tuple[float, str]:
    settings = _service_fee_settings()
    action_count = len([label for label in action_labels if str(label).strip()])
    fee = min(
        settings["max_per_call"],
        settings["base"] + settings["per_action"] * action_count,
    )
    if fee <= 0:
        return 0.0, ""
    detail = (
        f"{action_count} actions x ${settings['per_action']:.2f} + "
        f"base ${settings['base']:.2f} = ${fee:.2f}"
    )
    if action_labels:
        detail += " [" + ", ".join(action_labels) + "]"
    return round(fee, 4), detail


def _build_foundry_bootstrap() -> FoundryBootstrap | None:
    foundry_cfg = CONFIG.get("foundry") if isinstance(CONFIG.get("foundry"), dict) else {}
    enabled = _env_truthy("FOUNDRY_DISCOVERY_ENABLE", bool(foundry_cfg.get("enabled")))
    foundry_base_url_env = os.getenv("FOUNDRY_BASE_URL")
    if foundry_base_url_env is None:
        foundry_base_url = str(foundry_cfg.get("base_url") or "").strip()
    else:
        foundry_base_url = foundry_base_url_env.strip()
    public_base_url_env = os.getenv("FOUNDRY_AGENT_PUBLIC_URL")
    if public_base_url_env is None:
        public_base_url = str(foundry_cfg.get("public_base_url") or "").strip()
    else:
        public_base_url = public_base_url_env.strip()
    if not enabled or not public_base_url:
        return None

    heartbeat_interval = int(os.getenv("FOUNDRY_HEARTBEAT_SECONDS", str(foundry_cfg.get("heartbeat_interval_seconds") or 30)).strip() or "30")
    tags = _env_list("FOUNDRY_DISCOVERY_TAGS", foundry_cfg.get("tags") or ["me_agent", "personal", "self_hosted"])
    requirements = _env_json("FOUNDRY_REQUIREMENTS_JSON", foundry_cfg.get("requirements") or {})
    resource_requests = _env_json("FOUNDRY_RESOURCE_REQUESTS_JSON", foundry_cfg.get("resource_requests") or {})
    budget = _env_json("FOUNDRY_BUDGET_JSON", foundry_cfg.get("budget") or {})
    governance = _env_json("FOUNDRY_GOVERNANCE_JSON", foundry_cfg.get("governance") or {})
    profile_metadata = _env_json("FOUNDRY_PROFILE_METADATA_JSON", foundry_cfg.get("profile_metadata") or {})
    workspace_features = _env_list_or_config(
        "FOUNDRY_WORKSPACE_FEATURES",
        foundry_cfg.get("workspace_features") or ["chat"],
    )
    interaction_modes = _env_list_or_config(
        "FOUNDRY_INTERACTION_MODES",
        foundry_cfg.get("interaction_modes") or ["chat"],
    )
    terminal_provider = (
        os.getenv("FOUNDRY_TERMINAL_PROVIDER", "").strip()
        or str(foundry_cfg.get("terminal_provider") or "none").strip()
        or "none"
    )
    bootstrap_delivery = (
        os.getenv("FOUNDRY_BOOTSTRAP_DELIVERY", "").strip()
        or str(foundry_cfg.get("bootstrap_delivery") or "push").strip()
        or "push"
    )
    discovery_claim_token = (
        os.getenv("FOUNDRY_DISCOVERY_CLAIM_TOKEN", "").strip()
        or str(foundry_cfg.get("discovery_claim_token") or "").strip()
        or None
    )
    developer_identity = _env_json(
        "FOUNDRY_DEVELOPER_IDENTITY_JSON",
        foundry_cfg.get("developer_identity") or {},
    )
    resource_contract_preview = _env_json(
        "FOUNDRY_RESOURCE_CONTRACT_PREVIEW_JSON",
        foundry_cfg.get("resource_contract_preview") or {},
    )
    network_zone = os.getenv("FOUNDRY_NETWORK_ZONE", "").strip() or str(foundry_cfg.get("network_zone") or "EXTERNAL")
    runtime_transport = (
        os.getenv("FOUNDRY_RUNTIME_TRANSPORT", "").strip()
        or str(foundry_cfg.get("runtime_transport") or "http_push").strip()
        or "http_push"
    )
    callback_secret = os.getenv("FOUNDRY_CALLBACK_SECRET", "").strip() or str(foundry_cfg.get("callback_secret") or "").strip() or None
    discovery_nonce = os.getenv("FOUNDRY_DISCOVERY_NONCE", "").strip() or str(foundry_cfg.get("discovery_nonce") or "").strip() or None
    state_path = (
        os.getenv("FOUNDRY_BOOTSTRAP_STATE_PATH", "").strip()
        or str(foundry_cfg.get("state_path") or "").strip()
        or str(BASE_DIR / "agent_space" / ".foundry_bootstrap.json")
    )

    return FoundryBootstrap(
        manifest=MANIFEST,
        agent_space_dir=BASE_DIR,
        config=FoundryBootstrapConfig(
            enabled=True,
            foundry_base_url=foundry_base_url,
            public_base_url=public_base_url,
            network_zone=network_zone,
            runtime_transport=runtime_transport,
            heartbeat_interval_seconds=max(10, heartbeat_interval),
            bootstrap_delivery=bootstrap_delivery,
            discovery_nonce=discovery_nonce,
            discovery_claim_token=discovery_claim_token,
            callback_secret=callback_secret,
            state_path=state_path,
            developer_identity=developer_identity,
            tags=tags,
            requirements=requirements,
            resource_requests=resource_requests,
            budget=budget,
            governance=governance,
            profile_metadata=profile_metadata,
            resource_contract_preview=resource_contract_preview,
            workspace_features=workspace_features,
            interaction_modes=interaction_modes,
            terminal_provider=terminal_provider,
            system_manifest={
                "agent_space_dir": str(BASE_DIR / "agent_space"),
                "example": "me_agent",
                "model": os.getenv("ME_AGENT_MODEL", "").strip() or str(CONFIG.get("model") or "ccfoundry-local"),
                "loaded_skills": MANIFEST.loaded_skills,
            },
            runtime_state={
                "mode": "demo",
                "skills_hash": _compute_skills_hash(),
                "loaded_skills": MANIFEST.loaded_skills,
            },
        ),
    )


def _strip_wrapping_delimiters(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text).rstrip("`").strip()
    return text.strip(" \n\r\t`'\"“”‘’")


def _extract_fenced_code_block(text: str) -> str:
    match = re.search(r"```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```", str(text or ""), re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _normalize_workspace_location(raw_path: str, *, allow_root: bool) -> str | None:
    path = str(raw_path or "").strip().replace("\\", "/")
    if not path:
        return None
    if path.startswith("/workspace/"):
        path = f"workspace/{path[len('/workspace/'):]}"
    elif path == "/workspace":
        path = "workspace"
    else:
        path = path.lstrip("/")
        if not path.startswith("workspace/"):
            path = "workspace" if path == "workspace" else f"workspace/{path}"

    parts = [part for part in path.split("/") if part]
    if not parts or parts[0] != "workspace":
        return None
    if any(part in {".", ".."} for part in parts[1:]):
        return None
    if len(parts) < 2 and not allow_root:
        return None
    return "/".join(parts)


def _normalize_workspace_path(raw_path: str) -> str | None:
    return _normalize_workspace_location(raw_path, allow_root=False)


def _normalize_workspace_dir(raw_path: str) -> str | None:
    return _normalize_workspace_location(raw_path, allow_root=True)


def _normalize_terminal_pip_install_command(command: str) -> tuple[str, bool, str]:
    raw_command = str(command or "").strip()
    if not raw_command:
        return raw_command, False, ""
    try:
        parts = shlex.split(raw_command)
    except Exception:
        return raw_command, False, ""
    if not parts:
        return raw_command, False, ""

    install_idx: int | None = None
    if parts[0] in {"pip", "pip3"} and len(parts) >= 2 and parts[1] == "install":
        install_idx = 1
    elif (
        len(parts) >= 4
        and parts[0].split("/")[-1].startswith("python")
        and parts[1] == "-m"
        and parts[2] == "pip"
        and parts[3] == "install"
    ):
        install_idx = 3
    if install_idx is None:
        return raw_command, False, ""

    install_args = list(parts[install_idx + 1 :])
    lower_args = [str(arg).lower() for arg in install_args]
    if any(flag in lower_args for flag in ("--target", "--prefix", "-t")):
        return raw_command, False, ""

    requested_packages: list[str] = []
    for arg in install_args:
        if not arg or str(arg).startswith("-"):
            continue
        normalized = re.split(r"[<>=!~\[]", str(arg), maxsplit=1)[0].strip().lower()
        if normalized:
            requested_packages.append(normalized)

    preinstalled = {"requests", "beautifulsoup4", "bs4"}
    if requested_packages and set(requested_packages).issubset(preinstalled):
        verify_cmd = (
            "python3 -c "
            + shlex.quote(
                "import requests, bs4; "
                "print('dependencies already available: requests beautifulsoup4')"
            )
        )
        return verify_cmd, True, "preinstalled_python_deps"

    normalized_args = [arg for arg in install_args if str(arg).strip()]
    if "--user" not in lower_args:
        normalized_args = ["--user", *normalized_args]
    rewritten = (
        "PYTHONUSERBASE=/workspace/.local "
        "PIP_CACHE_DIR=/workspace/.cache/pip "
        "PIP_DISABLE_PIP_VERSION_CHECK=1 "
        "python3 -m pip install "
        + shlex.join(normalized_args)
    )
    return rewritten, True, "workspace_userbase"


def _looks_like_terminal_prompt(line: str) -> bool:
    return bool(re.match(r"^.+@.+:.*[#$]\s*$", str(line or "").rstrip()))


def _extract_terminal_command_output(state: dict, command: str) -> str:
    command_text = str(command or "").strip()
    last_lines = [str(line) for line in list(state.get("last_lines") or [])]
    for index in range(len(last_lines) - 1, -1, -1):
        candidate = last_lines[index].rstrip()
        if candidate.endswith(f"$ {command_text}") or candidate.endswith(f"# {command_text}") or candidate == command_text:
            output_lines: list[str] = []
            for line in last_lines[index + 1 :]:
                if _looks_like_terminal_prompt(line):
                    break
                output_lines.append(line)
            output = "\n".join(output_lines).strip("\n")
            if output:
                return output
            break

    capture_text = str(state.get("capture_text") or "").strip()
    if not capture_text:
        return ""
    return capture_text[-4000:]


def _truncate_text(value: str, *, max_chars: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 32].rstrip() + "\n... [truncated]"


def _render_workspace_tree(node: dict, *, max_depth: int = 2, level: int = 0) -> list[str]:
    if not isinstance(node, dict):
        return []
    name = str(node.get("name") or node.get("path") or ".")
    is_dir = bool(node.get("is_dir"))
    prefix = "  " * level
    icon = "📁" if is_dir else "📄"
    lines = [f"{prefix}{icon} {name}"]
    if level >= max_depth:
        return lines
    children = node.get("children")
    if not isinstance(children, list):
        return lines
    for child in children:
        lines.extend(_render_workspace_tree(child, max_depth=max_depth, level=level + 1))
    return lines


def _parse_sandbox_request(message: str) -> dict | None:
    stripped = str(message or "").strip()
    if not stripped:
        return None
    lower = stripped.lower()
    fenced = _extract_fenced_code_block(stripped)

    exec_patterns = [
        r"^\s*(?:请|帮我|麻烦)?\s*在\s*(?:sandbox|沙箱)\s*(?:里|中)?执行\s*[:：]?\s*([\s\S]+?)\s*$",
        r"^\s*(?:/sandbox\s+exec|sandbox\s+exec)\s+([\s\S]+?)\s*$",
        r"^\s*(?:run|execute)\s+([\s\S]+?)\s+(?:in|inside)\s+sandbox\s*$",
    ]
    for pattern in exec_patterns:
        match = re.match(pattern, stripped, re.IGNORECASE)
        if not match:
            continue
        raw_command = fenced or _strip_wrapping_delimiters(match.group(1))
        raw_command = raw_command.rstrip("。；;")
        if not raw_command:
            return None
        command, rewritten, rewrite_note = _normalize_terminal_pip_install_command(raw_command)
        return {
            "type": "exec",
            "command": command,
            "original_command": raw_command,
            "rewritten": rewritten,
            "rewrite_note": rewrite_note,
        }

    if re.match(r"^\s*(?:/sandbox\s+status|sandbox\s+status)\s*$", stripped, re.IGNORECASE) or (
        ("sandbox" in lower or "沙箱" in stripped) and ("状态" in stripped or re.search(r"\bstatus\b", lower))
    ):
        return {"type": "status"}

    if re.match(r"^\s*(?:/sandbox\s+tree|sandbox\s+tree)\s*$", stripped, re.IGNORECASE) or (
        ("sandbox" in lower or "沙箱" in stripped or "workspace" in lower)
        and any(keyword in stripped for keyword in ("列出", "看看", "查看", "目录", "文件", "树", "有哪些"))
    ):
        return {"type": "tree", "path": "workspace", "depth": 2}

    read_patterns = [
        r"^\s*(?:/sandbox\s+read|sandbox\s+read)\s+([^\s`]+)\s*$",
        r"^\s*(?:读取|查看|read|cat)\s*(?:sandbox|沙箱)?(?:\s*(?:里的|中的))?\s*(?:文件)?\s*[:：]?\s*([^\s`]+)\s*$",
    ]
    for pattern in read_patterns:
        match = re.match(pattern, stripped, re.IGNORECASE)
        if not match:
            continue
        path = _normalize_workspace_path(_strip_wrapping_delimiters(match.group(1)))
        if path:
            return {"type": "read", "path": path}

    write_patterns = [
        r"^\s*(?:/sandbox\s+write|sandbox\s+write)\s+([^\s`]+)\s*[:：]?\s*([\s\S]*)$",
        r"^\s*(?:写入|保存到)\s*(?:sandbox|沙箱)?(?:\s*(?:文件))?\s*([^\s`：:]+)\s*[:：]\s*([\s\S]+)$",
    ]
    for pattern in write_patterns:
        match = re.match(pattern, stripped, re.IGNORECASE)
        if not match:
            continue
        path = _normalize_workspace_path(_strip_wrapping_delimiters(match.group(1)))
        content = fenced or str(match.group(2) or "").strip()
        if path and content:
            return {"type": "write", "path": path, "content": content}

    return None


async def _handle_sandbox_request(request: ChatRequest) -> ChatResponse | None:
    action = _parse_sandbox_request(request.message)
    if not action:
        return None
    if not FOUNDRY_BOOTSTRAP:
        return ChatResponse(
            reply="This agent has not enabled Foundry bootstrap yet, so the sandbox control plane is not available.",
            metadata={"mode": request.mode.value, "path": "sandbox_unavailable", "sandbox_action": action["type"]},
        )

    try:
        client = FOUNDRY_BOOTSTRAP.sandbox_client(timeout=20.0)
    except FoundrySandboxClientError as exc:
        return ChatResponse(
            reply=f"No Foundry sandbox is currently available: {exc}",
            metadata={"mode": request.mode.value, "path": "sandbox_unavailable", "sandbox_action": action["type"]},
        )

    try:
        if action["type"] == "status":
            status = await client.status()
            session = dict(status.get("sandbox_session") or {})
            runtime_minutes = status.get("runtime_minutes")
            idle_minutes = status.get("idle_minutes")
            lines = [
                "Sandbox is available.",
                f"- Active: `{bool(status.get('active'))}`",
                f"- Workspace profile: `{status.get('workspace_profile') or 'unknown'}`",
                f"- CWD: `{((status.get('lease_activity') or {}).get('cwd') or '/workspace')}`",
            ]
            if session.get("container_name"):
                lines.append(f"- Container: `{session['container_name']}`")
            if runtime_minutes is not None:
                lines.append(f"- Runtime: `{runtime_minutes}` minutes")
            if idle_minutes is not None:
                lines.append(f"- Idle: `{idle_minutes}` minutes")
            service_fee, service_fee_detail = _compute_service_fee(["sandbox_status"])
            return ChatResponse(
                reply="\n".join(lines),
                metadata={"mode": request.mode.value, "path": "sandbox_status", "sandbox_action": "status"},
                service_fee=service_fee,
                service_fee_detail=service_fee_detail,
            )

        if action["type"] == "tree":
            tree = await client.workspace_tree(depth=int(action.get("depth") or 2))
            rendered = _render_workspace_tree(tree, max_depth=2)
            reply = "Current sandbox workspace tree:\n\n```text\n" + "\n".join(rendered) + "\n```"
            service_fee, service_fee_detail = _compute_service_fee(["sandbox_tree"])
            return ChatResponse(
                reply=reply,
                metadata={"mode": request.mode.value, "path": "sandbox_tree", "sandbox_action": "tree"},
                service_fee=service_fee,
                service_fee_detail=service_fee_detail,
            )

        if action["type"] == "read":
            content = await client.workspace_read_text(action["path"])
            rendered = _truncate_text(content, max_chars=5000)
            reply = f"Read `{action['path']}`:\n\n```text\n{rendered}\n```"
            service_fee, service_fee_detail = _compute_service_fee(["sandbox_read"])
            return ChatResponse(
                reply=reply,
                metadata={
                    "mode": request.mode.value,
                    "path": "sandbox_read",
                    "sandbox_action": "read",
                    "workspace_path": action["path"],
                },
                service_fee=service_fee,
                service_fee_detail=service_fee_detail,
            )

        if action["type"] == "write":
            await client.workspace_write(action["path"], action["content"])
            service_fee, service_fee_detail = _compute_service_fee(["sandbox_write"])
            return ChatResponse(
                reply=f"Wrote content to `{action['path']}`.",
                metadata={
                    "mode": request.mode.value,
                    "path": "sandbox_write",
                    "sandbox_action": "write",
                    "workspace_path": action["path"],
                },
                service_fee=service_fee,
                service_fee_detail=service_fee_detail,
            )

        if action["type"] == "exec":
            result = await client.terminal_exec(
                action["command"],
                wait_ms=600,
                capture_lines=120,
                clear_line=True,
                enter=True,
            )
            state = dict(result.get("state") or {})
            output = _extract_terminal_command_output(state, action["command"])
            cwd = str(state.get("cwd") or "/workspace")
            output_block = _truncate_text(output.strip() or "(no output)", max_chars=5000)
            reply_lines = [
                f"Executed `{action['original_command']}` in the Foundry sandbox.",
                f"- Working directory: `{cwd}`",
            ]
            if action.get("rewritten"):
                reply_lines.append(
                    f"- Note: the command was rewritten to comply with sandbox restrictions: `{action['command']}`"
                )
            reply = "\n".join(reply_lines) + f"\n\n```text\n{output_block}\n```"
            service_fee, service_fee_detail = _compute_service_fee(["sandbox_exec"])
            return ChatResponse(
                reply=reply,
                metadata={
                    "mode": request.mode.value,
                    "path": "sandbox_exec",
                    "sandbox_action": "exec",
                    "command": action["command"],
                    "original_command": action["original_command"],
                    "cwd": cwd,
                },
                service_fee=service_fee,
                service_fee_detail=service_fee_detail,
            )
    except Exception as exc:
        service_fee, service_fee_detail = _compute_service_fee([f"sandbox_{action['type']}_error"])
        return ChatResponse(
            reply=f"I tried to run the sandbox action `{action['type']}`, but it failed: {exc}",
            metadata={
                "mode": request.mode.value,
                "path": "sandbox_error",
                "sandbox_action": action["type"],
                "error": str(exc),
            },
            service_fee=service_fee,
            service_fee_detail=service_fee_detail,
        )

    return None


def _extract_memory_update(message: str) -> str:
    stripped = str(message or "").strip()
    lowered = stripped.lower()
    for prefix in ("记住", "帮我记住", "remember", "please remember"):
        if lowered.startswith(prefix.lower()):
            candidate = stripped[len(prefix):].strip(" ：:，,")
            return candidate or stripped
    return ""


def _build_prompt(request: ChatRequest, agent_space: AgentSpace) -> str:
    soul = agent_space.read_text("agent_space/SOUL.md", default="You are a personal agent.")
    notes = agent_space.recent_notes(limit_chars=1800).strip()
    task_board = agent_space.read_text("agent_space/task.md", default="").strip()
    mode_hint = (
        "Respond briefly because you are being called inline from another conversation."
        if request.mode == ContextMode.INLINE
        else "You are speaking directly to your user."
    )
    notes_block = f"\n\nRecent notes:\n{notes}" if notes else ""
    task_block = f"\n\nTask board:\n{task_board[-1200:]}" if task_board else ""
    return f"{soul}\n\nMode hint: {mode_hint}{notes_block}{task_block}"


def _bootstrap_env(name: str) -> str:
    if not FOUNDRY_BOOTSTRAP:
        return ""
    return str((FOUNDRY_BOOTSTRAP.state.env_vars or {}).get(name) or "").strip()


def _bootstrap_allowed_models() -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    candidates = [
        _bootstrap_env("LLM_ALLOWED_MODELS_JSON"),
        _bootstrap_env("FOUNDRY_ALLOWED_MODELS_JSON"),
        _bootstrap_env("LLM_ALLOWED_MODELS"),
    ]
    for raw in candidates:
        if not raw:
            continue
        parsed: list[str] = []
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except Exception:
                decoded = []
            if isinstance(decoded, list):
                parsed = [str(item).strip() for item in decoded if str(item).strip()]
        else:
            parsed = [item.strip() for item in stripped.split(",") if item.strip()]
        for item in parsed:
            if item not in seen:
                seen.add(item)
                values.append(item)
    return values


def _request_llm_overrides(request: ChatRequest) -> dict[str, str]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    raw = metadata.get("dev_overrides")
    if not isinstance(raw, dict):
        return {}
    allowed = {"model", "base_url", "api_key"}
    return {
        key: str(value).strip()
        for key, value in raw.items()
        if key in allowed and str(value).strip()
    }


def _llm_runtime_config(request: ChatRequest) -> dict[str, str]:
    overrides = _request_llm_overrides(request)
    local_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    bootstrap_api_key = _bootstrap_env("OPENAI_API_KEY") or _bootstrap_env("LLM_API_KEY")
    api_key = overrides.get("api_key") or local_api_key or bootstrap_api_key
    base_url = (
        overrides.get("base_url")
        or os.getenv("OPENAI_BASE_URL", "").strip()
        or _bootstrap_env("OPENAI_BASE_URL")
        or _bootstrap_env("LLM_API_BASE")
        or None
    )
    requested_model = (
        overrides.get("model")
        or os.getenv("ME_AGENT_MODEL", "").strip()
        or str(CONFIG.get("model") or "ccfoundry-local")
    )
    bootstrap_model = _bootstrap_env("LLM_MODEL")
    allowed_models = _bootstrap_allowed_models()
    model = requested_model
    if allowed_models:
        if model not in allowed_models:
            if bootstrap_model and bootstrap_model in allowed_models:
                model = bootstrap_model
            else:
                model = allowed_models[0]
    elif bootstrap_model:
        model = bootstrap_model
    if (bootstrap_model and model == bootstrap_model) or (allowed_models and model != requested_model):
        model_source = "foundry_policy"
    elif overrides and model == overrides.get("model"):
        model_source = "dev_override"
    elif bootstrap_api_key:
        model_source = "foundry_gateway"
    elif local_api_key:
        model_source = "local_env"
    else:
        model_source = "demo"
    return {
        "api_key": api_key,
        "base_url": str(base_url or "").strip(),
        "model": str(model).strip(),
        "model_source": model_source,
        "allowed_models": ",".join(allowed_models),
    }


async def _try_llm_reply(request: ChatRequest, agent_space: AgentSpace, runtime: dict[str, str]) -> str | None:
    api_key = runtime.get("api_key", "").strip()
    if not api_key:
        return None
    base_url = runtime.get("base_url", "").strip() or None
    model = runtime.get("model", "").strip() or "ccfoundry-local"
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    system_prompt = _build_prompt(request, agent_space)
    response = await client.chat.completions.create(
        model=model,
        temperature=float(CONFIG.get("temperature") or 0.3),
        max_tokens=int(CONFIG.get("max_tokens") or 400),
        user=request.user_id or "",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.message},
        ],
    )
    return (response.choices[0].message.content or "").strip() or None


def _fallback_reply(request: ChatRequest, agent_space: AgentSpace) -> str:
    notes = agent_space.recent_notes(limit_chars=600).strip()
    if request.mode == ContextMode.INLINE:
        prefix = "Inline answer"
    else:
        prefix = "Direct answer"
    if notes:
        return (
            f"{prefix}: I am currently running in local example mode."
            f" I will review the saved notes before I continue helping you.\n\nRecent notes:\n{notes[-260:]}"
        )
    return f"{prefix}: I am currently running in local example mode, and I do not have any long-term notes yet."


async def handle_chat(request: ChatRequest, agent_space: AgentSpace) -> ChatResponse:
    memory_update = _extract_memory_update(request.message)
    if memory_update:
        service_fee, service_fee_detail = _compute_service_fee(["notes_update"])
        return ChatResponse(
            reply=f"Saved. I will remember this: {memory_update}",
            notes_update=memory_update,
            metadata={"mode": request.mode.value, "path": "memory_update"},
            service_fee=service_fee,
            service_fee_detail=service_fee_detail,
        )

    sandbox_response = await _handle_sandbox_request(request)
    if sandbox_response is not None:
        return sandbox_response

    runtime = _llm_runtime_config(request)
    llm_reply = None
    llm_error = ""
    try:
        llm_reply = await _try_llm_reply(request, agent_space, runtime)
    except Exception as exc:
        llm_error = str(exc)
    reply = llm_reply or _fallback_reply(request, agent_space)
    service_fee, service_fee_detail = _compute_service_fee([])
    return ChatResponse(
        reply=reply,
        metadata={
            "mode": request.mode.value,
            "used_llm": bool(llm_reply),
            "llm_override_active": bool(_request_llm_overrides(request)),
            "llm_error": llm_error,
            "model": runtime.get("model", ""),
            "model_source": runtime.get("model_source", ""),
            "base_url": runtime.get("base_url", ""),
        },
        service_fee=service_fee,
        service_fee_detail=service_fee_detail,
    )


FOUNDRY_BOOTSTRAP = _build_foundry_bootstrap()


app = create_agent_app(
    manifest=MANIFEST,
    chat_handler=handle_chat,
    agent_space_dir=BASE_DIR,
    foundry_bootstrap=FOUNDRY_BOOTSTRAP,
)
