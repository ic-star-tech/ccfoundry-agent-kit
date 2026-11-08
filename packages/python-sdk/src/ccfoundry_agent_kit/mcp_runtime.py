"""Bounded, request-scoped MCP clients for approved and agent-owned tools.

Transport/protocol negotiation is delegated to the official MCP SDK. A session
must be entered, used and closed in the same async task. No tool call is retried.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import anyio
import httpx2
from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
import yaml

ServerProvider = Callable[[], Awaitable[dict[str, Any]]]
CatalogAck = Callable[[list[str]], Awaitable[None]]
MAX_SERVERS = 16
MAX_TOOLS = 128
MAX_PAGES = 16
MAX_CONFIG_BYTES = 256_000
MAX_SCHEMA_BYTES = 32_000
MAX_ARGUMENT_BYTES = 32_000
MAX_RESULT_CHARS = 24_000
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


class FoundryMCPError(RuntimeError):
    """A safe, layered failure; never includes URL, headers or upstream text."""

    def __init__(
        self,
        layer: str,
        operation: str,
        *,
        server: str = "",
        http_status: int | None = None,
    ):
        self.layer = layer
        self.operation = operation
        self.server = server
        self.http_status = http_status
        self.calls: list[dict] = []
        super().__init__(
            f"MCP {operation} failed ({layer})" + (f" for {server}" if server else "")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "operation": self.operation,
            "server": self.server,
            "http_status": self.http_status,
            "calls": deepcopy(self.calls),
        }


def _leaves(exc: BaseException):
    yield exc
    for child in getattr(exc, "exceptions", ()):
        yield from _leaves(child)
    if exc.__cause__ is not None:
        yield from _leaves(exc.__cause__)


def _failure(
    exc: Exception, operation: str, server: str = "", status: int | None = None
) -> FoundryMCPError:
    errors = list(_leaves(exc))
    for error in errors:
        if isinstance(error, FoundryMCPError):
            return error
        if isinstance(error, httpx2.HTTPStatusError):
            status = error.response.status_code
    if status is not None:
        layer = "auth" if status in (401, 403) else "http"
    elif any(
        isinstance(e, (TimeoutError, httpx2.TimeoutException))
        or isinstance(e, MCPError)
        and e.code == -32001
        for e in errors
    ):
        layer = "timeout"
    elif any(isinstance(e, (httpx2.NetworkError, OSError)) for e in errors):
        layer = "network"
    else:
        layer = "tools" if operation in ("list", "call") else "protocol"
    return FoundryMCPError(layer, operation, server=server, http_status=status)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _timeout(config: dict, keys: tuple[str, ...], default: float) -> float:
    for key in keys:
        if key in config:
            value = config[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 < value <= 600
            ):
                raise FoundryMCPError("config", "configure")
            return float(value)
    return default


def _strings(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or "\x00" in k + v
        for k, v in value.items()
    ):
        raise FoundryMCPError("config", "configure")
    return dict(value)


@dataclass
class _Server:
    key: str
    name: str
    config: dict = field(repr=False)
    transport: str
    method_timeout: float
    call_timeout: float
    headers: dict = field(repr=False)
    env: dict = field(repr=False)
    client: Client | None = field(default=None, repr=False)
    http_error: int | None = None

    async def observe(self, response):
        # Streamable HTTP may legitimately reject the optional GET stream.
        if response.request.method != "DELETE" and response.status_code >= 300:
            if not (
                self.transport == "streamable-http"
                and response.request.method == "GET"
                and response.status_code == 405
            ):
                self.http_error = response.status_code
        elif response.request.method == "POST":
            # A successful same-origin redirect has already resolved the 3xx.
            self.http_error = None


def _server(key: str, value: Any) -> _Server:
    name = key.split(":", 1)[1]
    if not isinstance(value, dict) or not _NAME.fullmatch(name):
        raise FoundryMCPError("config", "configure")
    config = deepcopy(value)
    transport = (
        config.get("transport")
        or config.get("type")
        or ("stdio" if "command" in config else "streamable-http")
    )
    if transport in ("http", "streamable_http"):
        transport = "streamable-http"
    if transport not in ("stdio", "streamable-http", "sse"):
        raise FoundryMCPError("config", "configure", server=name)
    if config.get("protocol_mode", "auto") not in ("auto", "legacy"):
        raise FoundryMCPError("config", "configure", server=name)
    env, headers = _strings(config.get("env")), _strings(config.get("headers"))
    if transport == "stdio":
        command, args = config.get("command"), config.get("args", [])
        if (
            not isinstance(command, str)
            or not command.strip()
            or "\x00" in command
            or not isinstance(args, list)
            or any(not isinstance(a, str) or "\x00" in a for a in args)
        ):
            raise FoundryMCPError("config", "configure", server=name)
        if (
            "url" in config
            or headers
            or config.get("cwd") is not None
            and not isinstance(config["cwd"], str)
        ):
            raise FoundryMCPError("config", "configure", server=name)
    else:
        try:
            url = urlsplit(config.get("url", ""))
            valid = (
                url.scheme in ("http", "https")
                and bool(url.hostname)
                and url.port != 0
                and not url.username
                and not url.password
                and not url.fragment
            )
        except (TypeError, ValueError, AttributeError):
            valid = False
        if not valid or "command" in config:
            raise FoundryMCPError("config", "configure", server=name)
        if any(
            not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k)
            or "\r" in v
            or "\n" in v
            or k.lower()
            in {"host", "content-length", "mcp-session-id", "mcp-protocol-version"}
            for k, v in headers.items()
        ):
            raise FoundryMCPError("config", "configure", server=name)
    return _Server(
        key,
        name,
        config,
        transport,
        _timeout(config, ("method_timeout_seconds", "timeout_seconds"), 20),
        _timeout(
            config,
            ("tool_call_timeout_seconds", "call_timeout_seconds", "timeout_seconds"),
            120,
        ),
        headers,
        env,
    )


def _check_schema(value: Any, depth: int = 0):
    if depth > 40:
        raise FoundryMCPError("protocol", "list")
    if isinstance(value, dict):
        for key, child in value.items():
            # Validation must never fetch a schema from a tool-provided URL.
            if key in ("$ref", "$dynamicRef") and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                raise FoundryMCPError("protocol", "list")
            _check_schema(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_schema(child, depth + 1)


class FoundryMCPRuntime:
    def __init__(
        self,
        *,
        managed_servers: ServerProvider | None = None,
        owned_file: str | Path | None = None,
        catalog_ack: CatalogAck | None = None,
    ):
        self.managed_servers = managed_servers
        self.owned_file = Path(owned_file) if owned_file else None
        self.catalog_ack = catalog_ack

    async def _configs(self) -> dict[str, Any]:
        merged = {}
        if self.managed_servers is not None:
            managed = await self.managed_servers()
            if not isinstance(managed, dict):
                raise FoundryMCPError("protocol", "refresh")
            merged.update(
                {"foundry:" + k: v for k, v in managed.items() if isinstance(k, str)}
            )
            if len(merged) != len(managed):
                raise FoundryMCPError("config", "configure")
        if self.owned_file is not None:
            try:
                with self.owned_file.open("rb") as source:
                    data = source.read(MAX_CONFIG_BYTES + 1)
                if len(data) > MAX_CONFIG_BYTES:
                    raise ValueError("config too large")
                loaded = yaml.safe_load(data)
                if (
                    not isinstance(loaded, dict)
                    or set(loaded) - {"servers", "mcp_servers"}
                    or "servers" in loaded
                    and "mcp_servers" in loaded
                ):
                    raise ValueError("invalid owned config")
                owned = loaded.get("servers", loaded.get("mcp_servers", {}))
                if not isinstance(owned, dict) or any(
                    not isinstance(k, str) for k in owned
                ):
                    raise ValueError("invalid servers")
                merged.update({"owned:" + k: v for k, v in owned.items()})
            except (OSError, ValueError, yaml.YAMLError):
                raise FoundryMCPError("config", "configure") from None
        try:
            if (
                len(merged) > MAX_SERVERS
                or len(_json(merged).encode()) > MAX_CONFIG_BYTES
            ):
                raise ValueError("config limit")
            return deepcopy(merged)
        except (ValueError, TypeError, RecursionError):
            raise FoundryMCPError("config", "configure") from None

    @asynccontextmanager
    async def session(self):
        session = FoundryMCPToolSession(self)
        body_error = None
        try:
            async with AsyncExitStack() as stack:
                await session._open(stack)
                try:
                    yield session
                except BaseException as exc:
                    body_error = exc
                    raise
        except Exception as exc:
            if body_error is not None and not isinstance(body_error, FoundryMCPError):
                raise body_error from None
            error = _failure(exc, "session")
            error.calls = deepcopy(session.calls)
            raise error from None
        finally:
            session._closed = True


class FoundryMCPToolSession:
    def __init__(self, runtime: FoundryMCPRuntime):
        self.runtime = runtime
        self._tools: dict[str, tuple[_Server, str, dict]] = {}
        self._definitions: list[dict] = []
        self._closed = False
        self.calls: list[dict] = []
        self._task = anyio.get_current_task().id

    def _check_owner(self):
        if self._closed or anyio.get_current_task().id != self._task:
            raise FoundryMCPError("config", "session")

    @property
    def tools(self) -> list[dict]:
        return deepcopy(self._definitions)

    async def _open(self, stack: AsyncExitStack):
        configs = await self.runtime._configs()
        servers = [_server(key, value) for key, value in sorted(configs.items())]
        for server in servers:
            operation = "initialize"
            try:
                if server.transport == "stdio":
                    errlog = stack.enter_context(open(os.devnull, "w"))
                    transport = stdio_client(
                        StdioServerParameters(
                            command=server.config["command"],
                            args=server.config.get("args", []),
                            env=server.env,
                            cwd=server.config.get("cwd"),
                        ),
                        errlog=errlog,
                    )
                elif server.transport == "sse":

                    def factory(
                        *, headers=None, auth=None, timeout=None, current=server
                    ):
                        return httpx2.AsyncClient(
                            headers=headers,
                            auth=auth,
                            timeout=timeout,
                            trust_env=False,
                            event_hooks={"response": [current.observe]},
                        )

                    transport = sse_client(
                        server.config["url"],
                        headers=server.headers,
                        timeout=server.method_timeout,
                        sse_read_timeout=max(
                            server.method_timeout, server.call_timeout
                        ),
                        httpx_client_factory=factory,
                    )
                else:
                    http = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            headers=server.headers,
                            trust_env=False,
                            timeout=httpx2.Timeout(
                                server.method_timeout,
                                read=max(server.method_timeout, server.call_timeout),
                            ),
                            event_hooks={"response": [server.observe]},
                        )
                    )
                    transport = streamable_http_client(
                        server.config["url"], http_client=http
                    )
                server.client = await stack.enter_async_context(
                    Client(
                        transport,
                        mode=server.config.get("protocol_mode", "auto"),
                        cache=None,
                        input_required_max_rounds=0,
                        read_timeout_seconds=server.method_timeout,
                    )
                )
                operation = "list"
                server.http_error = None
                if server.client.server_capabilities.tools is None:
                    continue
                cursor, seen = None, set()
                for _ in range(MAX_PAGES):
                    with anyio.fail_after(server.method_timeout):
                        listed = await server.client.list_tools(cursor=cursor)
                    for tool in listed.tools:
                        schema = tool.input_schema
                        if (
                            not isinstance(schema, dict)
                            or len(_json(schema).encode()) > MAX_SCHEMA_BYTES
                            or not isinstance(tool.name, str)
                            or not tool.name
                            or len(tool.name) > 128
                        ):
                            raise FoundryMCPError(
                                "protocol", "list", server=server.name
                            )
                        _check_schema(schema)
                        Draft202012Validator.check_schema(schema)
                        digest = hashlib.sha256(
                            (server.key + "\0" + tool.name).encode()
                        ).hexdigest()[:16]
                        slug = re.sub(
                            r"[^A-Za-z0-9_-]", "_", server.name + "_" + tool.name
                        )[:40]
                        name = "mcp_" + slug + "_" + digest
                        if name in self._tools or len(self._tools) >= MAX_TOOLS:
                            raise FoundryMCPError(
                                "protocol", "list", server=server.name
                            )
                        self._tools[name] = (server, tool.name, schema)
                        self._definitions.append(
                            {
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "description": (tool.description or "")[:2048],
                                    "parameters": schema,
                                },
                            }
                        )
                    cursor = listed.next_cursor
                    if not cursor:
                        break
                    if cursor in seen:
                        raise FoundryMCPError("protocol", "list", server=server.name)
                    seen.add(cursor)
                else:
                    raise FoundryMCPError("protocol", "list", server=server.name)
            except Exception as exc:
                raise _failure(exc, operation, server.name, server.http_error) from None
        if self.runtime.catalog_ack is not None:
            await self.runtime.catalog_ack(
                [s.name for s in servers if s.key.startswith("foundry:")]
            )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_owner()
        if name not in self._tools:
            raise FoundryMCPError("tools", "call")
        server, raw_name, schema = self._tools[name]
        try:
            if (
                not isinstance(arguments, dict)
                or len(_json(arguments).encode()) > MAX_ARGUMENT_BYTES
            ):
                raise ValueError("invalid arguments")
            Draft202012Validator(schema).validate(arguments)
        except Exception:
            raise FoundryMCPError("tools", "arguments", server=server.name) from None
        # Revalidate before side effects. A changed endpoint/credential requires a
        # new catalog; never silently replay against a new destination.
        current = await self.runtime._configs()
        if current.get(server.key) != server.config:
            raise FoundryMCPError("auth", "refresh", server=server.name)
        server.http_error = None
        receipt = {"server": server.name, "tool": raw_name, "status": "unknown"}
        self.calls.append(receipt)
        try:
            with anyio.fail_after(server.call_timeout):
                result = await server.client.call_tool(
                    raw_name, arguments, read_timeout_seconds=server.call_timeout
                )
            if result.is_error:
                receipt["status"] = "tool_error"
                raise FoundryMCPError("tools", "call", server=server.name)
            receipt["status"] = "success"
            payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            encoded = _json(payload)
            if len(encoded) > MAX_RESULT_CHARS:
                return {
                    "isError": False,
                    "truncated": True,
                    "content": [{"type": "text", "text": encoded[:MAX_RESULT_CHARS]}],
                }
            return payload
        except Exception as exc:
            raise _failure(exc, "call", server.name, server.http_error) from None
