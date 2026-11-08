import asyncio
import importlib
import json

import httpx
import pytest

from ccfoundry_agent_kit import (
    AgentManifest,
    AgentSpace,
    ChatRequest,
    FoundryApprovalPayload,
    FoundryBootstrap,
    FoundryBootstrapConfig,
    FoundryMCPError,
)
from ccfoundry_agent_kit import bootstrap as bootstrap_module
from mcp_fixture_server import http_peer


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "FOUNDRY_REGISTRATION_STATUS",
        "FOUNDRY_ALLOCATED_RESOURCES_JSON",
        "FOUNDRY_REGISTERED_AGENT_NAME",
        "AGENT_SECRET",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "AGENT_DEPLOY_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


def bootstrap(tmp_path, url):
    return FoundryBootstrap(
        manifest=AgentManifest(name="fixture-agent", label="Fixture"),
        agent_space_dir=tmp_path,
        config=FoundryBootstrapConfig(
            enabled=True,
            foundry_base_url=url,
            state_path=str(tmp_path / "bootstrap.json"),
        ),
    )


def test_refresh_and_ack_use_complete_snapshot_not_yaml_or_recursive_merge(tmp_path):
    with http_peer() as peer:
        peer.grants = {
            "web-search": {"type": "sse", "url": peer.url + "/sse"},
            "removed": {"command": "old"},
        }
        b = bootstrap(tmp_path, peer.url)

        async def run():
            await b.handle_approval(
                FoundryApprovalPayload(
                    agent_name="fixture-agent",
                    agent_secret="fixture-agent-secret",
                    allocated_resources={
                        "mcp_servers": peer.grants,
                        "old_resource": {"x": 1},
                    },
                )
            )
            peer.grants.pop("removed")
            current = await b.mcp_servers()
            assert list(current) == ["web-search"]
            assert "old_resource" not in b.state.allocated_resources
            await b.acknowledge_mcp_catalog(["web-search"])
            assert peer.requests[-1][2] == {
                "capabilities": {"mcp_services": ["web-search"]}
            }
            peer.grants = {}
            assert await b.mcp_servers() == {}
            assert b.state.allocated_resources == {"mcp_servers": {}}
            peer.retired = True
            assert await b.mcp_servers() == {}
            assert (
                b.state.registration_status == "RETIRED"
                and b.state.allocated_resources == {}
            )
            count = len(peer.requests)
            assert await b.mcp_servers() == {} and len(peer.requests) == count

        asyncio.run(run())
        saved = json.loads(b.state_path.read_text())
        assert (
            saved["registration_status"] == "RETIRED"
            and saved["allocated_resources"] == {}
        )
        assert all("files" not in r[2] for r in peer.requests)


def test_sync_files_replaces_explicit_empty_resources(tmp_path, monkeypatch):
    real = httpx.AsyncClient

    def handler(request):
        assert json.loads(request.content) == {"files": {"SOUL.md": "fixture"}}
        return httpx.Response(200, json={"ok": True, "allocated_resources": {}})

    monkeypatch.setattr(
        bootstrap_module.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    b = bootstrap(tmp_path, "http://fixture.invalid")

    async def run():
        await b.handle_approval(
            FoundryApprovalPayload(
                agent_name="fixture-agent",
                agent_secret="fixture",
                allocated_resources={"mcp_servers": {"old": {"command": "old"}}},
            )
        )
        await b.sync_registry_files({"SOUL.md": "fixture"})
        assert b.state.allocated_resources == {}

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,body,layer",
    [
        (401, {"detail": "secret"}, "auth"),
        (404, {}, "http"),
        (302, {}, "http"),
        (200, {"ok": True}, "protocol"),
        (200, {"ok": True, "allocated_resources": {"mcp_servers": []}}, "protocol"),
    ],
)
def test_refresh_failure_clears_cached_tools_and_redacts(
    tmp_path, monkeypatch, status, body, layer
):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        bootstrap_module.httpx,
        "AsyncClient",
        lambda **kw: real(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)),
            **kw,
        ),
    )
    b = bootstrap(tmp_path, "http://fixture.invalid")

    async def run():
        await b.handle_approval(
            FoundryApprovalPayload(
                agent_name="fixture-agent",
                agent_secret="fixture",
                allocated_resources={"mcp_servers": {"old": {"command": "old"}}},
            )
        )
        with pytest.raises(FoundryMCPError) as error:
            await b.mcp_servers()
        assert error.value.layer == layer and "secret" not in str(error.value)
        assert "mcp_servers" not in b.state.allocated_resources

    asyncio.run(run())


def test_not_approved_or_manifest_opt_out_never_loads_assigned_tools(tmp_path):
    b = bootstrap(tmp_path, "http://must-not-contact.invalid")
    b.state.allocated_resources = {"mcp_servers": {"old": {"command": "old"}}}
    assert asyncio.run(b.mcp_servers()) == {}
    b.state.registration_status = "APPROVED"
    b.manifest.manifest.mcp.accepts_foundry_mcp = False
    assert asyncio.run(b.mcp_servers()) == {}


