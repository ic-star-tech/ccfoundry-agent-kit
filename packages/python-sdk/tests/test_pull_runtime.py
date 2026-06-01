import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ccfoundry_agent_kit.pull_runtime import FoundryPullRuntime


class FoundryPullRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounty_execute_invocation_merges_payload_and_completes(self) -> None:
        seen_payload: dict = {}

        async def run_bounty(payload: dict) -> dict:
            seen_payload.update(payload)
            return {
                "ok": True,
                "deliverable": {"status": "accepted", "job_id": payload["job_id"]},
                "summary": "accepted",
            }

        runtime = FoundryPullRuntime(
            bootstrap=SimpleNamespace(),
            normalize_payload=lambda payload: payload,
            run_chat=AsyncMock(),
            run_bounty=run_bounty,
        )
        runtime._emit_events = AsyncMock()  # type: ignore[method-assign]
        runtime._complete = AsyncMock()  # type: ignore[method-assign]

        await runtime._process_invocation(
            {
                "id": 42,
                "invocation_type": "bounty_execute",
                "payload": {
                    "foundry_url": "https://foundry.test",
                    "agent_payload": {"job_id": "req-1", "job_name": "RRA"},
                },
            }
        )

        self.assertEqual(seen_payload["foundry_url"], "https://foundry.test")
        self.assertEqual(seen_payload["job_id"], "req-1")
        self.assertEqual(seen_payload["billing_context"]["invocation_id"], 42)
        self.assertEqual(seen_payload["billing_context"]["requirement_id"], "req-1")
        complete_payload = runtime._complete.await_args.args[1]
        self.assertEqual(complete_payload["status"], "succeeded")
        self.assertEqual(complete_payload["metadata"]["billing_context"]["invocation_id"], 42)
        self.assertEqual(complete_payload["metadata"]["deliverable"]["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
