"""Contract tests for FoundryBootstrap against mocked Foundry endpoints.

Every HTTP call made by the bootstrap goes through httpx.MockTransport (injected
by wrapping httpx.AsyncClient), so request paths, headers and bodies are checked
against the shapes Foundry serves, and state persistence is checked on disk.
"""

import asyncio
import json
import stat

import httpx
import pytest

from ccfoundry_agent_kit import bootstrap as bootstrap_module
from ccfoundry_agent_kit.bootstrap import (
    FoundryApprovalPayload,
    FoundryBootstrap,
    FoundryBootstrapConfig,
    FoundryInvitePayload,
)
from ccfoundry_agent_kit.models import AgentManifest

FOUNDRY = "https://foundry.test"
PUBLIC = "https://agent.example.com"
DISCOVER = ("POST", "/api/registry/discover")
HEARTBEAT = ("POST", "/api/registry/discover/heartbeat")
ACTIONS = ("POST", "/api/registry/discover/actions")
REGISTER = ("POST", "/api/registry/register")
CONFIG = ("GET", "/api/agents/demo_agent/config")

SEEDED_ENV_KEYS = (
    "FOUNDRY_AGENT_SOURCE_ID",
    "FOUNDRY_REGISTERED_AGENT_NAME",
    "FOUNDRY_REGISTRATION_STATUS",
    "FOUNDRY_APPROVED_AT",
    "FOUNDRY_ALLOCATED_RESOURCES_JSON",
    "AGENT_SECRET",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_API_BASE",
    "LLM_ALLOWED_MODELS_JSON",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "FOUNDRY_ALLOWED_MODELS_JSON",
    "AGENT_DEPLOY_MODE",
)


