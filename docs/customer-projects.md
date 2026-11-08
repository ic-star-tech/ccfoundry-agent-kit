# Build and debug your own Python agent

Dev Board accepts customer source directories without a GitHub repository or
GitHub account. The operator registers projects when launching the board; the
browser can select these projects but cannot submit arbitrary host paths.
Use a trusted local workstation and trusted source: starting an app or building
its dependencies executes that source as your local user or Docker operator.

This workflow complements the Foundry account login in the intranet-login
change. Local processes, local Docker containers and private registry/image
archive delivery are supported here. Cloud Run currently builds the bundled
example and is disabled for customer projects.

## Prepare and register a project

Create `ccfoundry.project.json` in your project root:

```json
{
  "version": 1,
  "label": "Customer support agent",
  "description": "Our internal support workflow",
  "app_module": "support_agent.app:app",
  "app_dir": "src",
  "agent_space": "agent_space",
  "python": ".venv/bin/python",
  "build": {
    "version": "0.1.0",
    "python_image": "python:3.12-slim",
    "include": ["src", "agent_space", "pyproject.toml", "README.md"],
    "config_env": ["OPENAI_BASE_URL", "OPENAI_API_KEY"]
  }
}
```

`src/support_agent/app.py` must export an ASGI app. Use the SDK's
`create_agent_app`, `AgentManifest` and chat handler to implement the Foundry
HTTP contract, bootstrap and task features your agent needs. This descriptor
selects an existing app; it does not generate a handler, add bootstrap to an
arbitrary app, or adapt another framework's chat protocol.

All source paths must stay within the registered root. Listing projects and
creating a source do not import code or install dependencies. Prepare the
project's Python environment explicitly before local Python debugging:

```sh
cd /path/to/customer-project
python3 -m venv .venv
.venv/bin/python -m pip install -e . 'uvicorn>=0.30,<1.0'
ccfoundry --project /path/to/customer-project
```

The project must declare its SDK/runtime dependencies. Existing `pip.conf`,
`PIP_CONFIG_FILE`, index and CA settings apply to the explicit pip command.
Repeat `--project` for additional projects, or set `CCFOUNDRY_AGENT_PROJECTS`
to a JSON array of absolute paths. These are server startup settings; restart
the board to register new roots. Edit a registered descriptor to change the
entry point or build inputs, then stop and restart/rebuild the source.

In **Create agent source**, select the customer project. A source has its own
stable name, port and runtime directory. **Start local Python runtime** uses
its declared Python executable, working directory and `app_module`. A missing
venv fails without installing anything. Built-in template paths remain
confined to the kit checkout.

The app must use `CCFOUNDRY_AGENT_BASE_DIR` (or `ME_AGENT_BASE_DIR`) for writable
state, with `agent_space/` and `workspace/` beneath it. The original
`agent_space` is copied when creating the source. Later source edits do not
overwrite that runtime's existing data. Bootstrap-aware apps should use
`FOUNDRY_BOOTSTRAP_STATE_PATH` and the supplied Foundry/discovery environment.

## Build and run a local container

