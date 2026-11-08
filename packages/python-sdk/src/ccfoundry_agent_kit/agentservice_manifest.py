"""Validate an AgentService manifest before uploading it as a Foundry local package.

A customer-built agent is installed on a Foundry instance as a Marketplace
*local package*: the instance admin uploads its ``AgentService`` manifest in
Admin Panel -> Marketplace -> Local packages. Foundry validates the manifest
when it is saved, and the marketplace runner checks the image again at install
time. This module repeats both sets of checks locally so mistakes show up on
the build machine instead of on the appliance.

Checks that need the target instance are not repeated here:

- ``metadata.name`` must not equal an official Marketplace catalog item name;
- ``spec.agent.agent_name`` must not collide with an agent that already exists
  on the instance (reinstalling or upgrading the same local package is fine);
- the image must already be present on the host with an image ID or
  RepoDigest equal to ``spec.image.digest``.

Foundry reports its save-time errors in two passes (the local-package checks
first, then the generic AgentService checks). This validator reports
everything it finds in one pass.

``local_manifest_warnings`` lists problems Foundry accepts but that break the
package at run time, such as a container that never receives ``AGENT_SECRET``.

Command line::

    python -m ccfoundry_agent_kit.agentservice_manifest manifest.yaml

Warnings are printed but do not change the exit status: 0 when the manifest
is valid, 1 when it has errors and 2 when the file cannot be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml

__all__ = [
    "DEFAULT_LOCAL_IMAGE_PREFIXES",
    "MAX_MANIFEST_BYTES",
    "OFFICIAL_IMAGE_PREFIXES",
    "local_manifest_warnings",
    "main",
    "validate_local_manifest",
]

# Foundry rejects larger manifests outright.
MAX_MANIFEST_BYTES = 256 * 1024

# Official Marketplace image prefixes. Local packages may not use them, so a
# customer package can never pass itself off as an official one.
OFFICIAL_IMAGE_PREFIXES: tuple[str, ...] = (
    "registry.cochiper.com/foundry/",
    "ccfoundryprod.azurecr.io/foundry/",
)

# Default of the runner's MARKETPLACE_LOCAL_IMAGE_PREFIXES: the repository of a
# local package must start with one of these.
DEFAULT_LOCAL_IMAGE_PREFIXES: tuple[str, ...] = ("local/",)

RESERVED_AGENT_NAME_PREFIX = "user_agent_"

# Same patterns as the Foundry server. Always used with fullmatch(): Python's
# "$" would also accept a trailing newline, Go's does not.
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,62}")
_REPOSITORY_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,254}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_ITEM_RE = re.compile(r"[A-Za-z0-9_-]+")


def _py_str(value: Any) -> str:
    """str() of a YAML scalar, the way Foundry stringifies manifest values."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return value
    return str(value)


def _to_str(value: Any) -> str:
    """Like _py_str, but a missing value is the empty string."""
    return "" if value is None else _py_str(value)


