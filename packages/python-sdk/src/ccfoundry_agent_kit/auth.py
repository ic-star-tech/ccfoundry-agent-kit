"""Bearer authentication for SDK apps, including secrets delivered by bootstrap."""
from __future__ import annotations

import hmac
from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def matches_bearer(scope: Scope, secret: str) -> bool:
    headers = [value for key, value in scope.get("headers", ()) if key.lower() == b"authorization"]
    if not secret or len(headers) != 1:
        return False
    scheme, separator, token = headers[0].partition(b" ")
    return bool(separator and scheme.lower() == b"bearer" and
                hmac.compare_digest(token.strip(), secret.encode("utf-8")))


class AgentSecretAuth:
    """Protect operations once a secret is configured; never reopen after removal.

    None means the local developer has not configured authentication. An empty
    configured value, or required=True without a secret, denies operations.
    Bootstrap callbacks retain their own callback-token/local-caller checks.
    """

    PUBLIC_READS = frozenset({"/health", "/manifest", "/.well-known/agent-card.json", "/foundry/bootstrap/state"})
    BOOTSTRAP_CALLBACKS = frozenset({
        "/foundry/bootstrap/invite", "/foundry/bootstrap/approved", "/foundry/bootstrap/developer-claim",
    })

    def __init__(self, app: ASGIApp, *, get_secret: Callable[[], str | None], required: bool = False,
                 platform_authenticated_poll: bool = False) -> None:
        self.app = app
        self.get_secret = get_secret
        self.required = required or get_secret() is not None
        self.platform_authenticated_poll = platform_authenticated_poll

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        secret = self.get_secret()
        self.required = self.required or secret is not None
        method, path = scope.get("method"), scope.get("path")
        root_path = scope.get("root_path", "")
        if root_path and path and path.startswith(root_path + "/"):
            path = path[len(root_path):]
        public = scope["type"] == "http" and (
            method in {"GET", "HEAD"} and path in self.PUBLIC_READS or
            method == "POST" and path in self.BOOTSTRAP_CALLBACKS or
            method == "POST" and path == "/foundry/poll" and self.platform_authenticated_poll
        )
        if public or not self.required or matches_bearer(scope, secret or ""):
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            response = JSONResponse({"detail": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
