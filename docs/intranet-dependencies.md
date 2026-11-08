# Dev Board dependencies on a private network

The `ccfoundry` launcher accepts a locally delivered npm tarball or a source
checkout. Starting the installed CLI does not clone GitHub. It needs Python
3.10+ with `venv`, Node 18+, npm, and a writable package installation (the web
app's `node_modules` is inside it). Use an installation owned by the developer,
not a shared system package directory. Keep the Dev Board on loopback unless
its access is separately controlled.

## Private pip and npm mirrors

The launcher inherits native pip/npm configuration. Configure the mirrors and
CA files in private files outside the kit and customer source tree:

```ini
# /private/config/pip.conf
[global]
index-url = https://python.packages.example/simple/
cert = /private/config/company-ca.pem
```

```ini
# /private/config/npmrc
registry=https://node.packages.example/
cafile=/private/config/company-ca.pem
strict-ssl=true
# If needed, scope a token to the actual registry host/path:
//node.packages.example/:_authToken=${NPM_TOKEN}
```

```bash
PIP_CONFIG_FILE=/private/config/pip.conf \
NPM_CONFIG_USERCONFIG=/private/config/npmrc \
ccfoundry --workspace ./my-board --setup-only
```

Supply credentials using the package manager's supported private configuration
or environment, not a committed file. The launcher never adds a public mirror,
`trusted-host`, or a TLS verification bypass. It also does not erase existing
pip `extra-index-url`, npm registry overrides, proxy or CA settings: remove
unwanted fallback sources from your own configuration. In particular, pip
`PIP_INDEX_URL` overrides the file's `index-url`. Check configuration privately;
package manager diagnostics can contain sensitive values.

`--setup-only` installs and checks dependencies, then exits without writing the
runtime agents file or opening service ports. Omit it to start the API and web
app. Python packages are editable installs of the SDK, example and API; web
packages use the shipped lockfile with `npm ci`, including development and
optional dependencies required by Vite/esbuild.

## Prepare an offline bundle

Prepare on a connected machine/container with the **same OS, CPU architecture,
Python minor version and Node/npm versions** as the destination. Obtain a
reviewed kit tarball through your delivery channel. Do not copy an existing
`.venv` or `node_modules` between machines.

For example, in a writable staging directory containing `ccfoundry-VERSION.tgz`:

```bash
mkdir kit-extracted offline-assets
tar -xzf ccfoundry-VERSION.tgz -C kit-extracted
KIT_DIR="$PWD/kit-extracted/package"
ASSET_DIR="$PWD/offline-assets"
python3 -m venv bundle-tools
bundle-tools/bin/python -m pip download --only-binary=:all: \
  --dest "$ASSET_DIR/wheels" pip setuptools wheel \
  "$KIT_DIR/packages/python-sdk" \
  "$KIT_DIR/examples/me_agent" \
  "$KIT_DIR/apps/agent-dev-board-api"
npm ci --prefix "$KIT_DIR/apps/agent-dev-board-web" \
  --cache "$ASSET_DIR/npm-cache" --no-audit --no-fund
```

npm runs inside the installed web package, so a `.npmrc` in the separate
workspace is not its project configuration; use `NPM_CONFIG_USERCONFIG` for an
explicit private file.

These preparation commands use the configured mirrors too. Supplying all three
local Python packages together prevents resolution of a different SDK from an
index. Include build requirements (`setuptools` and `wheel`) and `pip`, as well
as transitive runtime dependencies. If a target-compatible wheel does not exist,
prepare and review it in a compatible build environment; do not silently change
to an online source build on the offline machine. Pin or constrain versions in
your delivery process and retain hashes and provenance for the exact bundle.

Transfer the tarball, complete wheels directory and complete npm cache to the
destination. Verify the delivery hashes there. Install into a writable local
prefix, then bootstrap:

```bash
npm install --offline --ignore-scripts --no-audit --no-fund \
  --prefix ./kit-install ./ccfoundry-VERSION.tgz
./kit-install/node_modules/.bin/ccfoundry \
  --workspace ./my-board --offline \
  --wheelhouse ./offline-assets/wheels \
  --npm-cache ./offline-assets/npm-cache --setup-only
```

Run the same last command without `--setup-only` to start Dev Board.
`--offline` sets pip `--no-index` and a local `--find-links` directory, including
for isolated editable builds, and npm `--offline`. Missing wheels or cached
npm packages cause failure; there is no online retry. Wheelhouse paths with
spaces are supported. Offline mode replaces inherited pip `find-links` with
the selected local directory. Use reviewed dependencies: package build hooks,
install scripts and direct URL requirements are executable inputs, so these
package-manager options are not a general network sandbox. Enforce network
isolation externally when that is part of your delivery policy.

| CLI | Environment fallback | Meaning |
| --- | --- | --- |
| `--offline` | `CCFOUNDRY_OFFLINE=true` or `1` | Use prepared offline assets |
| `--online` | — | Override `CCFOUNDRY_OFFLINE`; retain native pip/npm settings |
| `--wheelhouse PATH` | `CCFOUNDRY_WHEELHOUSE` | Local wheels; required in offline mode |
| `--npm-cache PATH` | `CCFOUNDRY_NPM_CACHE` | npm cache directory; otherwise npm's configured cache |
| `--setup-only` | — | Install/check without starting services |

CLI paths override these environment fallbacks and resolve from the invoking
working directory. `--online` does not override a native `PIP_NO_INDEX` or
`npm_config_offline` setting. The mode controls dependency bootstrap; model
calls, Foundry login, project dependencies and container base images have their
own connectivity requirements. This guide does not by itself validate a
private Foundry or ARM64 deployment.

## Refresh and recovery

A successful setup writes private, atomic markers containing only a schema and
opaque SHA-256 fingerprint. They cover package metadata/lockfile contents,
installation path, interpreter/runtime, effective tool configuration, referenced
CA/config files and relevant environment. Wheel inventory uses names, sizes and
modification times; it is not a substitute for delivery hash verification.
Every reuse also checks `pip check`, required imports and Vite/esbuild operation.

Changed inputs or failed health checks trigger installation. The old marker is
removed before installation; failure cannot leave the old configuration marked
ready. Fix the mirror/CA or complete the offline bundle and rerun. Changes to
Python inputs do not require npm reinstallation, and vice versa. If inputs
change during installation, setup refuses to write a success marker; rerun with
stable inputs. Do not run concurrent setup processes against the same workspace
or shared web package directory.

## Validation

`npm run test:launcher` exercises option validation, content-sensitive markers
and credential-free persistence. `tests/containers/launcher_dependencies.py`
prepares real wheels/cache and a packed CLI, then exercises fresh setup, reuse,
failed installs/recovery, actual API/Vite startup, private HTTPS pip/npm mirrors
and trusted/untrusted CAs. Its `exercise` phase must run in a disposable
`--network=none` container with loopback available. Run container validation on
a disposable CI or test host, not on a production Foundry host. The supplied `tests/containers/Dockerfile.launcher`
provides an amd64 Linux/Python 3.11/Node 22 fixture; it does not establish ARM64
compatibility. In an isolated fixture root, place the kit under `source/`, create
`out/`, and provide `certs/ca.pem`, `certs/server.pem`, `certs/server.key`
(SAN `127.0.0.1`, signed by that CA), plus an unrelated `certs/wrong-ca.pem`.
Build the driver image, mount the root at `/fixture`, and run
`python /fixture/source/tests/containers/launcher_dependencies.py prepare --root /fixture`
with connectivity, followed by the same command with `exercise` and Docker
`--network=none`. Each phase writes its own logs/results under `out/`; use a new
fixture root per run and do not run two validations against the same root.
Fixtures are temporary and do not publish a kit
or change a real registry, CA trust store or Foundry instance.

References: [pip configuration](https://pip.pypa.io/en/stable/topics/configuration/),
[npm ci](https://docs.npmjs.com/cli/commands/npm-ci/),
[npm configuration](https://docs.npmjs.com/cli/using-npm/config/).
