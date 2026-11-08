import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sys

import pytest

from ccfoundry_agent_kit import FoundryMCPError, FoundryMCPRuntime
from mcp_fixture_server import TOOL, http_peer


def runtime_for(config, name="fixture"):
    async def provider():
        return {name: config}

    return FoundryMCPRuntime(managed_servers=provider)


def network_config(peer, transport="http"):
    return {
        "type": transport,
        "url": peer.url + ("/sse" if transport == "sse" else "/mcp"),
        "protocol_mode": "legacy",
        "method_timeout_seconds": 1,
        "tool_call_timeout_seconds": 1,
    }


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_real_http_transports_list_page_call_and_session_headers(transport):
    with http_peer() as peer:
        peer.pagination = True
        config = network_config(peer, transport)
        config["headers"] = {"Authorization": "Bearer fixture-mcp-secret"}

        async def run():
            async with runtime_for(config).session() as session:
                assert len(session.tools) == 1
                name = session.tools[0]["function"]["name"]
                assert len(name) <= 64
                result = await session.call_tool(name, {"query": "synthetic"})
                assert result["content"][0]["text"] == "fixture search answer"
                assert session.calls == [
                    {"server": "fixture", "tool": "search", "status": "success"}
                ]
            with pytest.raises(FoundryMCPError):
                await session.call_tool(name, {"query": "synthetic"})

        asyncio.run(run())
        assert (
            peer.calls.count("tools/list") == 2 and peer.calls.count("tools/call") == 1
        )
        rpc = [r for r in peer.requests if r[2].get("method") == "tools/call"][0]
        assert rpc[1]["Authorization"] == "Bearer fixture-mcp-secret"
        if transport == "http":
            headers = {k.lower(): v for k, v in rpc[1].items()}
            assert headers["mcp-session-id"] == "fixture-session"
            assert headers["mcp-protocol-version"] == "2025-03-26"


def test_auto_mode_negotiates_legacy_server():
    with http_peer() as peer:
        config = network_config(peer)
        del config["protocol_mode"]

        async def run():
            async with runtime_for(config).session() as session:
                assert session.tools

        asyncio.run(run())
        assert peer.calls[:2] == ["server/discover", "initialize"]


def test_two_sse_requests_have_independent_protocol_sessions():
    with http_peer() as peer:
        runtime = runtime_for(network_config(peer, "sse"))

        async def run():
            for _ in range(2):
                async with runtime.session() as session:
                    result = await session.call_tool(
                        session.tools[0]["function"]["name"], {"query": "x"}
                    )
                    assert result["content"][0]["text"] == "fixture search answer"

        asyncio.run(run())
        assert len(peer.events) == 2 and peer.calls.count("tools/call") == 2


def test_streamable_http_event_stream_responses():
    with http_peer() as peer:
        peer.stream_reply = True

        async def run():
            async with runtime_for(network_config(peer)).session() as session:
                result = await session.call_tool(
                    session.tools[0]["function"]["name"], {"query": "x"}
                )
                assert result["content"][0]["text"] == "fixture search answer"

        asyncio.run(run())


def test_server_without_tools_is_connected_with_empty_catalog():
    with http_peer() as peer:
        peer.has_tools = False

        async def run():
            async with runtime_for(network_config(peer)).session() as session:
                assert session.tools == []

        asyncio.run(run())
        assert "tools/list" not in peer.calls


def test_same_origin_redirect_keeps_actual_protocol_error_layer():
    with http_peer() as peer:
        peer.redirect, peer.rpc_error = peer.url + "/mcp", True
        config = network_config(peer)
        config["url"] = peer.url + "/redirect"

        async def run():
            with pytest.raises(FoundryMCPError) as error:
                async with runtime_for(config).session():
                    pass
            assert error.value.layer == "protocol" and error.value.http_status is None

        asyncio.run(run())


def test_cross_origin_redirect_does_not_forward_credentials():
    with http_peer() as peer, http_peer() as other:
        peer.redirect = other.url + "/mcp"
        config = network_config(peer)
        config["url"] = peer.url + "/redirect"
        config["headers"] = {"Authorization": "Bearer fixture-private"}

        async def run():
            with pytest.raises(FoundryMCPError) as error:
                async with runtime_for(config).session():
                    pass
            assert error.value.layer == "http" and error.value.http_status == 307
            assert "fixture-private" not in str(error.value)

        asyncio.run(run())
        assert not other.requests


