"""Tests for mandate_signing module — AP2-inspired payment mandates."""

from __future__ import annotations

from ccfoundry_agent_kit.mandate_signing import (
    CART_MANDATE_KEY,
    INTENT_MANDATE_KEY,
    SETTLEMENT_MANDATE_KEY,
    create_cart_mandate,
    create_intent_mandate,
    create_settlement_mandate,
    sign_mandate,
    verify_mandate,
)
from ccfoundry_agent_kit.models import (
    FoundryMandate,
    MandateItem,
    SettlementNotification,
    SettlementRecord,
)

TEST_SECRET = "sk-foundry-test-secret-for-unit-tests-only"


class TestSignAndVerify:
    def test_sign_and_verify_round_trip(self) -> None:
        data = {"agent_name": "test_agent", "amount": 42.0, "currency": "USD"}
        sig = sign_mandate(data, TEST_SECRET)
        assert isinstance(sig, str)
        assert len(sig) == 64  # hex SHA-256
        assert verify_mandate(data, sig, TEST_SECRET)

    def test_wrong_secret_fails(self) -> None:
        data = {"agent_name": "test_agent", "amount": 42.0}
        sig = sign_mandate(data, TEST_SECRET)
        assert not verify_mandate(data, sig, "wrong-secret")

    def test_tampered_data_fails(self) -> None:
        data = {"agent_name": "test_agent", "amount": 42.0}
        sig = sign_mandate(data, TEST_SECRET)
        tampered = {**data, "amount": 999.0}
        assert not verify_mandate(tampered, sig, TEST_SECRET)

    def test_signature_field_excluded_from_payload(self) -> None:
        data = {"agent_name": "test_agent", "amount": 42.0, "signature": "old_sig"}
        sig = sign_mandate(data, TEST_SECRET)
        # Re-signing the same data (without old sig) should match
        data_no_sig = {"agent_name": "test_agent", "amount": 42.0}
        assert sig == sign_mandate(data_no_sig, TEST_SECRET)


class TestIntentMandate:
    def test_creates_valid_intent(self) -> None:
        mandate = create_intent_mandate(
            task_description="Write a sorting algorithm",
            budget_max=50.0,
            currency="USD",
            payer_identity="foundry:admin",
            secret=TEST_SECRET,
        )
        assert mandate["mandate_type"] == "intent"
        assert mandate["data_key"] == INTENT_MANDATE_KEY
        assert mandate["budget_max"] == 50.0
        assert mandate["mandate_id"].startswith("intent-")
        assert mandate["signature"]
        assert verify_mandate(mandate, mandate["signature"], TEST_SECRET)


class TestCartMandate:
    def test_creates_valid_cart(self) -> None:
        mandate = create_cart_mandate(
            intent_mandate_id="intent-abc123",
            agent_name="coding_agent",
            proposed_fee=20.0,
            fee_breakdown=[
                {"label": "base_fee", "amount": 5.0, "currency": "USD"},
                {"label": "per_action", "amount": 15.0, "currency": "USD"},
            ],
            skills_offered=["python", "algorithms"],
            secret=TEST_SECRET,
        )
        assert mandate["mandate_type"] == "cart"
        assert mandate["data_key"] == CART_MANDATE_KEY
        assert mandate["proposed_fee"] == 20.0
        assert len(mandate["fee_breakdown"]) == 2
        assert verify_mandate(mandate, mandate["signature"], TEST_SECRET)


