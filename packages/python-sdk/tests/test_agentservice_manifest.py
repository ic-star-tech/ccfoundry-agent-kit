from __future__ import annotations

import copy
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from ccfoundry_agent_kit.agentservice_manifest import (
    MAX_MANIFEST_BYTES,
    local_manifest_warnings,
    main,
    validate_local_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "agentservice"
EXAMPLE_MANIFEST = EXAMPLE_DIR / "manifest.yaml"
REAL_DIGEST = "sha256:" + "0123456789abcdef" * 4


def _example() -> dict[str, Any]:
    return yaml.safe_load(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))


def _dump(manifest: Any) -> str:
    return yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    """Mutation that sets a dotted path; missing parents are created."""

    def mutate(manifest: dict[str, Any]) -> None:
        *parents, leaf = path.split(".")
        node = manifest
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value

    return mutate


def _delete(path: str) -> Callable[[dict[str, Any]], None]:
    def mutate(manifest: dict[str, Any]) -> None:
        *parents, leaf = path.split(".")
        node = manifest
        for key in parents:
            node = node[key]
        del node[leaf]

    return mutate


def _errors_after(mutate: Callable[[dict[str, Any]], None], **kwargs: Any) -> list[str]:
    manifest = copy.deepcopy(_example())
    mutate(manifest)
    return validate_local_manifest(_dump(manifest), **kwargs)


def test_example_manifest_is_valid() -> None:
    assert validate_local_manifest(EXAMPLE_MANIFEST.read_text(encoding="utf-8")) == []


def test_example_manifest_is_valid_with_a_real_digest() -> None:
    assert _errors_after(_set("spec.image.digest", REAL_DIGEST)) == []


def test_example_manifest_matches_the_documented_package() -> None:
    manifest = _example()
    assert manifest["metadata"]["name"] == "me-agent"
    assert manifest["spec"]["image"]["repository"] == "local/me-agent"
    assert manifest["spec"]["runtime"]["container_name"] == "foundry-agent-me-agent"
    assert manifest["spec"]["runtime"]["port"] == 8080
    assert manifest["spec"]["agent"]["agent_name"] == "me_agent"
    assert manifest["spec"]["access"]["required_plan"] == "advanced"
    # Without this the container never receives the secret Foundry sends.
    assert "AGENT_SECRET" in manifest["spec"]["runtime"]["config_env"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        # apiVersion / kind
        (_set("apiVersion", "foundry.ai/v2"), "apiVersion must be foundry.ai/v1"),
        (_delete("apiVersion"), "apiVersion must be foundry.ai/v1"),
        (_set("kind", "McpService"), "kind must be AgentService"),
        (_set("kind", "AgentTemplate"), "kind must be AgentService"),
        (_set("kind", " AgentService "), "kind must be AgentService"),
        # metadata.name
        (_set("metadata.name", "Me-Agent"), "metadata.name must be 2-63 characters"),
        (_set("metadata.name", "m"), "metadata.name must be 2-63 characters"),
        (_set("metadata.name", "-me-agent"), "metadata.name must be 2-63 characters"),
        (_set("metadata.name", "me_agent"), "metadata.name must be 2-63 characters"),
        (_set("metadata.name", "a" * 64), "metadata.name must be 2-63 characters"),
        (_delete("metadata.name"), "metadata.name must be 2-63 characters"),
        (_set("metadata.name", " me-agent"), "metadata.name must not have leading or trailing whitespace"),
        (_set("metadata", "me-agent"), "metadata must be an object"),
        # metadata.version
        (_delete("metadata.version"), "metadata.version is required"),
        (_set("metadata.version", ""), "metadata.version is required"),
        (_set("metadata.version", "1" * 65), "metadata.version is required"),
        (_set("metadata.version", 0), "metadata.version is required"),
        # spec
        (_delete("spec"), "spec must be an object"),
        # spec.image.repository
        (_delete("spec.image.repository"), "spec.image.repository is required"),
        (_set("spec.image.repository", "local/Me-Agent"), "not a valid image repository"),
        (_set("spec.image.repository", "local/me-agent:0.1.0"), "not a valid image repository"),
        (_set("spec.image.repository", "local/me-agent@" + REAL_DIGEST), "not a valid image repository"),
        (_set("spec.image.repository", "/local/me-agent"), "not a valid image repository"),
        (
            _set("spec.image.repository", "registry.cochiper.com/foundry/me-agent"),
            "may not use the official prefix registry.cochiper.com/foundry/",
        ),
        (
            _set("spec.image.repository", "ccfoundryprod.azurecr.io/foundry/me-agent"),
            "may not use the official prefix ccfoundryprod.azurecr.io/foundry/",
        ),
        (_set("spec.image.repository", "docker.io/acme/me-agent"), "outside the local image prefixes"),
        (_set("spec.image.repository", "localhost/me-agent"), "outside the local image prefixes"),
        # spec.image.digest
        (_delete("spec.image.digest"), "spec.image.digest must be sha256:<64 hex>"),
        (_set("spec.image.digest", "sha256:" + "A" * 64), "spec.image.digest must be sha256:<64 hex>"),
        (_set("spec.image.digest", "sha256:" + "0" * 63), "spec.image.digest must be sha256:<64 hex>"),
        (_set("spec.image.digest", "sha512:" + "0" * 64), "spec.image.digest must be sha256:<64 hex>"),
        (_set("spec.image.digest", "0" * 64), "spec.image.digest must be sha256:<64 hex>"),
        # spec.runtime
        (_delete("spec.runtime.container_name"), "spec.runtime.container_name is required"),
        (_delete("spec.runtime.port"), "spec.runtime.port is required"),
        (_set("spec.runtime.port", 0), "spec.runtime.port is required"),
        (_delete("spec.runtime"), "spec.runtime.container_name is required"),
        # spec.agent
        (_delete("spec.agent"), "spec.agent is required"),
        (_delete("spec.agent.agent_name"), "spec.agent.agent_name is required and must be a safe identifier"),
        (_set("spec.agent.agent_name", "me agent"), "spec.agent.agent_name is required and must be a safe identifier"),
        (_set("spec.agent.agent_name", "me.agent"), "spec.agent.agent_name is required and must be a safe identifier"),
        (_set("spec.agent.agent_name", "user_agent_me"), "uses the reserved prefix user_agent_"),
        (_set("spec.agent.vscode_bridge", {"enabled": "true"}), "vscode_bridge.enabled must be a boolean"),
        (_set("spec.agent.vscode_bridge", {}), "vscode_bridge.enabled must be a boolean"),
        # spec.pricing is replaced for local packages, but validated first
        (_set("spec.pricing", {"install_credits": -1}), "install_credits must be a non-negative integer"),
        (_set("spec.pricing", {"install_credits": "ten"}), "install_credits must be a non-negative integer"),
    ],
)
def test_each_rule_rejects_a_bad_manifest(mutate: Callable[[dict[str, Any]], None], expected: str) -> None:
    errors = _errors_after(mutate)
    assert any(expected in error for error in errors), errors