@pytest.mark.parametrize("status", [401, 404, 500])
def test_tool_http_error_never_replays(status):
    with http_peer() as peer:
        peer.status, peer.error_method = status, "tools/call"

        async def run():
            with pytest.raises(FoundryMCPError) as error:
                async with runtime_for(network_config(peer)).session() as session:
                    await session.call_tool(
                        session.tools[0]["function"]["name"], {"query": "x"}
                    )
            assert error.value.http_status == status
            assert error.value.calls == [
                {"server": "fixture", "tool": "search", "status": "unknown"}
            ]

        asyncio.run(run())
        assert sum(r[2].get("method") == "tools/call" for r in peer.requests) == 1


@pytest.mark.parametrize(
    "status,layer",
    [(401, "auth"), (403, "auth"), (404, "http"), (405, "http"), (502, "http")],
)
def test_http_errors_preserve_status_without_upstream_secrets(status, layer):
    with http_peer() as peer:
        peer.status = status

        async def run():
            with pytest.raises(FoundryMCPError) as error:
                async with runtime_for(network_config(peer)).session():
                    pytest.fail("bad server was treated as healthy")
            assert error.value.layer == layer and error.value.http_status == status
            assert "private" not in str(error.value) and "127.0.0.1" not in str(
                error.value
            )

        asyncio.run(run())


@pytest.mark.parametrize(
    "kind,layer",
    [
        ("malformed", "protocol"),
        ("rpc", "protocol"),
        ("list", "tools"),
        ("tool", "tools"),
        ("timeout", "timeout"),
    ],
)
def test_protocol_tools_and_timeout_errors(kind, layer):
    with http_peer() as peer:
        config = network_config(peer)
        if kind == "malformed":
            peer.malformed = True
        if kind == "rpc":
            peer.rpc_error = True
        if kind == "list":
            peer.rpc_error, peer.error_method = True, "tools/list"
        if kind == "tool":
            peer.tool_error = True
        if kind == "timeout":
            peer.delay_method = "tools/call"
            config["tool_call_timeout_seconds"] = 0.05

        async def run():
            with pytest.raises(FoundryMCPError) as error:
                async with runtime_for(config).session() as session:
                    await session.call_tool(
                        session.tools[0]["function"]["name"], {"query": "synthetic"}
                    )
            assert error.value.layer == layer, error.value.as_dict()
            if kind in ("timeout", "tool"):
                assert error.value.calls[0]["status"] in ("unknown", "tool_error")

        asyncio.run(run())
        assert peer.calls.count("tools/call") <= 1


def test_connection_refused_is_network_failure():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    async def run():
        with pytest.raises(FoundryMCPError) as error:
            async with runtime_for(
                {
                    "url": f"http://127.0.0.1:{port}/mcp",
                    "protocol_mode": "legacy",
                    "method_timeout_seconds": 0.2,
                }
            ).session():
                pass
        assert error.value.layer == "network"

    asyncio.run(run())


@pytest.mark.parametrize("change", ["remove", "credential", "endpoint"])
def test_revocation_or_changed_config_prevents_call(change):
    with http_peer() as peer:
        config = network_config(peer)
        grants = {"fixture": config}

        async def provider():
            return deepcopy(grants)

        async def run():
            with pytest.raises(FoundryMCPError) as error:
                async with FoundryMCPRuntime(
                    managed_servers=provider
                ).session() as session:
                    if change == "remove":
                        grants.clear()
                    elif change == "credential":
                        grants["fixture"]["headers"] = {"Authorization": "new"}
                    else:
                        grants["fixture"]["url"] += "/changed"
                    await session.call_tool(
                        session.tools[0]["function"]["name"], {"query": "synthetic"}
                    )
            assert error.value.layer == "auth"

        asyncio.run(run())
        assert "tools/call" not in peer.calls


