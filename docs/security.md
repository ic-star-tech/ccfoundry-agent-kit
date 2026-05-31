# Security

This document describes the security posture of the open-source agent kit and the local Agent Dev Board. It is written for contributors and users running the repository on their own machines.

## Scope

This repository provides:

- an agent-side Python SDK
- example agents
- a local Dev Board API and browser UI
- deployment helpers for Google Cloud Run

It does not provide a production multi-tenant control plane, RBAC system, billing authority, or hosted secret manager. Those responsibilities belong outside this repository.

## Local Dev Board Threat Model

Agent Dev Board is a privileged local development tool. Its API can:

- create, start, stop, and retire local agent processes
- read local Git and GitHub identity context
- proxy chat and bounty requests to local agents
- request Foundry bootstrap tickets
- start Google Cloud Run deployment jobs using the host's `gcloud` and Docker credentials

For that reason, the default launcher binds both services to `127.0.0.1`.

## Network Defaults

The Dev Board API defaults to localhost-only CORS:

```text
http://localhost:<port>
http://127.x.x.x:<port>
http://[::1]:<port>
```

LAN access is explicit:

```bash
npm run dev-board:lan
# or
ccfoundry dev-board --host 0.0.0.0
```

When running on LAN, use only trusted networks. To allow a specific browser origin, set:

```bash
CCFOUNDRY_DEV_BOARD_ALLOWED_ORIGINS=http://192.168.1.10:3000
```

For multiple exact origins, use a comma-separated list. For advanced cases:

```bash
CCFOUNDRY_DEV_BOARD_ALLOWED_ORIGIN_REGEX='^https?://(localhost|192\.168\.1\.[0-9]+)(:[0-9]+)?$'
```

## URL Handling

Foundry URLs must be HTTP or HTTPS. Hostnames without a scheme default to HTTPS unless the host is loopback.

Remote plain HTTP is blocked by default. Use it only for a trusted non-production Foundry endpoint:

```bash
CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP=true
```

Dev Board may discover the host user's GitHub token from `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token` for the hosted Foundry onboarding flow. It only auto-forwards that discovered token to the default trusted hosts:

- `foundry.cochiper.com`
- `foundry.cochiper.ai`

For a custom Foundry host, pass an explicit developer/GitHub token in the UI or add a trusted host override:

```bash
CCFOUNDRY_TRUSTED_FOUNDRY_HOSTS=foundry.dev.example.com
```

The Dev Board bounty proxy only forwards to loopback HTTP agent URLs such as `http://127.0.0.1:8085`.

## Secret Handling

Never commit real credentials. The repository ignores:

- `.env` and `.env.*`, except `.env.example`
- private keys and PEM files
- Google service account and client secret JSON files
- `.dev-board/`
- local agent bootstrap state files
- local runtime logs and reflection JSONL files
- local npm package tarballs

Bootstrap state intentionally persists approved runtime secrets locally so an agent can restart. Keep those files out of Git and out of support bundles.

## Native Terminal

Native PTY support is disabled by default. It is only enabled when the agent is configured with:

- `terminal_provider = agent_native`
- `workspace_features` containing `terminal`

When enabled, PTY access requires the approved agent secret in the websocket authorization header. Prefer Foundry sandbox workspaces for task execution.

## Cloud Run

The Cloud Run flow uses the host's `gcloud` credentials. It does not require service account key files in this repository. Prefer user login, workload identity, or an attached service account over checked-in JSON keys.

Before sharing deployment logs, remove:

- project numbers and service account emails if they are sensitive in your environment
- authorization codes
- access tokens
- Foundry bootstrap claims
- agent secrets

## Pre-Publish Checklist

```bash
git status --short
rg -n "(api[_-]?key|secret|token|password|BEGIN .*PRIVATE KEY|AKIA)" .
PYTHONPATH=packages/python-sdk/src python -m pytest packages/python-sdk/tests
PYTHONPATH=packages/python-sdk/src:apps/agent-dev-board-api/src python -m pytest apps/agent-dev-board-api/tests
npm audit --audit-level=moderate --prefix apps/agent-dev-board-web
npm run build --prefix apps/agent-dev-board-web
```