@pytest.mark.parametrize(
    "mutate",
    [
        # Lower plans are raised to advanced by Foundry, not rejected.
        _set("spec.access.required_plan", "basic"),
        _delete("spec.access"),
        # vscode_bridge is optional; when declared, enabled must be a real boolean.
        _set("spec.agent.vscode_bridge", {"enabled": False}),
        _set("spec.agent.vscode_bridge", {"enabled": True}),
        # Declared prices are allowed (Foundry resets them to 0 for local packages).
        _set("spec.pricing", {"install_credits": 50}),
        _set("spec.pricing", {"install_credits": "5"}),
        # Agent names may use upper case, digits, '_' and '-'.
        _set("spec.agent.agent_name", "Me-Agent_2"),
        # Numeric YAML versions are fine.
        _set("metadata.version", 1.5),
        _set("spec.runtime.port", "8080"),
        # A deeper path under the local prefix.
        _set("spec.image.repository", "local/acme/me-agent"),
    ],
)
def test_accepted_variations(mutate: Callable[[dict[str, Any]], None]) -> None:
    assert _errors_after(mutate) == []


def test_local_prefixes_follow_the_runner_setting() -> None:
    repository = _set("spec.image.repository", "registry.example.com:5000/team/me-agent")
    assert any("outside the local image prefixes" in e for e in _errors_after(repository))
    assert _errors_after(repository, local_image_prefixes=["local/", "registry.example.com:5000/team/"]) == []
    assert _errors_after(repository, local_image_prefixes=None) == []
    # An allowed prefix never makes an official repository acceptable.
    official = _set("spec.image.repository", "registry.cochiper.com/foundry/me-agent")
    errors = _errors_after(official, local_image_prefixes=["registry.cochiper.com/"])
    assert any("official prefix" in e for e in errors)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "manifest is required"),
        ("   \n\t", "manifest is required"),
        ("kind: [unclosed", "manifest YAML is invalid"),
        ("- a\n- b\n", "manifest must be a YAML object"),
        ("just a string", "manifest must be a YAML object"),
    ],
)
def test_document_level_errors(text: str, expected: str) -> None:
    errors = validate_local_manifest(text)
    assert len(errors) == 1 and errors[0].startswith(expected), errors


