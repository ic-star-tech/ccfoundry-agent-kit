"""Delivery behaviour of FoundryPullRuntime.

Requests go through httpx.MockTransport, so retries, status handling and
per-invocation isolation are exercised against real HTTP semantics. Backoff
sleeps are recorded instead of slept.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from ccfoundry_agent_kit.pull_runtime import FoundryPullRuntime


def make_bootstrap() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(foundry_base_url="https://foundry.test", runtime_transport="pull"),
        state=SimpleNamespace(
            env_vars={"AGENT_SECRET": "agent-secret"},
            registered_agent_name="demo-agent",
            registration_status="APPROVED",
            last_error="",
        ),
        manifest=SimpleNamespace(name="demo-agent"),
        _save_state=lambda: None,
    )


def make_runtime(handler, *, run_chat=None, **kwargs):
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    runtime = FoundryPullRuntime(
        bootstrap=make_bootstrap(),
        normalize_payload=lambda payload: payload,
        run_chat=run_chat or AsyncMock(),
        http_transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
        **kwargs,
    )
    return runtime, sleeps


class PullRuntimeRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_retries_transient_status_then_succeeds(self) -> None:
        statuses = [503, 502, 200]
        seen_auth: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_auth.append(request.headers.get("Authorization", ""))
            return httpx.Response(statuses.pop(0), json={"ok": True})

        runtime, sleeps = make_runtime(handler)
        await runtime._complete(7, {"status": "succeeded"})
        self.assertEqual(statuses, [])
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(value == "Bearer agent-secret" for value in seen_auth))

    async def test_complete_retries_connection_errors(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"ok": True})

        runtime, sleeps = make_runtime(handler)
        await runtime._complete(7, {"status": "succeeded"})
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(len(sleeps), 2)

    async def test_client_errors_are_not_retried(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(422, json={"detail": "Completion status must be terminal"})

        runtime, sleeps = make_runtime(handler)
        with self.assertRaises(httpx.HTTPStatusError):
            await runtime._complete(7, {"status": "running"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    async def test_retry_after_header_is_honoured(self) -> None:
        responses = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={})]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        runtime, sleeps = make_runtime(handler)
        await runtime._complete(7, {"status": "succeeded"})
        self.assertEqual(sleeps, [2.0])

    async def test_retries_are_bounded(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(503)

        runtime, sleeps = make_runtime(handler, max_attempts=4)
        with self.assertRaises(httpx.HTTPStatusError):
            await runtime._complete(7, {"status": "succeeded"})
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(sleeps), 3)
        self.assertTrue(all(delay <= runtime.retry_max_delay_seconds for delay in sleeps))

    async def test_events_are_retried_at_most_once(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(500)

        runtime, _ = make_runtime(handler, max_attempts=4)
        with self.assertRaises(httpx.HTTPStatusError):
            await runtime._emit_events(7, [{"event_type": "step", "message": "x"}])
        self.assertEqual(len(calls), 2)

    async def test_claim_is_not_retried(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(503)

        runtime, sleeps = make_runtime(handler)
        result = await runtime.poll_once()
        self.assertEqual(result["status"], "claim_error")
        self.assertEqual(calls, ["/api/agent-runtime/claim"])
        self.assertEqual(sleeps, [])

    async def test_poll_once_isolates_a_failed_invocation(self) -> None:
        calls: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content or b"{}")
            calls.append((request.url.path, body))
            path = request.url.path
            if path == "/api/agent-runtime/claim":
                return httpx.Response(200, json={"items": [
                    {"id": 1, "invocation_type": "chat_turn", "payload": {"agent_payload": {"message": "hi"}}},
                    {"id": 2, "invocation_type": "something_new", "payload": {}},
                ]})
            if path == "/api/agent-runtime/1/complete":
                return httpx.Response(500)
            return httpx.Response(200, json={"ok": True})

        run_chat = AsyncMock(side_effect=RuntimeError("model gateway down"))
        runtime, _ = make_runtime(handler, run_chat=run_chat, max_attempts=2)
        result = await runtime.poll_once()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["failed_invocation_ids"], [1])
        paths = [path for path, _ in calls]
        self.assertEqual(paths.count("/api/agent-runtime/1/complete"), 2)
        self.assertIn("/api/agent-runtime/2/complete", paths)
        second = [body for path, body in calls if path == "/api/agent-runtime/2/complete"][0]
        self.assertEqual(second["status"], "cancelled")
        self.assertIn("pull_runtime_complete_failed: invocation 1", runtime.bootstrap.state.last_error)

    async def test_failure_is_still_completed_when_error_events_fail(self) -> None:
        completes: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content or b"{}")
            if request.url.path.endswith("/events"):
                # Progress events succeed; only the error report after the failure is rejected.
                if any(event.get("event_type") == "error" for event in body.get("events", [])):
                    return httpx.Response(500)
                return httpx.Response(200, json={"ok": True})
            completes.append(body)
            return httpx.Response(200, json={"ok": True})

        run_chat = AsyncMock(side_effect=RuntimeError("boom"))
        runtime, _ = make_runtime(handler, run_chat=run_chat)
        ok = await runtime._process_invocation(
            {"id": 9, "invocation_type": "chat_turn", "payload": {"agent_payload": {"message": "hi"}}}
        )
        self.assertFalse(ok)
        # The error-event report failed, but the invocation was still marked failed.
        self.assertEqual(len(completes), 1)
        self.assertEqual(completes[0]["status"], "failed")
        self.assertEqual(completes[0]["error"], "boom")


if __name__ == "__main__":
    unittest.main()
