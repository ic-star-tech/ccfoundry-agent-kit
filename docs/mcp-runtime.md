# MCP tools in customer agents

The SDK includes `FoundryMCPRuntime`. It opens real MCP sessions, lists tools,
validates arguments and calls tools over Streamable HTTP, stdio or legacy SSE.
`me_agent` connects that catalog to its existing Chat Completions model loop.
This is source/runtime support; acceptance against a live Foundry deployment
is a separate check (see the end of this page).

## Managed assignments

An approved agent with `manifest.mcp.accepts_foundry_mcp: true` consumes
`allocated_resources.mcp_servers`. In the example, bootstrap refreshes this
complete snapshot through authenticated `POST /api/agents/{name}/registry/sync`.
The request only contains `capabilities.mcp_services`, the names from the last
observed catalog. It does not upload MCP YAML or request new grants.

After connecting/listing, the runtime acknowledges the observed names through
the same metadata sync. This field records a catalog observation, not a live
health guarantee. An empty snapshot removes old assignments; retirement clears
all resources. Failed refreshes clear cached MCP grants and fail the workflow.
The current Foundry Go handler returns the full configured transport, endpoint,
headers or command/args/env unchanged for this metadata-only request.

Immediately before each call the SDK refreshes grants again. Removed or changed
configuration stops the call and requires a new session/catalog. It does not
silently reconnect and replay against a changed endpoint. Calls already in
flight cannot be undone by a subsequent revocation; endpoint-side authorization
and tool-side idempotency remain necessary for side effects.

```python
from ccfoundry_agent_kit import FoundryMCPRuntime

runtime = FoundryMCPRuntime(
    managed_servers=bootstrap.mcp_servers,
    catalog_ack=bootstrap.acknowledge_mcp_catalog,
)
async with runtime.session() as session:
    function_tools = session.tools  # Chat Completions function definitions
    # Select only a name from this catalog; validate inputs against its schema.
    result = await session.call_tool(function_tools[0]["function"]["name"],
                                     {"query": "the user's search"})
```

The example assumes a listed search tool with that argument schema. A session
must be opened, used and closed in one async task. Use a new session per request;
do not move its clients between event loops or share them across concurrent chats.

## Agent-owned configuration

Set `manifest.mcp.has_own_mcp: true` to enable `mcp_file` in `me_agent`.
Relative paths resolve from `ME_AGENT_BASE_DIR`. This is explicit operator-owned
configuration, separate from Foundry-managed grants. Names are qualified by
origin and mapped to stable, collision-checked function names.

```yaml
servers:
  search:
    type: streamable-http
    url: https://mcp.example.test/mcp
    headers:
      Authorization: Bearer REPLACE_WITH_SERVER_CREDENTIAL
    tool_call_timeout_seconds: 45
  local:
    command: /opt/my-tools/venv/bin/my-mcp
    args: ["--stdio"]
    env:
      MY_TOOL_SETTING: example
```

Accepted transports are `streamable-http` (aliases `http`, `streamable_http`),
`sse`, and `stdio`. Use `sse` explicitly for old `/sse` endpoints; a transport
failure does not trigger a fallback. With no type, a command means stdio and a
URL means Streamable HTTP. HTTP `headers` are explicit; `env` is for stdio and
is never converted to HTTP headers. The Foundry agent secret is used only for
Foundry refresh, never forwarded to MCP servers or the model. Referenced
credential bindings are not resolved into credentials by this client.

stdio launches argv directly with the official SDK's minimal environment plus
explicit `env`, without a shell or automatic package installation. Its binaries
must already be present in the agent environment. Child stderr is discarded;
exit and cancellation close/reap the child. HTTP verifies TLS and ignores
ambient proxy variables. The upstream transport restricts redirects to the same
origin or a same-host HTTP-to-HTTPS upgrade. Credential-bearing URLs are rejected.

The dependency is the official [MCP Python SDK 2.2 series](https://github.com/modelcontextprotocol/python-sdk/tree/v2.2.0).
Its default `auto` negotiation supports modern discovery and legacy initialize;
set `protocol_mode: legacy` when an endpoint requires the older handshake.
Sampling, elicitation, roots and input-required tool replay are not enabled by
this adapter. These are not implicit permissions granted by receiving a tool.

## Bounds and failures

Method timeout defaults to 20 seconds; tool timeout to 120. Numeric
`method_timeout_seconds`, `tool_call_timeout_seconds`, or shared
`timeout_seconds` values must be positive and no more than 600 seconds.
Timeout means the result is unknown, not that an action was rolled back.

The runtime allows at most 16 servers, 16 catalog pages per server and 128 tools
total. Schemas and arguments have 32 KB limits, configuration a 256 KB limit;
schema validation never fetches remote references. Tool results exceeding
24,000 characters are explicitly marked truncated. The transport still parses
the upstream message before this presentation limit; this is not a process
memory sandbox for a hostile server.

`FoundryMCPError` distinguishes `config`, `network`, `timeout`, `auth`, `http`,
`protocol` and `tools`, following Foundry's MCP error categories. It exposes the
operation, safe server name, actual HTTP status when available, and call
receipts (`success`, `tool_error`, `unknown`). URLs, headers and raw upstream
errors are excluded. There is no automatic tool-call retry.

The example performs at most three tool rounds/eight calls and one final model
request. It preserves `tool_call_id` on results, serializes calls, checks grants
before each one, and refuses unoffered tools, malformed arguments and duplicate
call IDs. A failed tool or subsequent model request produces an explicit error
with existing receipts, without a fabricated answer or a tool replay. This uses
the existing compatible model configuration; it does not migrate providers or
promise that every provider/model supports Chat Completions tool calling.
See the official [function calling guide](https://developers.openai.com/api/docs/guides/function-calling).

## Validation and installation order

Install the SDK, API and example into an isolated Python environment and run
`python -m pytest packages/python-sdk/tests apps/agent-dev-board-api/tests`.
The tests use actual loopback HTTP/SSE and child stdio processes; model responses
are synthetic. `tests/contracts/foundry_mcp.json` is the shared request/resource
contract. Foundry maintainers run the same fixture against the server's registry
handler in Foundry's own test suite; that paired check proves request/resource
compatibility, not a single Python-process-to-server end-to-end model test.

To accept a deployment on your own test Foundry instance, approve `me_agent`,
assign the instance web-search MCP, ask a source-backed question, retain the actual tool receipt/model answer,
then revoke the grant and verify a new tool call is refused. Record exact image,
SDK and model versions. Fixture success does not complete that acceptance.
