"""SDK version, structured Foundry errors and generated schemas."""

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from ccfoundry_agent_kit import (
    FoundryAPIError,
    FoundryAuthError,
    FoundryHTTPStatusError,
    FoundryNotFoundError,
    FoundryRateLimitError,
    FoundryServerError,
    __version__,
)
from ccfoundry_agent_kit import bootstrap as bootstrap_module
from ccfoundry_agent_kit.bootstrap import FoundryBootstrap, FoundryBootstrapConfig, FoundryInvitePayload
from ccfoundry_agent_kit.errors import raise_for_foundry_status
from ccfoundry_agent_kit.models import AgentManifest
from ccfoundry_agent_kit.sandbox_client import FoundrySandboxClient
from ccfoundry_agent_kit.schemas import SCHEMA_DIALECT, SCHEMA_MODELS, render_schema

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
FOUNDRY = "https://foundry.test"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in ("AGENT_SECRET", "FOUNDRY_AGENT_SOURCE_ID", "FOUNDRY_REGISTERED_AGENT_NAME",
                "FOUNDRY_REGISTRATION_STATUS", "LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def routes(monkeypatch):
    table: dict[tuple[str, str], object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        answer = table.get((request.method, request.url.path))
        if callable(answer):
            return answer(request)
        if answer is None:
            return httpx.Response(599, json={"detail": "unexpected request"})
        status, body = answer
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_with_mock_transport(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(bootstrap_module.httpx, "AsyncClient", client_with_mock_transport)
    return table


def make_bootstrap(tmp_path) -> FoundryBootstrap:
    return FoundryBootstrap(
        manifest=AgentManifest(name="demo_agent", label="Demo Agent"),
        agent_space_dir=tmp_path,
        config=FoundryBootstrapConfig(
            enabled=True,
            foundry_base_url=FOUNDRY,
            public_base_url="https://agent.example.com",
            state_path=str(tmp_path / "state.json"),
            discovery_nonce="nonce-1",
            callback_secret="cb-secret",
        ),
    )


def foundry_response(status, body=None, *, headers=None, method="POST", path="/api/registry/register"):
    return httpx.Response(
        status,
        json={} if body is None else body,
        headers=headers,
        request=httpx.Request(method, f"{FOUNDRY}{path}"),
    )


def test_version_has_a_single_source():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[.+-].+)?", __version__)
    pyproject = (SDK_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_table = pyproject.split("[project]", 1)[1].split("\n[", 1)[0]
    assert 'dynamic = ["version"]' in project_table
    assert not re.search(r"^version\s*=", project_table, re.M), "[project] must not pin a static version"
    assert 'version = { attr = "ccfoundry_agent_kit._version.__version__" }' in pyproject


@pytest.mark.parametrize(
    "status,error_class",
    [
        (400, FoundryHTTPStatusError),
        (401, FoundryAuthError),
        (403, FoundryAuthError),
        (404, FoundryNotFoundError),
        (410, FoundryHTTPStatusError),
        (429, FoundryRateLimitError),
        (500, FoundryServerError),
        (503, FoundryServerError),
    ],
)
def test_status_codes_map_to_error_classes(status, error_class):
    with pytest.raises(FoundryAPIError) as info:
        raise_for_foundry_status(foundry_response(status, {"detail": "invite expired"}))
    error = info.value
    assert type(error) is error_class
    # Existing ``except httpx.HTTPStatusError`` handlers keep working.
    assert isinstance(error, httpx.HTTPStatusError)
    assert error.response.status_code == status
    assert error.request.method == "POST"
    assert (error.method, error.path, error.status_code) == ("POST", "/api/registry/register", status)
    assert error.response_excerpt == "invite expired"
    assert f"HTTP {status}" in str(error)


def test_success_passes_and_excerpt_is_bounded():
    raise_for_foundry_status(foundry_response(200))
    raise_for_foundry_status(foundry_response(201))

    long_body = httpx.Response(502, text="x" * 5000, request=httpx.Request("GET", f"{FOUNDRY}/api/agents"))
    with pytest.raises(FoundryServerError) as info:
        raise_for_foundry_status(long_body)
    assert len(info.value.response_excerpt) == 300
    assert len(info.value.summary()) < 200


def test_rate_limit_exposes_retry_after():
    with pytest.raises(FoundryRateLimitError) as info:
        raise_for_foundry_status(foundry_response(429, {"detail": "slow down"}, headers={"Retry-After": "12"}))
    assert info.value.retry_after == 12.0


def test_envelope_and_registration_carry_sdk_version(tmp_path):
    bootstrap = make_bootstrap(tmp_path)
    envelope = bootstrap.build_foundry_envelope()
    assert envelope["system_manifest"]["sdk_version"] == __version__
    assert envelope["profile_metadata"]["sdk_version"] == __version__
    registration = bootstrap.build_registration_payload(FoundryInvitePayload(invite_code="c", expected_name="demo_agent"))
    assert registration["system_manifest"]["sdk_version"] == __version__


def test_register_failure_raises_structured_error_and_last_error_keeps_status(tmp_path, routes):
    routes[("POST", "/api/registry/register")] = (410, {"detail": "invite expired"})
    bootstrap = make_bootstrap(tmp_path)

    with pytest.raises(FoundryHTTPStatusError) as info:
        asyncio.run(bootstrap.handle_invite(FoundryInvitePayload(invite_code="c", expected_name="demo_agent")))

    assert info.value.status_code == 410
    assert bootstrap.state.last_error == "bootstrap_register_failed: HTTP 410 POST /api/registry/register: invite expired"
    saved = json.loads(bootstrap.state_path.read_text(encoding="utf-8"))
    assert saved["last_error"] == bootstrap.state.last_error


def test_discovery_network_failure_names_the_exception(tmp_path, routes):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    routes[("POST", "/api/registry/discover")] = refuse
    bootstrap = make_bootstrap(tmp_path)

    asyncio.run(bootstrap.heartbeat_once())

    assert bootstrap.state.last_error == "bootstrap_discovery_failed: ConnectError"


def test_config_fetch_auth_failure_is_recorded(tmp_path, routes):
    routes[("POST", "/api/registry/register")] = (200, {"agent_id": "a-1", "status": "APPROVED", "agent_secret": "secret-1"})
    routes[("GET", "/api/agents/demo_agent/config")] = (401, {"detail": "Invalid agent secret"})
    bootstrap = make_bootstrap(tmp_path)

    asyncio.run(bootstrap.handle_invite(FoundryInvitePayload(invite_code="c", expected_name="demo_agent")))

    assert bootstrap.state.last_error == (
        "bootstrap_fetch_config_failed: HTTP 401 GET /api/agents/demo_agent/config: Invalid agent secret"
    )
    assert "secret-1" not in bootstrap.state.last_error


def test_sandbox_client_raises_structured_errors(routes):
    client = FoundrySandboxClient(
        foundry_base_url=FOUNDRY,
        agent_name="demo_agent",
        agent_secret="agent-secret",
        control_plane={"status_url": "/api/agents/demo_agent/sandbox/status"},
    )
    routes[("GET", "/api/agents/demo_agent/sandbox/status")] = (404, {"detail": "sandbox not allocated"})

    with pytest.raises(FoundryNotFoundError) as info:
        asyncio.run(client.status())

    assert info.value.method == "GET"
    assert info.value.response_excerpt == "sandbox not allocated"


@pytest.mark.parametrize("filename", sorted(SCHEMA_MODELS))
def test_checked_in_schemas_match_models(filename):
    path = REPO_ROOT / "schemas" / filename
    assert path.read_text(encoding="utf-8") == render_schema(SCHEMA_MODELS[filename]), (
        f"{path} is stale; run: PYTHONPATH=packages/python-sdk/src python -m ccfoundry_agent_kit.schemas schemas/"
    )


def test_schemas_cover_every_model_field():
    for model in SCHEMA_MODELS.values():
        schema = json.loads(render_schema(model))
        assert schema["$schema"] == SCHEMA_DIALECT
        assert set(schema["properties"]) == set(model.model_fields)