def test_unknown_tools_invalid_arguments_and_remote_schema_do_not_execute():
    with http_peer() as peer:

        async def run():
            async with runtime_for(network_config(peer)).session() as session:
                name = session.tools[0]["function"]["name"]
                for tool, args in [
                    ("invented", {}),
                    (name, {"query": 3}),
                    (name, {"query": "x", "secret": "extra"}),
                ]:
                    with pytest.raises(FoundryMCPError):
                        await session.call_tool(tool, args)

        asyncio.run(run())
        assert "tools/call" not in peer.calls
        peer.tools = [
            dict(TOOL, inputSchema={"$ref": "https://must-not-fetch.invalid/schema"})
        ]

        async def bad_schema():
            with pytest.raises(FoundryMCPError):
                async with runtime_for(network_config(peer)).session():
                    pass

        asyncio.run(bad_schema())


def test_repeated_cursor_is_rejected_and_large_result_is_explicitly_truncated():
    with http_peer() as peer:
        peer.pagination, peer.repeat_cursor = True, True

        async def run():
            with pytest.raises(FoundryMCPError):
                async with runtime_for(network_config(peer)).session():
                    pass
            peer.repeat_cursor = False
            peer.result = "界" * 30_000
            async with runtime_for(network_config(peer)).session() as session:
                result = await session.call_tool(
                    session.tools[0]["function"]["name"], {"query": "x"}
                )
                assert (
                    result["truncated"] is True
                    and len(result["content"][0]["text"]) == 24_000
                )

        asyncio.run(run())


def test_stdio_real_child_minimal_environment_and_reap(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_PARENT_SECRET", "parent-private")
    pidfile = tmp_path / "pid"
    config = {
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("mcp_fixture_server.py"))],
        "env": {"FIXTURE_PID_FILE": str(pidfile), "FIXTURE_EXPLICIT": "yes"},
        "protocol_mode": "legacy",
    }

    async def run():
        async with runtime_for(config).session() as session:
            result = await session.call_tool(
                session.tools[0]["function"]["name"], {"query": "x"}
            )
            assert json.loads(result["content"][0]["text"]) == {
                "secret_inherited": False,
                "explicit": "yes",
            }

    asyncio.run(run())
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_auto_stdio_negotiates_current_protocol_with_official_server():
    config = {
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("mcp_fixture_server.py")), "--modern"],
    }

    async def run():
        async with runtime_for(config).session() as session:
            name = session.tools[0]["function"]["name"]
            result = await session.call_tool(name, {"query": "synthetic"})
            assert result["content"][0]["text"] == "modern fixture: synthetic"
            assert session._tools[name][0].client.protocol_version == "2026-07-28"

    asyncio.run(run())


def test_stdio_cancellation_reaps_child(tmp_path):
    pidfile = tmp_path / "pid"
    config = {
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("mcp_fixture_server.py"))],
        "env": {"FIXTURE_PID_FILE": str(pidfile), "FIXTURE_HANG_CALL": "1"},
        "protocol_mode": "legacy",
    }

    async def task():
        async with runtime_for(config).session() as session:
            await session.call_tool(
                session.tools[0]["function"]["name"], {"query": "x"}
            )

    async def run():
        running = asyncio.create_task(task())
        while not pidfile.exists():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(run())
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_owned_and_managed_names_are_distinct_and_owned_changes_revoke(tmp_path):
    with http_peer() as peer:
        config = network_config(peer)
        path = tmp_path / "mcp.yaml"
        path.write_text(json.dumps({"servers": {"fixture": config}}))
        runtime = runtime_for(config)
        runtime.owned_file = path

        async def run():
            async with runtime.session() as session:
                names = [t["function"]["name"] for t in session.tools]
                assert len(set(names)) == 2
                owned = next(
                    name
                    for name in names
                    if session._tools[name][0].key.startswith("owned:")
                )
                path.write_text('{"servers":{}}')
                with pytest.raises(FoundryMCPError):
                    await session.call_tool(owned, {"query": "x"})

        asyncio.run(run())


@pytest.mark.parametrize(
    "config",
    [
        {"url": "file:///etc/passwd"},
        {"url": "https://user:password@example.test/mcp"},
        {"command": "x", "args": "shell args"},
        {"url": "http://fixture", "method_timeout_seconds": float("nan")},
        {"url": "http://fixture", "type": "unknown"},
        {"url": "http://fixture", "headers": {"Authorization": "x\r\nInjected: y"}},
    ],
)
def test_bad_configuration_is_rejected_before_io(config):
    async def run():
        with pytest.raises(FoundryMCPError) as error:
            async with runtime_for(config).session():
                pass
        assert error.value.layer == "config"

    asyncio.run(run())
