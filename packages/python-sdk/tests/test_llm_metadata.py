import unittest

from ccfoundry_agent_kit.llm_metadata import foundry_llm_metadata
from ccfoundry_agent_kit.models import BillingContext


class FoundryLLMMetadataTests(unittest.TestCase):
    def test_foundry_llm_metadata_flattens_billing_context(self) -> None:
        metadata = foundry_llm_metadata(
            BillingContext(invocation_id=42, requirement_id="req-1", job_name="FIFO"),
            agent_name="verilog-module-writer",
            extra={"purpose": "bounty_generate"},
        )

        self.assertEqual(metadata["purpose"], "bounty_generate")
        self.assertEqual(metadata["foundry_invocation_id"], 42)
        self.assertEqual(metadata["foundry_requirement_id"], "req-1")
        self.assertEqual(metadata["foundry_agent_name"], "verilog-module-writer")
        self.assertEqual(metadata["billing_context"]["invocation_id"], 42)
        self.assertEqual(metadata["billing_context"]["requirement_id"], "req-1")
        self.assertEqual(metadata["billing_context"]["job_name"], "FIFO")

    def test_foundry_llm_metadata_ignores_empty_context(self) -> None:
        self.assertEqual(foundry_llm_metadata({}), {})


if __name__ == "__main__":
    unittest.main()