class TestSettlementMandate:
    def test_creates_valid_settlement(self) -> None:
        mandate = create_settlement_mandate(
            task_ref="task-001",
            agent_name="coding_agent",
            amount=18.50,
            payer_identity="foundry:admin",
            payee_identity="github:developer123",
            items=[
                {"label": "llm_tokens", "amount": 0.08, "currency": "USD"},
                {"label": "sandbox_compute", "amount": 0.42, "currency": "USD"},
                {"label": "task_reward", "amount": 18.0, "currency": "USD"},
            ],
            secret=TEST_SECRET,
        )
        assert mandate["mandate_type"] == "settlement"
        assert mandate["data_key"] == SETTLEMENT_MANDATE_KEY
        assert mandate["amount"] == 18.50
        assert mandate["mandate_id"].startswith("settle-")
        assert len(mandate["items"]) == 3
        assert verify_mandate(mandate, mandate["signature"], TEST_SECRET)

    def test_settlement_with_stripe_metadata(self) -> None:
        """Extra fields (like Stripe IDs) are preserved in the mandate."""
        mandate = create_settlement_mandate(
            task_ref="task-002",
            agent_name="coding_agent",
            amount=25.0,
            secret=TEST_SECRET,
            stripe_payment_intent_id="pi_test_abc123",
            stripe_transfer_id="tr_test_def456",
        )
        assert mandate["stripe_payment_intent_id"] == "pi_test_abc123"
        assert mandate["stripe_transfer_id"] == "tr_test_def456"
        assert verify_mandate(mandate, mandate["signature"], TEST_SECRET)


class TestMandateChain:
    """Test the full Intent → Cart → Settlement mandate chain."""

    def test_full_chain(self) -> None:
        # Step 1: Foundry host creates an Intent Mandate
        intent = create_intent_mandate(
            task_description="Implement binary search tree",
            budget_max=50.0,
            payer_identity="foundry:admin",
            resource_offer={"sandbox": True, "llm": "gemini-2.5-pro"},
            secret=TEST_SECRET,
        )
        assert verify_mandate(intent, intent["signature"], TEST_SECRET)

        # Step 2: Agent creates a Cart Mandate (bid/quote)
        cart = create_cart_mandate(
            intent_mandate_id=intent["mandate_id"],
            agent_name="tree_builder_agent",
            proposed_fee=20.0,
            fee_breakdown=[
                {"label": "coding", "amount": 15.0},
                {"label": "testing", "amount": 5.0},
            ],
            skills_offered=["python", "data-structures"],
            secret=TEST_SECRET,
        )
        assert verify_mandate(cart, cart["signature"], TEST_SECRET)
        assert cart["intent_mandate_id"] == intent["mandate_id"]

        # Step 3: Foundry settles after verification
        settlement = create_settlement_mandate(
            task_ref="task-bst-001",
            agent_name="tree_builder_agent",
            amount=18.50,
            payer_identity="foundry:admin",
            payee_identity="github:tree_dev",
            items=[
                {"label": "llm_usage", "amount": 0.50},
                {"label": "sandbox_time", "amount": 0.0},
                {"label": "task_reward", "amount": 18.0},
            ],
            secret=TEST_SECRET,
        )
        assert verify_mandate(settlement, settlement["signature"], TEST_SECRET)

        # Verify the chain is independently verifiable
        assert intent["mandate_type"] == "intent"
        assert cart["mandate_type"] == "cart"
        assert settlement["mandate_type"] == "settlement"


class TestPydanticModels:
    """Test the Pydantic model layer."""

    def test_mandate_item(self) -> None:
        item = MandateItem(label="llm_tokens", amount=0.08)
        assert item.label == "llm_tokens"
        assert item.currency == "USD"
        assert not item.pending

    def test_foundry_mandate(self) -> None:
        mandate = FoundryMandate(
            mandate_id="settle-abc123",
            mandate_type="settlement",
            task_ref="task-001",
            agent_name="test_agent",
            amount=42.0,
            items=[MandateItem(label="reward", amount=42.0)],
            signature="deadbeef",
        )
        assert mandate.mandate_type == "settlement"
        assert len(mandate.items) == 1

    def test_settlement_record(self) -> None:
        record = SettlementRecord(
            agent_name="test_agent",
            task_ref="task-001",
            settled_amount=42.0,
            stripe_payment_intent_id="pi_test_123",
        )
        assert record.status == "settled"
        assert record.stripe_payment_intent_id == "pi_test_123"

    def test_settlement_notification(self) -> None:
        notification = SettlementNotification(
            mandate=FoundryMandate(
                mandate_id="settle-abc",
                amount=42.0,
                agent_name="test_agent",
            ),
            message="Task completed and settled!",
        )
        assert notification.action_type == "task_settled"
        assert notification.mandate.amount == 42.0
