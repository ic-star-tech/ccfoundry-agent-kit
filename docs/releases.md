# SDK releases and Foundry compatibility

The Python distribution is `ccfoundry-agent-kit`; its import is
`ccfoundry_agent_kit`. The npm package `ccfoundry` is a launcher that also carries
Python sources. They are separate artifacts. Source versions are currently
Python `0.1.0` and npm `0.1.5`; this document does not announce a new publication.

## Compatibility status

No Foundry server version range is certified yet. The native server currently
reports application version `2.0.0` for different commits, so a claim such as
“all Foundry 2.x” would not identify the tested protocol. Record the exact SDK
commit, wheel hash, Foundry commit and image digest with each deployment result.

| Surface | Required Foundry contract | Release evidence required |
| --- | --- | --- |
| Discovery and approval | `/api/registry/discover`, register, bootstrap actions and approval delivery | Fresh agent invite, approval and installed secret |
| Resource refresh | Authenticated registry sync and complete allocated-resource snapshots | Grant update and removal against the selected server |
| AgentService | Manifest schema, bearer-authenticated agent chat and JSON/SSE dispatch | Installed package health and both chat modes |
| Sandbox and task execution | Allocated sandbox control-plane descriptor and assignment-bound APIs | Approved sandbox execution with the selected plan |
| MCP tools, when included | Approved MCP resources plus an SDK build containing the runtime | Actual tool/model answer and grant revocation, not just metadata listing |

The artifact smoke test below proves package installation and local SDK behavior;
it does not satisfy those server checks. Python 3.10/3.12 run in CI. POSIX is the
runtime target because the app imports `fcntl`/`pty`; the wheel's universal tag
does not establish Windows, ARM64 dependency availability or offline support.
Complete the intranet workflow separately before claiming it.

## Build a reviewable candidate

Commit the intended source first. The helper rejects dirty tracked files and
builds a `git archive` of that exact HEAD in a temporary directory. Ignored and
untracked worktree files are not copied. Use a new output directory each time:

```bash
python3 -m venv /tmp/ccfoundry-release-tools
/tmp/ccfoundry-release-tools/bin/python -m pip install build twine pytest
/tmp/ccfoundry-release-tools/bin/python scripts/prepare_release.py --output /tmp/ccfoundry-candidate
CCFOUNDRY_RELEASE_ARTIFACTS=/tmp/ccfoundry-candidate \
  /tmp/ccfoundry-release-tools/bin/python -m pytest tests/test_release_artifacts.py -q
```

The helper builds an sdist and a wheel from that sdist, creates the npm tarball
with lifecycle scripts disabled, checks Python metadata with `twine check
--strict`, compares every SDK source file across all three artifacts and checks
license/launcher files. `release-manifest.json` records the source commit,
separate package versions, proposed tags and SHA256 hashes. It always says
`candidate` and `published: false`. The tests install the wheel in a fresh venv,
exercise local ASGI health/chat and run the packaged npm entry point.
They also instantiate both bundled agent templates without starting processes;
the inspector checks every shipped template file against the source snapshot.

The build may download build dependencies; the installation smoke test resolves
runtime dependencies. Standard pip index configuration applies. This is not an
offline bundle. Archive path checks catch common credential/runtime filenames;
they are not a secret-content scan or a public-source review. The npm bundle
contains more code and documentation than the Python wheel: review both.

## Versioning and deprecation

Keep `_version.py` as the Python version source and `package.json` as the npm
version source. Do not infer one from the other. Proposed per-artifact tags are
`python-v<version>` and `npm-v<version>` on the same release commit; the helper
creates neither. A future synchronized-version policy needs a maintainer
decision, version changes and matching changelog entries before publication.

For subsequent releases, document breaking behavior and upgrade steps in the
release entry. Preserve compatible patch releases; schedule incompatible API or
contract changes for a new minor version while the SDK is below 1.0. Announce
deprecations in a dated release and retain the old surface for at least the next
minor release unless an immediate security fix requires removal. Document any
exception with its migration path. There are no newly deprecated APIs here.

## Publication handoff

Before any public upload, a maintainer must select the final source/version
policy, review the complete candidate, and approve the publishing destination.
Existing npm `0.1.5` must not be reused for changed contents. A locally buildable
Python `0.1.0` does not prove the PyPI name is reserved or available when released.
Check both registries again immediately before publication.

The operator needs a PyPI project/token or a configured Trusted Publisher for
the actual publishing CI provider/repository/workflow/environment. Do not assume
an arbitrary CI token is a PyPI Trusted Publisher credential. Configure public npm
access separately. Never put publishing credentials in the repository or pass
them as command-line arguments.

After the release is authorized, create the selected immutable tag(s) on the
reviewed commit, publish only the exact inspected artifacts (for Python, the
manual workflow below does this), then record the
registry URLs/hashes. Avoid wildcard uploads from a reused output directory.
Verify installation from PyPI in a fresh environment with an exact version,
compare the downloaded artifact hash with the candidate, run the installed SDK
smoke test, and repeat the chosen Foundry deployment acceptance. Perform the
equivalent exact-version npm installation. Only then mark the issue's public
installation/tag/compatibility acceptance complete.

## Manual Python release workflow

`.github/workflows/release.yml` ("Python release") runs only on manual dispatch.
It rebuilds the committed candidate with `prepare_release.py`, runs
`tests/test_release_artifacts.py` against it and prints the manifest. With the
default `publish=false` it stops there, so it can be dispatched on any branch to
rehearse a release.

With `publish=true` it additionally runs `scripts/release_publish_guard.py`,
which refuses to continue unless:

- `repository_url` is an explicit `https` URL without embedded credentials,
  query or fragment (there is no default index; public PyPI is
  `https://upload.pypi.org/legacy/`, a private index supplies its own URL);
- the manifest is an unpublished candidate built from the checked-out commit;
- HEAD carries the tag `python-v<sdk_version>`;
- exactly one wheel and one sdist are listed and still match their hashes.

It then passes those two files, by exact path, to `twine upload`. Credentials
come from the repository secrets `PYTHON_INDEX_USERNAME` (`__token__` for a PyPI
API token) and `PYTHON_INDEX_TOKEN` through twine's environment variables, never
from the command line. Without them the step fails before uploading. The
workflow does not create tags, publish npm or change versions; anyone with write
access can dispatch it, so configure the secrets only once publication is
approved and remove or rotate them afterwards if that access should not persist.
The workflow uses an API token rather than PyPI Trusted Publishing so the same
procedure works on CI providers that PyPI does not trust.

References: [PyPA packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/),
[PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/),
[npm pack](https://docs.npmjs.com/cli/v11/commands/npm-pack/).
