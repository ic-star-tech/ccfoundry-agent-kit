# Contributing

Thanks for helping improve CCFoundry Agent Kit. This repository is intended to stay runnable from source, easy to inspect, and safe to publish.

## Development Setup

Prerequisites:

- Python 3.10 or newer
- Node.js 18 or newer, with Node.js 20 recommended

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e packages/python-sdk
pip install -e examples/me_agent
pip install -e apps/agent-dev-board-api

cd apps/agent-dev-board-web
npm ci
```

The one-command local harness is:

```bash
npm run dev-board
```

## Checks

Run the focused Python tests:

```bash
PYTHONPATH=packages/python-sdk/src python -m pytest packages/python-sdk/tests
PYTHONPATH=packages/python-sdk/src:apps/agent-dev-board-api/src python -m pytest apps/agent-dev-board-api/tests
```

Run the web checks:

```bash
cd apps/agent-dev-board-web
npm audit --audit-level=moderate
npm run build
```

## Pull Request Checklist

- Keep changes scoped to the SDK, example, Dev Board API, Dev Board Web, docs, or scripts touched by the issue.
- Add or update tests when changing request handling, auth, URL validation, file operations, or signing behavior.
- Update docs for new environment variables, runtime behavior, or user-visible workflows.
- Do not commit generated runtime directories, local package tarballs, local logs, `.env` files, service account keys, bootstrap state, or credentials.
- Verify `git status --short` before asking for review.

## Security Expectations

Treat local development surfaces as privileged. Dev Board can start local processes, read local Git/GitHub context, proxy agent requests, and launch Cloud Run deployments. Keep the default loopback-only posture unless a change has a clear reason to widen access.

See [SECURITY.md](SECURITY.md) and [docs/security.md](docs/security.md).