def test_only_the_first_yaml_document_counts() -> None:
    # Foundry reads the first document and ignores the rest.
    base = EXAMPLE_MANIFEST.read_text(encoding="utf-8")
    assert validate_local_manifest(base + "\n---\nkind: Other\n") == []
    assert validate_local_manifest("---\n") == ["manifest must be a YAML object"]


def test_manifest_size_limit() -> None:
    base = EXAMPLE_MANIFEST.read_text(encoding="utf-8")
    padding = MAX_MANIFEST_BYTES - len(base.encode("utf-8"))
    at_limit = base + "#" * (padding - 1) + "\n"
    assert len(at_limit.encode("utf-8")) == MAX_MANIFEST_BYTES
    assert validate_local_manifest(at_limit) == []
    over = at_limit + "#"
    assert validate_local_manifest(over) == [f"manifest is larger than {MAX_MANIFEST_BYTES} bytes"]


def test_all_problems_are_reported_together() -> None:
    manifest = _example()
    manifest["apiVersion"] = "v1"
    manifest["spec"]["image"]["digest"] = "latest"
    manifest["spec"]["agent"]["agent_name"] = "user_agent_x"
    errors = validate_local_manifest(_dump(manifest))
    assert len(errors) == 3, errors


def test_example_manifest_has_no_warnings() -> None:
    assert local_manifest_warnings(EXAMPLE_MANIFEST.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (_delete("spec.runtime.config_env"), "does not list AGENT_SECRET"),
        (_set("spec.runtime.config_env", ["OPENAI_API_KEY"]), "does not list AGENT_SECRET"),
        (_set("spec.runtime.config_env", "AGENT_SECRET"), "does not list AGENT_SECRET"),
        (
            lambda m: (m["spec"]["runtime"].pop("endpoint_path"), m["spec"].update(healthcheck_path="/health")),
            "set spec.runtime.endpoint_path: /",
        ),
        (_delete("metadata.displayName"), "displayName"),
    ],
)
def test_warnings_do_not_make_the_manifest_invalid(
    mutate: Callable[[dict[str, Any]], None], expected: str
) -> None:
    manifest = copy.deepcopy(_example())
    mutate(manifest)
    text = _dump(manifest)
    assert validate_local_manifest(text) == []
    warnings = local_manifest_warnings(text)
    assert any(expected in warning for warning in warnings), warnings


def test_unparseable_manifest_has_no_warnings() -> None:
    assert local_manifest_warnings("kind: [unclosed") == []


def test_cli_prints_warnings_without_failing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _example()
    manifest["spec"]["runtime"]["config_env"] = []
    path = tmp_path / "manifest.yaml"
    path.write_text(_dump(manifest), encoding="utf-8")
    assert main([str(path)]) == 0
    assert "warning:" in capsys.readouterr().err


def test_cli_accepts_the_example(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(EXAMPLE_MANIFEST)]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_reports_errors_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _example()
    manifest["spec"]["image"]["repository"] = "registry.cochiper.com/foundry/me-agent"
    path = tmp_path / "manifest.yaml"
    path.write_text(_dump(manifest), encoding="utf-8")
    assert main([str(path)]) == 1
    assert "official prefix" in capsys.readouterr().err


def test_cli_local_prefixes_option(tmp_path: Path) -> None:
    manifest = _example()
    manifest["spec"]["image"]["repository"] = "registry.example.com/team/me-agent"
    path = tmp_path / "manifest.yaml"
    path.write_text(_dump(manifest), encoding="utf-8")
    assert main([str(path)]) == 1
    assert main([str(path), "--local-prefixes", "local/,registry.example.com/team/"]) == 0
    assert main([str(path), "--local-prefixes", ""]) == 0


def test_cli_missing_file_exits_2(tmp_path: Path) -> None:
    assert main([str(tmp_path / "missing.yaml")]) == 2


def test_module_runs_with_python_dash_m() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ccfoundry_agent_kit.agentservice_manifest", str(EXAMPLE_MANIFEST)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_example_dockerfile_copies_existing_paths() -> None:
    dockerfile = (EXAMPLE_DIR / "Dockerfile").read_text(encoding="utf-8")
    copies = re.findall(r"^COPY\s+(?!--from)(?:--\S+\s+)*(.+)$", dockerfile, flags=re.MULTILINE)
    assert copies, "expected COPY instructions"
    for line in copies:
        *sources, _dest = line.split()
        for source in sources:
            assert (REPO_ROOT / source).exists(), f"Dockerfile copies missing path {source}"
    # The image must serve the wrapper that enforces AGENT_SECRET.
    assert "foundry_service:app" in dockerfile
