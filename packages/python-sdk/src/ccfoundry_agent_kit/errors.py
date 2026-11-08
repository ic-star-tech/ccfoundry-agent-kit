"""Structured errors for HTTP calls from the agent SDK to Foundry.

Every non-2xx Foundry response becomes a :class:`FoundryHTTPStatusError` subclass that
carries the method, path, status code and a short response excerpt, so callers can tell
an expired invite (HTTP 410) from an auth problem (401/403) or a server outage (5xx).
Status errors also subclass ``httpx.HTTPStatusError``, so existing
``except httpx.HTTPStatusError`` handlers and ``exc.response.status_code`` keep working.
Transport failures (DNS, refused connections, timeouts) are still raised as ``httpx``
transport errors.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

RESPONSE_EXCERPT_CHARS = 300


class FoundryAPIError(RuntimeError):
    """A call from the SDK to Foundry failed."""

    def __init__(
        self,
        message: str,
        *,
        method: str,
        path: str,
        status_code: int | None = None,
        response_excerpt: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.path = path
        self.status_code = status_code
        self.response_excerpt = response_excerpt
        self.retry_after = retry_after

    def summary(self, *, excerpt_chars: int = 120) -> str:
        """Short one-line description suitable for logs and ``last_error``."""
        status = f"HTTP {self.status_code}" if self.status_code is not None else "no response"
        text = f"{status} {self.method} {self.path}"
        excerpt = self.response_excerpt[:excerpt_chars]
        return f"{text}: {excerpt}" if excerpt else text


class FoundryHTTPStatusError(FoundryAPIError, httpx.HTTPStatusError):
    """Foundry answered with a non-2xx status."""

    def __init__(
        self,
        message: str,
        *,
        method: str,
        path: str,
        status_code: int,
        request: httpx.Request,
        response: httpx.Response,
        response_excerpt: str = "",
        retry_after: float | None = None,
    ) -> None:
        FoundryAPIError.__init__(
            self,
            message,
            method=method,
            path=path,
            status_code=status_code,
            response_excerpt=response_excerpt,
            retry_after=retry_after,
        )
        self.request = request
        self.response = response


class FoundryAuthError(FoundryHTTPStatusError):
    """HTTP 401 or 403: missing, expired or revoked credentials."""


class FoundryNotFoundError(FoundryHTTPStatusError):
    """HTTP 404."""


class FoundryRateLimitError(FoundryHTTPStatusError):
    """HTTP 429; ``retry_after`` holds the Retry-After seconds when Foundry sent them."""


class FoundryServerError(FoundryHTTPStatusError):
    """HTTP 5xx."""


def _error_class(status_code: int) -> type[FoundryHTTPStatusError]:
    if status_code in (401, 403):
        return FoundryAuthError
    if status_code == 404:
        return FoundryNotFoundError
    if status_code == 429:
        return FoundryRateLimitError
    if status_code >= 500:
        return FoundryServerError
    return FoundryHTTPStatusError


def _response_excerpt(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail", payload.get("error"))
        if isinstance(detail, str):
            text = detail
        elif detail is not None:
            text = json.dumps(detail, ensure_ascii=False)
        else:
            text = json.dumps(payload, ensure_ascii=False)
    else:
        try:
            text = response.text
        except Exception:
            text = ""
    return " ".join(str(text).split())[:RESPONSE_EXCERPT_CHARS]


def _retry_after(response: httpx.Response) -> float | None:
    raw = str(response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def foundry_error_from_response(
    response: httpx.Response,
    *,
    method: str | None = None,
    path: str | None = None,
) -> FoundryHTTPStatusError:
    request = response.request
    method = method or request.method
    path = path or request.url.path
    status_code = response.status_code
    excerpt = _response_excerpt(response)
    message = f"Foundry {method} {path} returned HTTP {status_code}"
    if excerpt:
        message = f"{message}: {excerpt}"
    return _error_class(status_code)(
        message,
        method=method,
        path=path,
        status_code=status_code,
        request=request,
        response=response,
        response_excerpt=excerpt,
        retry_after=_retry_after(response),
    )


def raise_for_foundry_status(
    response: httpx.Response,
    *,
    method: str | None = None,
    path: str | None = None,
) -> None:
    """Drop-in replacement for ``response.raise_for_status()`` on Foundry calls."""
    if response.is_success:
        return
    raise foundry_error_from_response(response, method=method, path=path)


def error_summary(code: str, exc: BaseException) -> str:
    """``code`` stays a stable prefix; the rest says what failed without payload content."""
    if isinstance(exc, FoundryAPIError):
        return f"{code}: {exc.summary()}"
    return f"{code}: {exc.__class__.__name__}"