class FakeFoundry:
    """Answers by (method, path) and records every request."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], tuple[int, object]] = {}
        self.requests: list[tuple[str, str, dict[str, str], object]] = []

    def on(self, route: tuple[str, str], *, status: int = 200, body: object = None) -> None:
        self.routes[route] = (status, {} if body is None else body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        route = (request.method, request.url.path)
        self.requests.append((request.method, request.url.path, dict(request.headers), body))
        if route not in self.routes:
            return httpx.Response(599, json={"detail": f"unexpected request {route}"})
        status, payload = self.routes[route]
        return httpx.Response(status, json=payload)

    def paths(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _, _ in self.requests]

    def bodies(self, route: tuple[str, str]) -> list[object]:
        return [body for method, path, _, body in self.requests if (method, path) == route]

    def headers(self, route: tuple[str, str]) -> list[dict[str, str]]:
        return [headers for method, path, headers, _ in self.requests if (method, path) == route]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in SEEDED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def foundry(monkeypatch):
    fake = FakeFoundry()
    transport = httpx.MockTransport(fake.handler)
    real_client = httpx.AsyncClient

    def client_with_mock_transport(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(bootstrap_module.httpx, "AsyncClient", client_with_mock_transport)
    return fake


def make_manifest() -> AgentManifest:
    return AgentManifest(name="demo_agent", label="Demo Agent", capabilities=["chat", "summary"])


def make_bootstrap(tmp_path, **overrides) -> FoundryBootstrap:
    values = {
        "enabled": True,
        "foundry_base_url": FOUNDRY,
        "public_base_url": PUBLIC,
        "state_path": str(tmp_path / "state" / "bootstrap.json"),
        "discovery_nonce": "nonce-1",
        "callback_secret": "cb-secret",
    }
    values.update(overrides)
    return FoundryBootstrap(
        manifest=make_manifest(),
        agent_space_dir=tmp_path,
        config=FoundryBootstrapConfig(**values),
    )


def saved_state(bootstrap: FoundryBootstrap) -> dict:
    return json.loads(bootstrap.state_path.read_text(encoding="utf-8"))


def invite(**overrides) -> FoundryInvitePayload:
    values = {"invite_id": "inv-1", "invite_code": "code-1", "expected_name": "demo_agent", "discovery_nonce": "nonce-1"}
    values.update(overrides)
    return FoundryInvitePayload(**values)


def test_disabled_bootstrap_makes_no_requests(tmp_path, foundry):
    bootstrap = make_bootstrap(tmp_path, enabled=False)
    assert asyncio.run(bootstrap.heartbeat_once()) == {"status": "disabled"}
    assert foundry.requests == []


def test_push_delivery_discover_invite_register_and_fetch_config(tmp_path, foundry):
    foundry.on(DISCOVER, body={
        "discovery_id": "disc-1",
        "status": "INVITED",
        "pending_invite": {"invite_id": "inv-1", "invite_code": "code-1", "expected_name": "demo_agent", "discovery_nonce": "nonce-1"},
    })
    foundry.on(REGISTER, body={"agent_id": "agent-1", "status": "APPROVED", "agent_secret": "secret-xyz", "owner_pseudonym": "owner-7"})
    foundry.on(CONFIG, body={"LLM_API_BASE": "https://llm.test/v1", "LLM_API_KEY": "sk-test"})
    bootstrap = make_bootstrap(tmp_path)

    result = asyncio.run(bootstrap.heartbeat_once())

    assert result["registration_status"] == "APPROVED"
    assert foundry.paths() == [DISCOVER, REGISTER, CONFIG]
    discover = foundry.bodies(DISCOVER)[0]
    assert discover["agent_card"]["name"] == "demo_agent"
    assert discover["agent_card"]["url"] == PUBLIC
    assert discover["x_foundry"]["discovery_nonce"] == "nonce-1"
    assert discover["x_foundry"]["governance"]["bootstrap_callback_secret"] == "cb-secret"
    assert discover["x_foundry"]["governance"]["bootstrap_delivery"] == "push"
    assert discover["x_foundry"]["system_manifest"]["sdk"] == "ccfoundry-agent-kit"
    register = foundry.bodies(REGISTER)[0]
    assert register["invite_code"] == "code-1"
    assert register["expected_name"] == "demo_agent"
    assert register["url"] == PUBLIC
    assert register["discovery_nonce"] == "nonce-1"
    assert foundry.headers(CONFIG)[0]["authorization"] == "Bearer secret-xyz"

    state = saved_state(bootstrap)
    assert state["discovery_id"] == "disc-1"
    assert state["registration_agent_id"] == "agent-1"
    assert state["registration_status"] == "APPROVED"
    assert state["registered_agent_name"] == "demo_agent"
    assert state["invite_status"] == "REDEEMED"
    assert state["discovery_status"] == "REGISTERED"
    assert state["owner_pseudonym"] == "owner-7"
    assert state["env_vars"]["AGENT_SECRET"] == "secret-xyz"
    assert state["env_vars"]["LLM_API_KEY"] == "sk-test"
    assert state["env_vars"]["_bootstrap_callback_secret"] == "cb-secret"
    assert state["has_agent_secret"] is True
    assert state["approved_at"]
    assert state["last_error"] is None
    assert stat.S_IMODE(bootstrap.state_path.stat().st_mode) == 0o600

    public = bootstrap.public_state()
    assert public["approval_mode"] == "callback"
    assert public["has_llm_api_key"] is True
    serialized = json.dumps(public)
    assert "secret-xyz" not in serialized and "cb-secret" not in serialized and "sk-test" not in serialized


def test_pending_registration_skips_config_and_repeat_invite_is_idempotent(tmp_path, foundry):
    foundry.on(REGISTER, body={"agent_id": "agent-2", "status": "PENDING"})
    bootstrap = make_bootstrap(tmp_path)

    first = asyncio.run(bootstrap.handle_invite(invite()))
    second = asyncio.run(bootstrap.handle_invite(invite()))

    assert first == {"ok": True, "status": "PENDING", "agent_id": "agent-2", "agent_name": "demo_agent"}
    assert second["message"] == "Invite already handled for this agent"
    assert foundry.paths() == [REGISTER]
    state = saved_state(bootstrap)
    assert state["registration_status"] == "PENDING"
    assert state["has_agent_secret"] is False
    assert bootstrap.public_state()["approval_mode"] == "pending"


def test_poll_delivery_heartbeat_then_actions_approval(tmp_path, foundry):
    foundry.on(DISCOVER, body={"discovery_id": "disc-9", "status": "DISCOVERED"})
    foundry.on(ACTIONS, body={"bootstrap_actions": []})
    bootstrap = make_bootstrap(tmp_path, bootstrap_delivery="poll")

    asyncio.run(bootstrap.heartbeat_once())

    assert foundry.paths() == [DISCOVER, ACTIONS]
    assert foundry.bodies(ACTIONS)[0] == {"discovery_id": "disc-9", "discovery_nonce": "nonce-1", "url": PUBLIC}
    assert saved_state(bootstrap)["last_polled_at"]

    foundry.on(HEARTBEAT, body={"status": "INVITED"})
    foundry.on(ACTIONS, body={"bootstrap_actions": {"kind": "bootstrap_approved", "payload": {
        "agent_name": "demo_agent",
        "agent_secret": "poll-secret",
        "owner_pseudonym": "owner-9",
        "env_vars": {"LLM_MODEL": "kimi-k2"},
        "allocated_resources": {"sandbox": {"pool": "green"}},
    }}})

    asyncio.run(bootstrap.heartbeat_once())

    assert foundry.paths()[-2:] == [HEARTBEAT, ACTIONS]
    assert foundry.bodies(HEARTBEAT)[0] == {"discovery_id": "disc-9", "discovery_nonce": "nonce-1", "url": PUBLIC}
    state = saved_state(bootstrap)
    assert state["registration_status"] == "APPROVED"
    assert state["env_vars"]["AGENT_SECRET"] == "poll-secret"
    assert state["env_vars"]["LLM_MODEL"] == "kimi-k2"
    assert state["allocated_resources"] == {"sandbox": {"pool": "green"}}
    assert state["owner_pseudonym"] == "owner-9"
    assert state["last_error"] is None


def test_actions_endpoint_404_is_not_an_error(tmp_path, foundry):
    foundry.on(DISCOVER, body={"discovery_id": "disc-3", "status": "DISCOVERED"})
    foundry.on(ACTIONS, status=404, body={"detail": "Not Found"})
    bootstrap = make_bootstrap(tmp_path, bootstrap_delivery="hybrid")

    asyncio.run(bootstrap.heartbeat_once())

    assert foundry.paths() == [DISCOVER, ACTIONS]
    assert saved_state(bootstrap)["last_error"] is None


def test_heartbeat_404_rediscovers(tmp_path, foundry):
    bootstrap = make_bootstrap(tmp_path)
    bootstrap.state.discovery_id = "stale-discovery"
    foundry.on(HEARTBEAT, status=404, body={"detail": "Not Found"})
    foundry.on(DISCOVER, body={"discovery_id": "fresh-discovery", "status": "DISCOVERED"})

    asyncio.run(bootstrap.heartbeat_once())

    assert foundry.paths() == [HEARTBEAT, DISCOVER]
    assert saved_state(bootstrap)["discovery_id"] == "fresh-discovery"


def test_invite_nonce_mismatch_is_rejected_without_state_change(tmp_path, foundry):
    bootstrap = make_bootstrap(tmp_path)

    with pytest.raises(ValueError, match="discovery_nonce mismatch"):
        asyncio.run(bootstrap.handle_invite(invite(discovery_nonce="someone-else")))

    state = saved_state(bootstrap)
    assert state["invite_status"] is None
    assert state["registration_status"] is None
    assert foundry.requests == []


def test_callback_token_verification_and_secret_persistence(tmp_path):
    bootstrap = make_bootstrap(tmp_path)
    bootstrap.verify_callback_token("cb-secret")
    for bad in (None, "", "wrong"):
        with pytest.raises(PermissionError):
            bootstrap.verify_callback_token(bad)

    reloaded = FoundryBootstrap(
        manifest=make_manifest(),
        agent_space_dir=tmp_path,
        config=FoundryBootstrapConfig(enabled=True, foundry_base_url=FOUNDRY, public_base_url=PUBLIC,
                                      state_path=str(bootstrap.state_path)),
    )
    assert reloaded.callback_secret == "cb-secret"
    assert reloaded.state.discovery_nonce == "nonce-1"


def test_push_approval_callback_merges_env_and_keeps_existing_secret(tmp_path):
    bootstrap = make_bootstrap(tmp_path)
    bootstrap.state.env_vars["LLM_MODEL"] = "old-model"

    result = asyncio.run(bootstrap.handle_approval(FoundryApprovalPayload(
        agent_name="demo_agent",
        agent_secret="callback-secret-1",
        owner_pseudonym="owner-3",
        env_vars={"LLM_MODEL": "new-model", "LLM_API_BASE": "https://llm.test"},
        allocated_resources={"cpu": 2},
    )))

    assert result == {"ok": True, "status": "APPROVED", "agent_name": "demo_agent", "has_agent_secret": True}
    state = saved_state(bootstrap)
    assert state["env_vars"]["AGENT_SECRET"] == "callback-secret-1"
    assert state["env_vars"]["LLM_MODEL"] == "new-model"
    assert state["env_vars"]["LLM_API_BASE"] == "https://llm.test"
    assert state["env_vars"]["_bootstrap_callback_secret"] == "cb-secret"
    assert state["allocated_resources"] == {"cpu": 2}
    assert state["registration_status"] == "APPROVED"
    assert state["invite_status"] == "REDEEMED"
    assert state["discovery_status"] == "REGISTERED"
    assert stat.S_IMODE(bootstrap.state_path.stat().st_mode) == 0o600

    # A top-level agent_secret never replaces an AGENT_SECRET that is already stored.
    asyncio.run(bootstrap.handle_approval(FoundryApprovalPayload(agent_name="demo_agent", agent_secret="other-secret")))
    assert saved_state(bootstrap)["env_vars"]["AGENT_SECRET"] == "callback-secret-1"


def test_action_shape_aliases(tmp_path):
    bootstrap = make_bootstrap(tmp_path)

    actions = bootstrap._extract_bootstrap_actions({
        "bootstrap_actions": [{"type": "invite", "payload": {"invite_code": "a", "expected_name": "x"}}, "not-a-dict"],
        "bootstrap_action": {"kind": "settlement", "task_ref": "t-1"},
        "pending_invite": {"invite_code": "b", "expected_name": "y"},
        "approval_bundle": {"agent_name": "y"},
    })
    assert [action.get("type") or action.get("kind") for action in actions] == ["invite", "settlement", "invite", "approved"]
    assert actions[2]["payload"] == {"invite_code": "b", "expected_name": "y"}
    assert actions[3]["payload"] == {"agent_name": "y"}
    assert bootstrap._extract_bootstrap_actions({"bootstrap_actions": {"type": "approved", "payload": {}}}) == [
        {"type": "approved", "payload": {}}
    ]
    assert bootstrap._extract_bootstrap_actions(None) == []
    assert bootstrap._extract_bootstrap_actions({"unrelated": 1}) == []


def test_apply_actions_dispatches_type_aliases_and_flat_payloads(tmp_path):
    bootstrap = make_bootstrap(tmp_path)
    calls = []

    async def fake_invite(payload):
        calls.append(("invite", payload.invite_code))

    async def fake_approval(payload):
        calls.append(("approval", payload.agent_name))

    async def fake_settlement(payload):
        calls.append(("settlement", payload.get("task_ref")))

    bootstrap.handle_invite = fake_invite
    bootstrap.handle_approval = fake_approval
    bootstrap._handle_settlement = fake_settlement

    asyncio.run(bootstrap._apply_bootstrap_actions({"bootstrap_actions": [
        {"type": "bootstrap_invite", "invite_code": "flat-code", "expected_name": "x"},
        {"kind": "approval", "payload": {"agent_name": "x"}},
        {"type": "task_settled", "task_ref": "t-2"},
        {"type": "something_new", "payload": {}},
    ]}))

    assert calls == [("invite", "flat-code"), ("approval", "x"), ("settlement", "t-2")]


def test_discovery_failure_records_last_error(tmp_path, foundry):
    foundry.on(DISCOVER, status=500, body={"detail": "boom"})
    bootstrap = make_bootstrap(tmp_path)

    asyncio.run(bootstrap.heartbeat_once())

    assert saved_state(bootstrap)["last_error"].startswith("bootstrap_discovery_failed")


def test_poll_failure_records_last_error(tmp_path, foundry):
    foundry.on(DISCOVER, body={"discovery_id": "disc-5", "status": "DISCOVERED"})
    foundry.on(ACTIONS, status=502, body={"detail": "bad gateway"})
    bootstrap = make_bootstrap(tmp_path, bootstrap_delivery="poll")

    asyncio.run(bootstrap.heartbeat_once())

    state = saved_state(bootstrap)
    assert state["discovery_id"] == "disc-5"
    assert state["last_error"].startswith("bootstrap_poll_failed")


def test_register_failure_records_last_error_and_raises(tmp_path, foundry):
    foundry.on(REGISTER, status=400, body={"detail": "invite expired"})
    bootstrap = make_bootstrap(tmp_path)

    with pytest.raises(Exception):
        asyncio.run(bootstrap.handle_invite(invite()))

    state = saved_state(bootstrap)
    assert state["last_error"].startswith("bootstrap_register_failed")
    assert state["invite_status"] == "ISSUED"
    assert state["registration_status"] is None


def test_config_fetch_failure_keeps_approval_and_records_last_error(tmp_path, foundry):
    foundry.on(REGISTER, body={"agent_id": "agent-4", "status": "APPROVED", "agent_secret": "secret-4"})
    foundry.on(CONFIG, status=403, body={"detail": "invalid agent secret"})
    bootstrap = make_bootstrap(tmp_path)

    result = asyncio.run(bootstrap.handle_invite(invite()))

    assert result["status"] == "APPROVED"
    state = saved_state(bootstrap)
    assert state["registration_status"] == "APPROVED"
    assert state["env_vars"]["AGENT_SECRET"] == "secret-4"
    assert state["last_error"].startswith("bootstrap_fetch_config_failed")


def test_environment_seed_restores_approved_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_REGISTERED_AGENT_NAME", "demo_agent")
    monkeypatch.setenv("FOUNDRY_REGISTRATION_STATUS", "approved")
    monkeypatch.setenv("AGENT_SECRET", "env-secret")
    monkeypatch.setenv("FOUNDRY_ALLOCATED_RESOURCES_JSON", '{"gpu": 1}')

    bootstrap = make_bootstrap(tmp_path)

    state = saved_state(bootstrap)
    assert state["registration_status"] == "APPROVED"
    assert state["registered_agent_name"] == "demo_agent"
    assert state["discovery_status"] == "REGISTERED"
    assert state["env_vars"]["AGENT_SECRET"] == "env-secret"
    assert state["has_agent_secret"] is True
    assert state["allocated_resources"] == {"gpu": 1}
    assert bootstrap.public_state()["approval_mode"] == "callback"


def test_corrupt_state_file_starts_fresh(tmp_path):
    state_path = tmp_path / "state" / "bootstrap.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not json", encoding="utf-8")

    bootstrap = make_bootstrap(tmp_path)

    state = saved_state(bootstrap)
    assert state["registration_status"] is None
    assert state["discovery_nonce"] == "nonce-1"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
