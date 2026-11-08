"""The same request/resource fixture is checked by the actual Go sync handler."""

import asyncio
import json
from pathlib import Path

import httpx

from ccfoundry_agent_kit import (
    AgentManifest,
    FoundryApprovalPayload,
    FoundryBootstrap,
    FoundryBootstrapConfig,
)
from ccfoundry_agent_kit import bootstrap as bootstrap_module


def test_current_go_resource_contract(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    contract = json.loads((root / "tests/contracts/foundry_mcp.json").read_text())
    replies = [
        {"ok": True, "allocated_resources": contract["resources"]},
        {"ok": True, "allocated_resources": contract["resources"]},
        {"ok": True, "allocated_resources": contract["resources_after_removal"]},
        contract["retired_response"],
    ]
    requests = []

    def handle(request):
        assert (
            request.method == "POST"
            and request.url.path
            == "/api/agents/" + contract["agent_name"] + "/registry/sync"
        )
        assert request.headers["Authorization"] == "Bearer " + contract["agent_secret"]
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(requests) - 1])

    real = httpx.AsyncClient
    monkeypatch.setattr(
        bootstrap_module.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handle), **kw),
    )
    b = FoundryBootstrap(
        manifest=AgentManifest(name=contract["agent_name"], label="Fixture"),
        agent_space_dir=tmp_path,
        config=FoundryBootstrapConfig(
            enabled=True,
            foundry_base_url="https://fixture.invalid",
            state_path=str(tmp_path / "state.json"),
        ),
    )

    async def run():
        await b.handle_approval(
            FoundryApprovalPayload(
                agent_name=contract["agent_name"],
                agent_secret=contract["agent_secret"],
                allocated_resources=contract["resources"],
            )
        )
        assert await b.mcp_servers() == contract["resources"]["mcp_servers"]
        await b.acknowledge_mcp_catalog(list(contract["resources"]["mcp_servers"]))
        assert await b.mcp_servers() == {}
        assert b.state.allocated_resources == contract["resources_after_removal"]
        assert await b.mcp_servers() == {} and b.state.registration_status == "RETIRED"

    asyncio.run(run())
    assert requests[0] == contract["refresh_request"]
    assert requests[1:3] == [contract["ack_request"], contract["ack_request"]]
