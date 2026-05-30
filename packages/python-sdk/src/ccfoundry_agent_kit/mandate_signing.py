"""AP2-inspired mandate signing for Foundry agent settlements.

Implements a simplified version of the AP2 (Agent Payments Protocol) mandate
chain for the Foundry agent labor market. Uses HMAC-SHA256 with the existing
``AGENT_SECRET`` as the shared signing key.

**AP2 Concept Mapping:**

* ``IntentMandate``  ≈  Foundry Match Policy's ``payment_terms`` + ``budget_contract``
* ``CartMandate``    ≈  Agent's ``budget_contract`` (from discovery ``x_foundry``)
* ``PaymentMandate`` ≈  :func:`create_settlement_mandate` output

The symmetric signing model is appropriate here because Foundry and the Agent
share a pre-established trust channel (``AGENT_SECRET`` distributed at
onboarding).  When third-party auditing is needed, the signing module can be
upgraded to asymmetric ECDSA/VDC without changing the mandate data structures.

See also:
    * https://ap2-protocol.org/overview/
    * https://github.com/google-agentic-commerce/AP2
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Data-key namespacing (modeled after AP2's ap2.mandates.* convention)
# ---------------------------------------------------------------------------

INTENT_MANDATE_KEY = "foundry.mandates.IntentMandate"
CART_MANDATE_KEY = "foundry.mandates.CartMandate"
SETTLEMENT_MANDATE_KEY = "foundry.mandates.SettlementMandate"


# ---------------------------------------------------------------------------
# Signing / Verification helpers
# ---------------------------------------------------------------------------

def _canonical_payload(data: dict[str, Any]) -> str:
    """Produce a deterministic JSON serialization for signing."""
    filtered = {k: v for k, v in data.items() if k != "signature"}
    return json.dumps(filtered, sort_keys=True, ensure_ascii=False)


def sign_mandate(mandate_data: dict[str, Any], secret: str) -> str:
    """Create an HMAC-SHA256 signature over *mandate_data* using *secret*.

    The ``signature`` key, if present, is excluded from the payload before
    signing to allow idempotent re-signing.

    Parameters
    ----------
    mandate_data:
        Mandate dictionary to sign. Must be JSON-serializable.
    secret:
        Shared secret (typically the ``AGENT_SECRET``).

    Returns
    -------
    str
        Hex-encoded HMAC-SHA256 digest.
    """
    payload = _canonical_payload(mandate_data)
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_mandate(
    mandate_data: dict[str, Any],
    signature: str,
    secret: str,
) -> bool:
    """Verify that *signature* is valid for *mandate_data*.

    Uses constant-time comparison to prevent timing side-channels.
    """
    expected = sign_mandate(mandate_data, secret)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Mandate builders
# ---------------------------------------------------------------------------

def create_intent_mandate(
    *,
    task_description: str,
    budget_max: float,
    currency: str = "USD",
    payer_identity: str = "",
    resource_offer: dict[str, Any] | None = None,
    secret: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build an Intent Mandate (AP2 equivalent: user's spending intent).

    In the Foundry context this represents the admin's task brief with a
    budget ceiling, roughly equivalent to a ``MatchPolicy``'s
    ``payment_terms`` + ``budget_contract``.
    """
    mandate: dict[str, Any] = {
        "mandate_type": "intent",
        "data_key": INTENT_MANDATE_KEY,
        "mandate_id": f"intent-{uuid.uuid4().hex[:16]}",
        "task_description": task_description,
        "budget_max": budget_max,
        "currency": currency,
        "payer_identity": payer_identity,
        "resource_offer": resource_offer or {},
        "signed_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    mandate["signature"] = sign_mandate(mandate, secret)
    return mandate


def create_cart_mandate(
    *,
    intent_mandate_id: str,
    agent_name: str,
    proposed_fee: float,
    currency: str = "USD",
    fee_breakdown: list[dict[str, Any]] | None = None,
    skills_offered: list[str] | None = None,
    secret: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a Cart Mandate (AP2 equivalent: concrete price lock).

    Represents the agent's quote/bid for a task.  The ``fee_breakdown``
    list follows the AP2 ``PaymentItem`` pattern::

        [{"label": "model_usage", "amount": 0.12, "currency": "USD"}, ...]
    """
    mandate: dict[str, Any] = {
        "mandate_type": "cart",
        "data_key": CART_MANDATE_KEY,
        "mandate_id": f"cart-{uuid.uuid4().hex[:16]}",
        "intent_mandate_id": intent_mandate_id,
        "agent_name": agent_name,
        "proposed_fee": proposed_fee,
        "currency": currency,
        "fee_breakdown": fee_breakdown or [],
        "skills_offered": skills_offered or [],
        "signed_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    mandate["signature"] = sign_mandate(mandate, secret)
    return mandate


def create_settlement_mandate(
    *,
    task_ref: str,
    agent_name: str,
    amount: float,
    currency: str = "USD",
    payer_identity: str = "",
    payee_identity: str = "",
    items: list[dict[str, Any]] | None = None,
    secret: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a Settlement Mandate (AP2 equivalent: PaymentMandate).

    This is the final, signed proof of payment after Foundry has verified
    the agent's work.  The ``items`` list follows AP2's ``PaymentItem``::

        [
            {"label": "llm_tokens",       "amount": 0.08, "currency": "USD"},
            {"label": "sandbox_compute",   "amount": 0.05, "currency": "USD"},
            {"label": "task_reward",       "amount": 10.0, "currency": "USD"},
        ]
    """
    mandate: dict[str, Any] = {
        "mandate_type": "settlement",
        "data_key": SETTLEMENT_MANDATE_KEY,
        "mandate_id": f"settle-{uuid.uuid4().hex[:16]}",
        "task_ref": task_ref,
        "agent_name": agent_name,
        "amount": amount,
        "currency": currency,
        "payer_identity": payer_identity,
        "payee_identity": payee_identity,
        "items": items or [],
        "signed_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    mandate["signature"] = sign_mandate(mandate, secret)
    return mandate