Install Docker Engine/Desktop and [Docker Buildx with BuildKit](https://github.com/docker/buildx#installing).
Use **Build customer image**, choose the target architecture and optionally a
local tag. The board snapshots the declared inputs, packages the checkout's
Python SDK, and installs the project's `pyproject.toml` and/or `requirements.txt`.
It also installs Uvicorn. Python dependencies requiring system packages need a
prepared Python base image; this builder does not install OS packages.

The image starts the declared customer app as UID/GID `10001:10001`. It requires
`AGENT_SECRET` and checks bearer authentication before calling the app; only
GET/HEAD `/health` are public in the packaged production runtime. The app still
needs to implement its own health, chat and task endpoints. A project with
additional authentication must accept the Foundry/SDK credential too.

**Start local container** uses the exact built image ID. Stop a Python runtime
first. The local Docker engine must use a Unix socket. The container maps only
`127.0.0.1:<source-port>`, drops capabilities, has a read-only root, and mounts
only that source's writable runtime directory. It uses the current non-root
user's UID/GID, or 10001 when the board runs as root. Docker logs are limited to
2 files of 10 MiB. Stop and recreation retain the source's data.

The board creates a separate random debug bearer in a mode-0600 runtime file.
It sends that bearer only to the matching source URL and never returns it to
the browser. The wrapper permits public manifest/bootstrap reads during local
debugging and adapts the operator bearer to the current SDK bootstrap secret.
Only an operator-authenticated developer-claim request gets the loopback
identity required by the SDK. The local container uses outbound poll/pull
bootstrap; it does not expose unauthenticated push callbacks.

Build receipts include source/context hashes, the actual image ID and inspected
architecture. Docker image IDs and published manifest/index digests can differ between image stores; use the recorded and destination-inspected values. An
architecture option does not install emulation: build ARM64 on ARM64 hardware
or an already configured compatible builder. Test on the destination
architecture before deployment. Existing source/container files are not
rewritten just by inspecting a build receipt.

## Pip mirror, CA and offline image builds

Container builds deliberately do not inherit the board host's pip credentials.
Use descriptor fields pointing to operator-managed files:

```json
{
  "build": {
    "python_image": "registry.example.internal/python:3.12-slim",
    "pip_config_file": "/secure/project-pip.conf",
    "pip_ca_file": "/secure/company-ca.pem",
    "wheelhouse": "/offline/wheels-py312-arm64",
    "offline": true
  }
}
```

For online builds, `pip_config_file` can set the private `index-url` and
`pip_ca_file` supplies its trusted CA. Do not disable TLS verification. These
files are BuildKit secrets mounted only during dependency installation; their
contents are not copied into the image or build receipt. Build tools can print
configuration, so build/push logs stay private on the board host.

Offline mode requires the Python base image already present on the selected
Docker engine and a wheelhouse for the chosen Python/platform. Include all
transitive runtime dependencies plus build dependencies (`setuptools>=69`,
`wheel`, and any project-specific build backend requirements). The build uses
`--network=none`, pip `--no-index --find-links`, and `--pull=false`. Missing
wheels/base images fail. It does not silently retry online. Stage Docker's
builder components and base image before disconnecting; the network restriction
covers Dockerfile build steps, not all daemon metadata or builder setup.

Keep `build.include` narrow. Without it, the builder includes `app_dir`,
`agent_space`, and existing `pyproject.toml`, `README.md`, `requirements.txt`.
It excludes common environment/credential files, configured pip secret files,
Git metadata, venvs, node_modules and local runtime/log directories. It rejects
symlinks and out-of-root paths and caps source contexts at 20,000 files/512 MiB.
This is not a classifier for arbitrary secrets: review application files and
dependency URLs before including them. Secrets embedded in Python code or a
nonstandard file name remain your responsibility.

The Dev Board CLI's own pip/npm bootstrap still follows its existing dependency
installation path. Offline/mirror setup for that launcher is a separate #12
follow-up; the image wheelhouse option does not make npm bootstrap offline.

## Export or push, then install on Foundry

**Export image archive** saves `image.tar`, its SHA-256 and `agentservice.yaml`
on the board host. Transfer them using your approved internal channel and
verify the archive checksum. On the target appliance, an operator loads and
tags the exact image named by the receipt:

```sh
sha256sum image.tar
docker image load --input image.tar
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' LOADED_IMAGE_ID
docker image tag LOADED_IMAGE_ID local/SOURCE-NAME:PROJECT-VERSION
```

Use the repository/tag from `agentservice.yaml`, including underscore-to-dash
normalization. Use the loaded image ID reported by Docker. Inspect the architecture on the
appliance and compare it with the receipt. Docker image stores may represent
IDs differently; after verifying the archive checksum and the loaded image,
set `spec.image.digest` to the appliance-inspected `.Id` before uploading.
Do not substitute an unrelated existing image. The generated manifest
uses the actual image ID, receives Foundry's `AGENT_SECRET`, and declares a
runner-managed `/app/state` volume with non-root ownership. First start seeds
`agent_space` only if absent; replacement containers preserve existing state.

For a private registry, run `docker login registry.example.internal` on the
board host, enter the full `host/repository:tag`, then click **Push to private
registry**. This explicitly publishes that built image. The board relies on
Docker's credential store and records the returned repository digest. Do not
concurrently retag the same destination from another process. A timeout or tag
change requires inspecting the registry before retrying; the board does not
automatically replay uncertain pushes.

The appliance operator pulls the recorded `repository@sha256:digest`, verifies
its image ID, and tags it under the manifest's `local/` repository. The Foundry
local-package runner does not pull private registry images itself. Upload the
manifest via **Admin → Marketplace → Local packages** and install under the
normal admin/plan checks. See [AgentService packaging](agentservice-packaging.md)
for prerequisites, configuration, upgrade and rollback.

No build/export action installs on Foundry. A temporary registry test is not
customer registry publication, and an amd64 CI test is not ARM64 acceptance.
