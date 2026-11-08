# Changelog

Only a dated, tagged entry denotes a release. This file records the source
candidate; it does not backdate or assert unverified publication history.

## Unreleased

The Python distribution `ccfoundry-agent-kit` has never been published to a
package index. The npm launcher `ccfoundry` 0.1.5 (version set on 2026-06-02,
`c998874`) is the last published artifact and embeds the SDK sources of that
time; the entries below are relative to that point.

### Python SDK (source version 0.1.0)

Existing surface:

- FastAPI agent card, JSON/SSE chat, bootstrap approval and resource delivery.
- Foundry sandbox/workspace clients, task tracking, reflections, pull runtime,
  and AP2-inspired settlement helpers.

Added since npm 0.1.5:

- One SDK version source (`ccfoundry_agent_kit.__version__`); bootstrap
  registration reports `sdk_version`. Typed Foundry API errors
  (`FoundryHTTPStatusError` and subclasses, still `httpx.HTTPStatusError`) and
  JSON schemas generated from the models.
- Pull runtime retries `complete` with a bound and isolates a failed invocation
  instead of aborting the whole round. FoundryBootstrap
  discover/invite/register/approval contract tests.
- AgentService manifest generation and container packaging example; Foundry
  waits for `healthcheck_path` after install.
- `AgentSecretAuth`: bearer authentication for operational HTTP and WebSocket
  routes whenever `AGENT_SECRET` is configured; chat honors the requested JSON
  or SSE response mode.
- Approved Foundry MCP tools consumed through `mcp_runtime` (me_agent example).
- Release candidate build/inspection and installation checks; the full MIT
  license is included in Python distributions and npm's embedded SDK.
- Manual release workflow and upload guard; nothing is uploaded unless a
  maintainer dispatches it with `publish=true` on a `python-v<version>` tag.

Upgrade notes:

- An agent with `AGENT_SECRET` set now answers 401 (HTTP) or closes with 1008
  (WebSocket) for requests without `Authorization: Bearer <secret>`. Health,
  manifest, agent card and redacted bootstrap state stay public. Foundry sends
  the bearer on chat, stream, aggregate and proxy calls when the secret is set.
- New runtime dependencies: `mcp>=2.2,<2.3` and `httpx2>=2.5,<3.0`.
- No API is deprecated.

### npm CLI (source version 0.1.5)

- Bundles the Python SDK, example agent and Dev Board API/Web sources.
- Include the previously omitted Verilog Module Writer agent space so both
  built-in templates can be created from an installed npm bundle.
- Dev Board: sign in with a private Foundry account without GitHub, run
  customer agent projects and package local AgentService images, and
  bootstrap dependencies through private mirrors or offline bundles.
- Refreshed Web build dependencies to clear `npm audit` findings.
- Keeps the launcher version independent of the embedded Python SDK version;
  the candidate manifest records both until maintainers choose a future policy.
  The published 0.1.5 must not be reused for these changed contents.

Pending pull requests are not released features. Before publishing, replace this
candidate section with the final version, date, artifact hashes, exact source
tag(s), supported Foundry revisions and migration notes.
