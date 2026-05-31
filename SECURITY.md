# Security Policy

CCFoundry Agent Kit is an agent-side SDK and local developer harness. It is not a hosted multi-tenant control plane.

## Reporting a Vulnerability

Please do not open a public issue for an active vulnerability.

Use GitHub private vulnerability reporting when it is enabled for the repository. If it is not enabled yet, contact the maintainers out of band and include:

- the affected package, app, or script
- reproduction steps
- impact and required privileges
- any relevant logs with secrets removed

We will acknowledge reports as quickly as possible, triage severity, and publish fixes with release notes when a public fix is available.

## Supported Security Posture

- `Agent Dev Board` binds to `127.0.0.1` by default.
- Dev Board CORS defaults to localhost origins only.
- LAN access is an explicit development mode and should be used only on trusted networks.
- Remote Foundry URLs should use HTTPS. Plain HTTP for non-loopback hosts requires the explicit `CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP=true` opt-in.
- Automatically discovered host GitHub tokens are forwarded only to trusted Foundry hosts. Custom Foundry hosts require an explicit token or `CCFOUNDRY_TRUSTED_FOUNDRY_HOSTS`.
- Agent bounty proxy endpoints only forward to loopback HTTP agent URLs.
- Native PTY access is disabled by default and requires both `terminal_provider=agent_native` and the `terminal` workspace feature.

See [docs/security.md](docs/security.md) for the full local threat model and hardening checklist.

## Secret Handling

The repository ignores common secret and runtime files, including `.env`, private keys, service account JSON files, Dev Board runtime directories, bootstrap state, and reflection JSONL logs.

Before publishing a branch or release:

```bash
git status --short
rg -n "(api[_-]?key|secret|token|password|BEGIN .*PRIVATE KEY|AKIA)" .
npm audit --audit-level=moderate --prefix apps/agent-dev-board-web
```

Use test-only sentinel values in tests and examples. Never commit real Foundry, GitHub, OpenAI-compatible, Google Cloud, Stripe, or sandbox credentials.