@pytest.fixture
def example(tmp_path, monkeypatch):
    monkeypatch.setenv("ME_AGENT_BASE_DIR", str(tmp_path))
    import me_agent_example.app as module

    module = importlib.reload(module)
    monkeypatch.setattr(module, "CONFIG", {"max_tokens": 80})
    monkeypatch.setattr(module.MANIFEST.manifest.mcp, "has_own_mcp", False)
    yield module


def setup_example(module, peer, tmp_path, monkeypatch):
    peer.grants = {
        "web-search": {
            "type": "sse",
            "url": peer.url + "/sse",
            "protocol_mode": "legacy",
            "method_timeout_seconds": 1,
        }
    }
    b = bootstrap(tmp_path, peer.url)
    asyncio.run(
        b.handle_approval(
            FoundryApprovalPayload(
                agent_name="fixture-agent",
                agent_secret="fixture-agent-secret",
                allocated_resources={"mcp_servers": peer.grants},
            )
        )
    )
    monkeypatch.setattr(module, "FOUNDRY_BOOTSTRAP", b)
    monkeypatch.setattr(
        module,
        "_llm_runtime_config",
        lambda _: {
            "api_key": "fixture-model-secret",
            "base_url": peer.url + "/v1",
            "model": "fixture",
            "model_source": "fixture",
        },
    )
    return b


def test_example_actual_http_model_tool_loop_with_foundry_grants(
    example, tmp_path, monkeypatch
):
    with http_peer() as peer:
        setup_example(example, peer, tmp_path, monkeypatch)
        result = asyncio.run(
            example.handle_chat(
                ChatRequest(message="Search the synthetic source"), AgentSpace(tmp_path)
            )
        )
        assert (
            result.status == "ok"
            and result.reply == "The synthetic source confirms the answer."
        )
        assert result.metadata["mcp_calls"] == [
            {"server": "web-search", "tool": "search", "status": "success"}
        ]
        assert len(peer.model_requests) == 2 and peer.calls.count("tools/call") == 1
        messages = peer.model_requests[-1]["messages"]
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == messages[-2]["tool_calls"][0]["id"]
        assert "fixture-agent-secret" not in json.dumps(peer.model_requests)
        syncs = [r[2] for r in peer.requests if "registry/sync" in r[0]]
        assert syncs == [
            {"capabilities": {"mcp_services": []}},
            {"capabilities": {"mcp_services": ["web-search"]}},
            {"capabilities": {"mcp_services": ["web-search"]}},
        ]


@pytest.mark.parametrize(
    "mode,expected_calls",
    [("invalid", 0), ("unknown", 0), ("duplicate", 1), ("forever", 3)],
)
def test_example_rejects_bad_calls_and_bounds_loop(
    example, tmp_path, monkeypatch, mode, expected_calls
):
    with http_peer() as peer:
        setup_example(example, peer, tmp_path, monkeypatch)
        peer.model_mode = mode
        result = asyncio.run(
            example.handle_chat(
                ChatRequest(message="Search the synthetic source"), AgentSpace(tmp_path)
            )
        )
        assert result.status == "error" and "mcp_error" in result.metadata
        assert peer.calls.count("tools/call") == expected_calls
        assert "local example mode" not in result.reply
        if mode == "forever":
            assert (
                len(peer.model_requests) == 4
                and peer.model_requests[-1]["tool_choice"] == "none"
            )


def test_example_stops_after_revocation_between_model_rounds(
    example, tmp_path, monkeypatch
):
    with http_peer() as peer:
        setup_example(example, peer, tmp_path, monkeypatch)
        peer.model_mode = "forever"
        peer.after_call = lambda: peer.grants.clear()
        result = asyncio.run(
            example.handle_chat(
                ChatRequest(message="Search twice"), AgentSpace(tmp_path)
            )
        )
        assert (
            result.status == "error" and result.metadata["mcp_error"]["layer"] == "auth"
        )
        assert peer.calls.count("tools/call") == 1


def test_example_tool_failure_has_no_fabricated_answer(example, tmp_path, monkeypatch):
    with http_peer() as peer:
        setup_example(example, peer, tmp_path, monkeypatch)
        peer.tool_error = True
        result = asyncio.run(
            example.handle_chat(ChatRequest(message="Search"), AgentSpace(tmp_path))
        )
        assert (
            result.status == "error"
            and result.metadata["mcp_error"]["layer"] == "tools"
        )
        assert result.metadata["mcp_error"]["calls"][0]["status"] == "tool_error"
        assert len(peer.model_requests) == 1


def test_model_failure_after_tool_preserves_receipt_without_replay(
    example, tmp_path, monkeypatch
):
    with http_peer() as peer:
        setup_example(example, peer, tmp_path, monkeypatch)
        peer.model_mode = "model-error"
        result = asyncio.run(
            example.handle_chat(ChatRequest(message="Search"), AgentSpace(tmp_path))
        )
        assert (
            result.status == "error"
            and result.metadata["llm_error"] == "model_request_failed"
        )
        assert result.metadata["mcp_calls"][0]["status"] == "success"
        assert peer.calls.count("tools/call") == 1 and len(peer.model_requests) == 2
        assert "private" not in result.model_dump_json()
