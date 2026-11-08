# Packaging An Agent As A Foundry AgentService (Local Package)

This guide shows how to take an agent built with this kit, for example
[`examples/me_agent`](../examples/me_agent/), and install it on your own
CoChiper Foundry instance, such as a DGX Spark appliance (linux/arm64), as a
Marketplace **local package**.

A worked example lives in [`examples/agentservice/`](../examples/agentservice/):

| File | Purpose |
|------|---------|
| `Dockerfile` | Builds `me_agent` with the SDK into a non-root image serving port 8080 |
| `foundry_service.py` | Entry point that wraps the agent and enforces `AGENT_SECRET` |
| `manifest.yaml` | The `AgentService` manifest you upload to Foundry |
| `Dockerfile.dockerignore` | Keeps local runtime state and secrets out of the image |

## What a local package is

A local package is an `AgentService` whose manifest the **instance admin
uploads directly** in Admin Panel → Marketplace → Local packages. Compared
with an official Marketplace package:

- there is no official catalog entry, no publisher control plane and no registry token:
  the image is already on the host, and the install charges no credits;
- the instance needs the **advanced** plan or higher. Foundry raises any lower
  `spec.access.required_plan` to `advanced` and adds the
  `marketplace_advanced_agents` feature requirement;
- the agent is registered with trust level `TRUSTED` (official packages are
  `OFFICIAL`), so Foundry runs its content guard on every reply (see
  [Content safety](#content-safety));
- preflight, install, uninstall, the service proxy, the management UI entry and
  the `AGENT_SECRET` handshake work the same way as for official packages.

The steps are:

1. Make the agent honour the chat contract and check `AGENT_SECRET`.
2. Build an image for the appliance's architecture and put it on the appliance.
3. Write the manifest, set `spec.image.digest` from the appliance, validate it.
4. Upload the manifest, run preflight, install.

## The chat contract

Foundry reaches the agent at its **service URL**,
`<protocol>://<container_name>:<port><endpoint_path>`, on the instance's
internal network. With the example manifest that is
`http://foundry-agent-me-agent:8080/`.

### Request

For every user message Foundry sends `POST <service_url>/api/chat` with a JSON
body. If that request fails (404, 405, any other status of 400 or above, or a
connection error), Foundry retries the same request on `POST <service_url>/chat`.

Headers:

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <AGENT_SECRET>` |
| `X-Foundry-Agent` | the agent's `spec.agent.agent_name` |
| `Content-Type` | `application/json` |

Body:

| Key | Type | Meaning |
|-----|------|---------|
| `message` | string | The user's message |
| `content` | string | Same text as `message` |
| `mode` | string | `direct`, or `inline` when the agent is invoked inline from another agent's chat |
| `context` | string | `[system] current user: <username> (role: <role>)`, an inline-mode line when applicable, then every earlier turn of the session as `[user] ...` / `[assistant] ...` lines |
| `username` | string | Foundry username |
| `user_id` | string | Stable per-user pseudonym; use it instead of the username as a key for per-user state |
| `session_id` | string | Chat session ID |
| `conversation_id` | string | Same as `session_id` |
| `stream` | bool | Present and `true` when Foundry wants a streamed reply |
| `skill_ref` | string | Present when the user picked a slash command bound to a skill |

The whole session history is in `context`, so trim it if your model has a
small context window.

### Authentication

At install time Foundry generates a random `AGENT_SECRET` for the agent, stores
it encrypted, and sends it as the bearer on every chat and service-proxy
request. The container receives it as the `AGENT_SECRET` environment variable
**only if the manifest lists it** under `spec.runtime.config_env`.

The SDK checks `Authorization: Bearer <AGENT_SECRET>` on operational routes,
including chat and workspace APIs. The example entry point additionally refuses
to start without the injected secret. Health, manifest and discovery metadata
remain public; keep them free of secrets. See [SDK authentication](sdk.md#agent-authentication)
for the bootstrap callback exceptions and local development behavior.

### Non-streaming reply

The SDK returns `200` and a JSON object when `stream` is false or omitted;
`stream: true` selects SSE. The non-streaming response has this shape:

```json
{
  "reply": "The answer shown to the user",
  "model": "gemma-4-26b",
  "usage": {"prompt_tokens": 812, "completion_tokens": 164, "total_tokens": 976}
}
```

- `reply` is required. Without it the user sees `No reply field in agent response`.
- `model` and `usage` are optional. When present, Foundry records the call in
  the instance usage ledger under that model.

### Streaming reply

When the request has `"stream": true` the agent may answer with
`Content-Type: text/event-stream`. Foundry understands these events:

| Event | Data | What Foundry does |
|-------|------|-------------------|
| `step` | `{"status": "running" \| "done" \| "error", "text": "..."}` | Shows it as a progress step and stores it with the message |
| `token` | `{"content": "..."}` | Streams it to the browser and appends it to the reply |
| `message` | `{"content": "...", "model": "...", "usage": {...}}` | `content` replaces the accumulated reply; `model`/`usage` are recorded as above. Not forwarded as is |
| `done` | `{}` | Optional; the stream ends when the connection closes |

Foundry ignores other events, including `error`. To show a failure to the user,
put it in the reply text. If a streaming request gets a plain JSON response
instead, Foundry uses its `reply` field, so an agent that never streams also works.

Each chat request, streamed or not, has a **180 second** limit, including the
time spent streaming.

### What the SDK gives you

`create_agent_app()` in [`packages/python-sdk`](../packages/python-sdk/)
already serves the Foundry-facing routes:

- `POST /api/chat` maps the Foundry body above into a `ChatRequest` and runs
  your handler once. Without `stream` (or with `stream: false`) it answers with
  the JSON `ChatResponse`; with `stream: true` it answers as SSE: `step`
  events, `token` chunks, a `message` event with the full `ChatResponse`, then
  `done`.
- `POST /chat` takes the same body and returns the `ChatResponse` as JSON.
- `GET /health` returns the agent name and version.
- Extra fields on `ChatResponse` are passed through, so
  `ChatResponse(reply=text, model=model_name, usage={...})` reports usage.

When `AGENT_SECRET` is set, the SDK's `AgentSecretAuth` requires
`Authorization: Bearer <AGENT_SECRET>` on every operational route it mounts,
including the workspace file API (see [Authentication](#authentication)).
[`examples/agentservice/foundry_service.py`](../examples/agentservice/foundry_service.py)
uses the same implementation and additionally refuses to start when
`AGENT_SECRET` is missing, so a package cannot run unprotected by mistake.

## Content safety

A local package is `TRUSTED`, so Foundry runs its content checks on every reply
before storing it. The exact rules are server-side policy and may change
between releases. Design your agent for this contract:

- Never return secrets, keys, tokens or private key material, and avoid
  credential-shaped `key=value` examples: a check cannot tell an example from a
  leak.
- Do not return destructive shell commands or links to malicious content.
- A reply that fails the checks is replaced, in the chat and in the stored
  history, by a safety notice with the reason, including when it was streamed.
  Violations are visible to the instance admin.
- Depending on the agent's history, the instance may apply stricter review to
  its replies.

## Optional host features

These go under `spec.agent` in the manifest.

**Service proxy.** Lets your agent serve its own pages and APIs through Foundry:

```yaml
service_proxy:
  admin_paths: ["/ui/", "/api/admin/"]   # admins only
  user_paths: ["/api/sources/"]          # any user who can use this agent
```

Foundry forwards `/api/agents/<agent_name>/service/<path>` to
`<service_url>/<path>` when the path is covered: an entry ending in `/` is a
prefix, any other entry matches itself and paths below it. Everything else is
`404`. Foundry removes the caller's `Authorization` and cookies, and adds
`Authorization: Bearer <AGENT_SECRET>`, `X-Foundry-Agent`, `X-Foundry-User`
(username), `X-Foundry-Role` and
`X-Forwarded-Prefix: /api/agents/<agent_name>/service`. `Set-Cookie` from
the container is dropped and redirects are rewritten under the proxy prefix, so
build pages with relative URLs and keep state server-side.

**Management UI.** Adds an entry for admins that opens a page served through
the proxy. The path must be covered by `admin_paths`:

```yaml
management_ui:
  path: /ui/
  label: "Document management"
```

**Citations.** A reply can end with a fenced block whose info string is
`sources` and whose body is JSON. The Foundry chat shows it as a sources panel
instead of raw text:

````markdown
```sources
{"answer_status": "answered",
 "evidence": [{"id": "E1", "kb": "manuals", "file": "datasheet.pdf", "page": 12,
               "heading": "Pin description", "chunk_id": "c-81", "document_id": "d-7",
               "url": "/api/agents/me_agent/service/api/sources/d-7"}]}
```
````

`answer_status` is one of `answered`, `insufficient_evidence`,
`no_knowledge_base`, `dependency_failed`, `sources_conflict` or
`needs_clarification`. Give each entry a `url` under one of your `user_paths` so
users can open the original document through the proxy.

## Build the image

The appliance is linux/arm64; a laptop is usually x86. Build the example from
the repository root with Docker Buildx:

```bash
docker buildx build --platform linux/arm64 \
  -f examples/agentservice/Dockerfile \
  -t local/me-agent:0.1.0 \
  --output type=docker,dest=me-agent-0.1.0-arm64.tar .
```

Build from a clean checkout. The example's `Dockerfile.dockerignore` excludes
`me_agent`'s local runtime state, but anything else you add to `agent_space/`
ships in the image.

Cross-building runs the `RUN` steps under emulation. If Buildx reports that it
cannot run arm64 binaries, register QEMU once with
`docker run --privileged --rm tonistiigi/binfmt --install arm64`. To test on
your own machine, build for its architecture and use `--load` instead of
`--output`.

Copy the tar to the appliance and load it:

```bash
scp me-agent-0.1.0-arm64.tar admin@appliance:/tmp/
ssh admin@appliance docker load -i /tmp/me-agent-0.1.0-arm64.tar
```

If you have a private registry, push there instead, then on the appliance
`docker login` with your own credentials, `docker pull` the image and retag it:
`docker tag registry.example.com/team/me-agent:0.1.0 local/me-agent:0.1.0`.

The marketplace runner never pulls a local package and holds no registry
credentials. At install time it looks for the image on the host, requires its
repository to start with a local prefix (`local/` unless the operator changed
`MARKETPLACE_LOCAL_IMAGE_PREFIXES`), and requires `spec.image.digest` to equal
the image ID or the digest of one of its `RepoDigests`.

## Write the manifest

Start from [`examples/agentservice/manifest.yaml`](../examples/agentservice/manifest.yaml).
The fields that matter:

| Field | Notes |
|-------|-------|
| `metadata.name` | Package name, for example `me-agent` |
| `metadata.version` | Shown in the catalog; bump it for every new image |
| `spec.image.repository` | `local/<name>`, without a tag |
| `spec.image.tag` | The tag you loaded; the runner looks up `<repository>:<tag>` (default `latest`) |
| `spec.image.digest` | See below |
| `spec.runtime.container_name` | Also the host name in the service URL, for example `foundry-agent-me-agent` |
| `spec.runtime.port` | Port the agent listens on inside the container |
| `spec.runtime.endpoint_path` | Base under which Foundry calls `/api/chat`; `/` for the container root |
| `spec.runtime.healthcheck_path` | `/health`, served without authentication. Foundry polls it after install and returns only once it answers below 500 (see below) |
| `spec.runtime.environment` | Fixed environment values |
| `spec.runtime.config_env` | Names of values injected into the container. Must include `AGENT_SECRET` |
| `spec.runtime.additional_networks` | `foundry_agent_llm_net` to reach the instance LLM gateway |
| `spec.runtime.requires_egress` | `true` if the agent needs to reach hosts outside the instance |
| `spec.config_schema` | Fields the admin fills in at install time; `type: secret` hides the value |
| `spec.agent.agent_name` | The agent's ID in Foundry, for example `me_agent` |
| `spec.access.required_plan` | `advanced` (lower values are raised) |

### How settings reach the container

- `spec.runtime.environment` holds fixed values, the same on every instance.
- `spec.runtime.config_env` lists the names of values the runner injects at
  install time: values Foundry issues itself, such as `AGENT_SECRET`, and
  values the admin enters in the install form for the fields of
  `spec.config_schema` (or their defaults). A value whose name is not listed
  never reaches the container, even when it has a `config_schema` field.
- Without `AGENT_SECRET` in `config_env` the agent never learns the secret
  Foundry sends as its bearer and cannot tell Foundry's calls from anyone
  else's. The example entry point refuses to start in that case, and the
  validator warns about it.
- `spec.runtime.endpoint_path` sets the service URL, the base under which
  Foundry calls `/api/chat` and `/chat` and the service proxy forwards paths.
  Always set it: when it is missing the runner falls back to a spec-level
  `spec.healthcheck_path`, which would turn the health path into the base.

The example passes the LLM settings `me_agent` reads (`OPENAI_BASE_URL`,
`OPENAI_API_KEY`, `ME_AGENT_MODEL`) through `config_schema` and `config_env`.
All three are optional: without a key, `me_agent` answers in its offline
fallback mode.

### Keep data across upgrades

The container's filesystem is replaced on every reinstall or upgrade. To keep
data, declare a runner-managed volume (the directory lives under
`/fdata/marketplace/installations/<name>/data/<subpath>` and survives
reinstall):

```yaml
runtime:
  persistent_volumes:
    - subpath: data
      container_path: /app/data
      uid: 10001        # the image's non-root user
      gid: 10001
      mode: "0700"
```

### Get the digest

On the appliance, after `docker load` (or `docker pull` and `docker tag`):

```bash
docker image inspect --format '{{.Id}}' local/me-agent:0.1.0
```

Put the output in `spec.image.digest`. Take it on the appliance, not on the
build machine: depending on the Docker image store, the ID is either the
image's config digest or its manifest digest, and the two machines may differ.

### Validate

The SDK ships a validator that applies the same rules as the Foundry server and
the runner's image prefix check:

```bash
pip install -e packages/python-sdk
python -m ccfoundry_agent_kit.agentservice_manifest examples/agentservice/manifest.yaml
```

It prints each problem and exits with status 1 when there are any. It also
prints warnings for mistakes Foundry accepts but that break the package, such
as `AGENT_SECRET` missing from `config_env`; warnings do not change the exit
status. Add `--local-prefixes local/,registry.example.com/team/` if the
instance operator allows more prefixes.

From Python, `validate_local_manifest(text)` in
`ccfoundry_agent_kit.agentservice_manifest` returns the list of problems and
`local_manifest_warnings(text)` returns the warnings.

## Install on the instance

1. Admin Panel → Marketplace → **Local packages**: choose the manifest file or
   paste the YAML, then **Save local package**. Saving again with the same
   `metadata.name` replaces it.
2. The package appears in the catalog under Agent Services with a
   *Customer-built* badge. Select it, run **preflight**, then **install**.
3. The agent shows up in the agent list; start a chat with it.

After the runner starts the container, Foundry waits for the agent to answer before the
install call returns: it polls `spec.runtime.healthcheck_path` until the status is below 500,
or, without a health path, until the port accepts connections. It waits up to 90 seconds by
default (`FOUNDRY_MARKETPLACE_SERVICE_READY_TIMEOUT_SECONDS` on the instance). A timeout does
not fail the install; the result carries `service_ready.ready = false` and the page asks the
admin to wait before chatting. Keep `/health` cheap and unauthenticated so this works.

To upgrade, load the new image, update `spec.image.tag`, `spec.image.digest`
and `metadata.version`, save the manifest again, then select the package and
click **Update**. The runner replaces the container; persistent volumes and
chat history are kept.

Uninstalling from the catalog removes the container. Deleting the local package
only deletes the manifest; an installed service keeps running until you
uninstall it.

## Rules Foundry enforces

Saving a manifest fails with the listed errors unless all of these hold:

| Field | Rule |
|-------|------|
| whole manifest | Non-empty YAML object, at most 256 KiB |
| `apiVersion` | `foundry.ai/v1` |
| `kind` | `AgentService` |
| `metadata.name` | Matches `^[a-z0-9][a-z0-9-]{1,62}$`; not the name of an official catalog item |
| `metadata.version` | Required, at most 64 characters |
| `spec.image.repository` | Required; matches `^[a-z0-9][a-z0-9._:/-]{0,254}$`; no tag (no `:` after the last `/`) and no digest; does not start with `registry.cochiper.com/foundry/` or `ccfoundryprod.azurecr.io/foundry/` |
| `spec.image.digest` | Matches `^sha256:[0-9a-f]{64}$` |
| `spec.runtime.container_name` | Required |
| `spec.runtime.port` | Required |
| `spec.agent.agent_name` | Required; matches `^[A-Za-z0-9_-]+$`; does not start with `user_agent_`; not used by another agent on the instance (reinstalling the same local package is fine) |
| `spec.agent.vscode_bridge.enabled` | A boolean, if `vscode_bridge` is declared |
| `spec.pricing.install_credits` | A non-negative integer, if declared (Foundry then sets it to 0) |

At install time the runner also requires the repository to start with a local
prefix (default `local/`) and the image with the matching digest to be present
on the host.

The official-name and agent-name collision checks need the instance, so the
SDK validator cannot run them; everything else in the table is checked locally.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Install fails: image outside the local prefixes | Retag the image as `local/<name>` and update `spec.image.repository` |
| Install fails: digest does not match | Take the digest on the appliance with `docker image inspect --format '{{.Id}}'` |
| Install fails: container exited during startup | Read the log in the error; for the example, `AGENT_SECRET is not set` means `config_env` lacks `AGENT_SECRET` |
| Chat shows "Proxy request failed" with 401 | The agent rejects Foundry's bearer, or reads the secret from the wrong variable |
| Chat shows "No reply field in agent response" | The JSON reply has no `reply` field |
| Reply replaced by a safety notice | The content guard matched; see [Content safety](#content-safety) |
