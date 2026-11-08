"""Dependency-free entry point copied into customer AgentService images."""

from __future__ import annotations

import hmac
import importlib
import json
import os
import sys
import shutil
from pathlib import Path


class ProjectAuth:
    def __init__(self, app, secret: str, debug_secret: str = "", state_path: str = ""):
        if not secret:
            raise RuntimeError(
                "AGENT_SECRET is required for customer AgentService images"
            )
        self.app, self.secret, self.debug_secret, self.state_path = (
            app,
            secret,
            debug_secret,
            state_path,
        )

    def live_secret(self) -> str:
        if self.debug_secret and self.state_path:
            try:
                path = Path(self.state_path)
                if path.stat().st_size <= 2 * 1024 * 1024:
                    state = json.loads(path.read_text())
                    values = state.get("env_vars", {})
                    if "AGENT_SECRET" in values:
                        return str(values["AGENT_SECRET"] or "").strip()
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        return self.secret

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        reads = {"/health"}
        if self.debug_secret:
            reads |= {
                "/manifest",
                "/.well-known/agent-card.json",
                "/foundry/bootstrap/state",
            }
        public = (
            scope["type"] == "http"
            and scope.get("method") in {"GET", "HEAD"}
            and path in reads
        )
        values = [
            v for k, v in scope.get("headers", []) if k.lower() == b"authorization"
        ]
        token = values[0] if len(values) == 1 else b""
        secret = self.live_secret()
        debug = bool(self.debug_secret) and hmac.compare_digest(
            token, ("Bearer " + self.debug_secret).encode()
        )
        authorized = bool(secret) and hmac.compare_digest(
            token, ("Bearer " + secret).encode()
        )
        if public or debug or authorized:
            if debug:
                # Dev Board remains usable after SDK bootstrap rotates the
                # runtime credential. The inner SDK sees its current secret.
                scope = dict(scope)
                scope["headers"] = [
                    (k, v)
                    for k, v in scope.get("headers", [])
                    if k.lower() != b"authorization"
                ]
                if secret:
                    scope["headers"].append(
                        (b"authorization", ("Bearer " + secret).encode())
                    )
                if path == "/foundry/bootstrap/developer-claim":
                    scope["client"] = ("127.0.0.1", 0)
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 1008})
        else:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send(
                {"type": "http.response.body", "body": b'{"detail":"Unauthorized"}'}
            )


def create_app():
    secret = os.environ.get("AGENT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("AGENT_SECRET is required for customer AgentService images")
    state = Path(os.environ.get("CCFOUNDRY_AGENT_BASE_DIR", "/app/state"))
    seed = Path(os.environ.get("CCFOUNDRY_PROJECT_SEED", "/app/agent_space.seed"))
    state.mkdir(parents=True, exist_ok=True)
    if not (state / "agent_space").exists():
        shutil.copytree(seed, state / "agent_space")
    (state / "workspace").mkdir(exist_ok=True)
    config = json.loads(
        Path(
            os.getenv("CCFOUNDRY_PROJECT_CONFIG", "/app/project-runtime.json")
        ).read_text()
    )
    project_root = Path(os.getenv("CCFOUNDRY_PROJECT_ROOT", "/app/project"))
    sys.path.insert(0, str(project_root / config["app_dir"]))
    module, name = config["app_module"].split(":")
    app = getattr(importlib.import_module(module), name)
    return ProjectAuth(
        app,
        secret,
        os.getenv("CCFOUNDRY_DEBUG_SECRET", ""),
        os.getenv("FOUNDRY_BOOTSTRAP_STATE_PATH", ""),
    )
