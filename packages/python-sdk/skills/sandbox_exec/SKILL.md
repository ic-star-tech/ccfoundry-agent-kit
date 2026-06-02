---
name: sandbox_exec
description: Execute commands and manage files in a Foundry sandbox.
slash_command:
  cmd: /sandbox
  label: Sandbox
  desc: Run commands, write files, and manage a Foundry sandbox workspace.
---

# Sandbox Executor Skill

## Trigger
Use this skill when the task requires executing commands in an isolated
sandbox environment, or when managing workspace files.

## Capabilities
- Acquire and release sandbox leases from the Foundry sandbox pool
- Execute shell commands inside the sandbox container
- Write files to the sandbox workspace via API
- Monitor command output and detect success/failure

## API Reference

### Acquire a sandbox lease
```http
POST {SANDBOX_DAEMON_URL}/acquire
Content-Type: application/json
x-sandbox-token: {token}

{"username": "<agent_username>"}
```
Returns: `{"lease_id": "...", "slot": 1, ...}`

### Execute a command
```http
POST {SANDBOX_DAEMON_URL}/leases/{lease_id}/terminal/exec
Content-Type: application/json

{"command": "<shell_command>"}
```
Returns: Terminal output

### Write a file to workspace
```http
PUT {SANDBOX_DAEMON_URL}/users/{username}/workspace/write
Content-Type: application/json
x-sandbox-token: {token}

{"path": "relative/path/to/file.txt", "content": "file content here"}
```

### List workspace files
```http
GET {SANDBOX_DAEMON_URL}/users/{username}/workspace/tree
x-sandbox-token: {token}
```

### Release the sandbox
```http
POST {SANDBOX_DAEMON_URL}/release
Content-Type: application/json

{"lease_id": "<lease_id>"}
```

## Best Practices
- Always release the sandbox lease after use (even on error — use try/finally)
- Wait 3-5 seconds after sandbox acquisition for container initialization
- Use unique markers in commands to detect success: `echo EXIT_CODE=$?`
- Set reasonable timeouts for long-running commands: `timeout 30 <command>`
- Check pool status before acquiring: `GET /pool-status`

## Environment Variables
- `SANDBOX_DAEMON_URL`: Daemon endpoint (default: `http://localhost:9000`)
- `SANDBOX_TOKEN`: Authentication token for the daemon API

## Error Handling
- If lease acquisition fails, the pool may be full — retry after delay
- If command execution times out, the sandbox may need to be released and re-acquired
- Always check HTTP status codes: 200/201 = success, 4xx = client error, 5xx = server error