def _mapping(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _load(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse the manifest the way Foundry does; return (manifest, errors)."""
    if not isinstance(text, str) or not text.strip():
        return None, ["manifest is required"]
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        return None, [f"manifest is larger than {MAX_MANIFEST_BYTES} bytes"]
    try:
        # Foundry reads only the first YAML document; later ones are ignored.
        manifest = next(yaml.safe_load_all(text), None)
    except yaml.YAMLError as exc:
        return None, [f"manifest YAML is invalid: {exc}"]
    if not isinstance(manifest, dict):
        return None, ["manifest must be a YAML object"]
    return manifest, []


def validate_local_manifest(
    text: str,
    *,
    local_image_prefixes: Sequence[str] | None = DEFAULT_LOCAL_IMAGE_PREFIXES,
) -> list[str]:
    """Return the problems Foundry would report for ``text``; empty means valid.

    ``local_image_prefixes`` mirrors the runner's MARKETPLACE_LOCAL_IMAGE_PREFIXES
    setting on the target instance. Pass ``None`` (or an empty sequence) to skip
    that check, for example when validating a manifest for an instance whose
    operator allows another prefix that you do not know.
    """
    manifest, errors = _load(text)
    if manifest is None:
        return errors

    if _py_str(manifest.get("apiVersion")) != "foundry.ai/v1":
        errors.append("apiVersion must be foundry.ai/v1")
    raw_kind = manifest.get("kind")
    if _py_str(raw_kind) != "AgentService":
        errors.append(
            "kind must be AgentService (local packages are agent services), "
            f"got {json.dumps(_to_str(raw_kind))}"
        )

    # metadata
    metadata = _mapping(manifest.get("metadata"))
    if metadata is None:
        errors.append("metadata must be an object")
        metadata = {}
    raw_name = metadata.get("name")
    name = _to_str(raw_name).strip()
    if not _NAME_RE.fullmatch(name):
        errors.append(
            "metadata.name must be 2-63 characters of lowercase letters, digits and '-', "
            "starting with a letter or digit"
        )
    elif _py_str(raw_name) != name:
        errors.append("metadata.name must not have leading or trailing whitespace")
    raw_version = metadata.get("version")
    version = _to_str(raw_version).strip()
    if not version or len(version.encode("utf-8")) > 64 or not raw_version:
        errors.append("metadata.version is required (at most 64 characters)")

    # spec
    spec = _mapping(manifest.get("spec"))
    if spec is None:
        errors.append("spec must be an object")
        spec = {}

    image = _mapping(spec.get("image")) or {}
    repository = _to_str(image.get("repository")).strip()
    if not repository:
        errors.append("spec.image.repository is required")
    elif not _REPOSITORY_RE.fullmatch(repository) or ":" in repository.rsplit("/", 1)[-1]:
        # A colon is only allowed in the registry host part (host:port); one in
        # the last path segment is a tag.
        errors.append(
            "spec.image.repository is not a valid image repository (lowercase, no tag or digest)"
        )
    else:
        official = [prefix for prefix in OFFICIAL_IMAGE_PREFIXES if repository.startswith(prefix)]
        for prefix in official:
            errors.append(
                f"spec.image.repository may not use the official prefix {prefix}; "
                "push the image to your own registry or load it as local/<name>"
            )
        prefixes = [p for p in (local_image_prefixes or ()) if p]
        if not official and prefixes and not any(
            repository == p or repository.startswith(p) for p in prefixes
        ):
            errors.append(
                f"spec.image.repository {repository} is outside the local image prefixes "
                f"{list(prefixes)}; retag the image (docker tag ... local/<name>) or ask the "
                "instance operator to add its prefix to MARKETPLACE_LOCAL_IMAGE_PREFIXES"
            )
    digest = _to_str(image.get("digest")).strip()
    if not _DIGEST_RE.fullmatch(digest):
        errors.append(
            "spec.image.digest must be sha256:<64 hex> (the registry digest, or the image ID "
            "for a docker-loaded local/ image)"
        )

    runtime = _mapping(spec.get("runtime"))
    if runtime is None or not runtime.get("container_name"):
        errors.append("spec.runtime.container_name is required for AgentService")
    if runtime is None or not runtime.get("port"):
        errors.append("spec.runtime.port is required for AgentService")

    agent = _mapping(spec.get("agent"))
    if agent is None:
        errors.append("spec.agent is required for AgentService")
        agent = {}
    raw_agent_name = agent.get("agent_name")
    agent_name = _py_str(raw_agent_name).strip() if raw_agent_name else ""
    if not agent_name or not _SAFE_ITEM_RE.fullmatch(agent_name):
        errors.append(
            "spec.agent.agent_name is required and must be a safe identifier "
            "(letters, digits, '_' and '-')"
        )
    elif agent_name.startswith(RESERVED_AGENT_NAME_PREFIX):
        errors.append(
            f"spec.agent.agent_name {agent_name} uses the reserved prefix {RESERVED_AGENT_NAME_PREFIX}"
        )
    bridge = _mapping(agent.get("vscode_bridge"))
    if bridge is not None and not isinstance(bridge.get("enabled"), bool):
        errors.append("spec.agent.vscode_bridge.enabled must be a boolean for AgentService")

    # Foundry replaces spec.pricing of a local package (no install charge), but
    # it validates the declared value first.
    pricing = _mapping(spec.get("pricing"))
    if pricing is not None and pricing.get("install_credits") is not None:
        try:
            credits = int(pricing["install_credits"])
        except (TypeError, ValueError, OverflowError):
            credits = -1
        if credits < 0:
            errors.append("spec.pricing.install_credits must be a non-negative integer")

    return errors


def local_manifest_warnings(text: str) -> list[str]:
    """Return problems Foundry accepts but that break or weaken the package.

    These never make Foundry reject the manifest, so they are kept apart from
    ``validate_local_manifest``. A manifest that does not parse has no warnings.
    """
    manifest, _ = _load(text)
    if manifest is None:
        return []
    warnings: list[str] = []
    metadata = _mapping(manifest.get("metadata")) or {}
    spec = _mapping(manifest.get("spec")) or {}
    runtime = _mapping(spec.get("runtime")) or {}

    config_env = runtime.get("config_env")
    names = {str(item).strip() for item in config_env} if isinstance(config_env, list) else set()
    if "AGENT_SECRET" not in names:
        warnings.append(
            "spec.runtime.config_env does not list AGENT_SECRET: the container will not "
            "receive the secret Foundry sends as its bearer, so it cannot reject other callers"
        )
    if not runtime.get("endpoint_path") and spec.get("healthcheck_path"):
        warnings.append(
            "spec.healthcheck_path is set but spec.runtime.endpoint_path is not: the runner "
            "then uses the health path as the service URL and Foundry posts chat to "
            "<healthcheck_path>/api/chat; set spec.runtime.endpoint_path: /"
        )
    if not metadata.get("displayName") and not metadata.get("displayName_zh"):
        warnings.append("metadata.displayName or metadata.displayName_zh is recommended")
    return warnings


def _parse_prefixes(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ccfoundry_agent_kit.agentservice_manifest",
        description="Validate an AgentService manifest for upload as a Foundry Marketplace local package.",
    )
    parser.add_argument("manifest", help="path to the manifest YAML file, or - for stdin")
    parser.add_argument(
        "--local-prefixes",
        default=",".join(DEFAULT_LOCAL_IMAGE_PREFIXES),
        help=(
            "comma-separated image repository prefixes the target runner accepts "
            "(its MARKETPLACE_LOCAL_IMAGE_PREFIXES; default: %(default)s). "
            "An empty value skips this check."
        ),
    )
    args = parser.parse_args(argv)

    try:
        if args.manifest == "-":
            text = sys.stdin.read()
        else:
            text = Path(args.manifest).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {args.manifest}: {exc}", file=sys.stderr)
        return 2

    for warning in local_manifest_warnings(text):
        print(f"warning: {warning}", file=sys.stderr)
    errors = validate_local_manifest(text, local_image_prefixes=_parse_prefixes(args.local_prefixes))
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print(f"{args.manifest}: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"{args.manifest}: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
