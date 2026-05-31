from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContextMode(str, Enum):
    DIRECT = "direct"
    INLINE = "inline"


class FoundryLLMManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    needs_gateway: bool = True


class FoundryDashboardManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    soul_visible: bool = True
    soul_editable: bool = True
    model_editable: bool = True
    config_editable: bool = True
    reflection_visible: bool = True
    vault_visible: bool = True


class FoundryInfraManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    heartbeat_managed: bool = True
    reflection_managed: bool = True


class FoundryMCPManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    accepts_foundry_mcp: bool = True
    has_own_mcp: bool = False


class FoundryManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    llm: FoundryLLMManifest = Field(default_factory=FoundryLLMManifest)
    dashboard: FoundryDashboardManifest = Field(default_factory=FoundryDashboardManifest)
    infra: FoundryInfraManifest = Field(default_factory=FoundryInfraManifest)
    mcp: FoundryMCPManifest = Field(default_factory=FoundryMCPManifest)


class SlashCommand(BaseModel):
    model_config = ConfigDict(extra="allow")

    cmd: str
    label: str
    desc: str = ""
    skill_ref: str = ""


class AgentManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    label: str
    version: str = "0.1.0"
    description: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    default_mode: ContextMode = ContextMode.DIRECT
    manifest: FoundryManifest = Field(default_factory=FoundryManifest)
    features: list[str] = Field(default_factory=list)
    loaded_skills: list[str] = Field(default_factory=list)
    slash_commands: list[SlashCommand] = Field(default_factory=list)
    privacy: dict[str, Any] = Field(default_factory=dict)
    billing: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str
    mode: ContextMode = ContextMode.DIRECT
    username: str = "local-user"
    user_id: str = ""
    context: str = ""
    conversation_id: str = ""
    session_id: str = ""
    history: list[dict[str, Any]] = Field(default_factory=list)
    images: list[Any] = Field(default_factory=list)
    files: list[Any] = Field(default_factory=list)
    skill_ref: str = ""
    inline_context: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    reply: str
    notes_update: str = ""
    status: str = "ok"
    command: str = ""
    service_fee: float = 0.0
    service_fee_detail: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def message_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.setdefault("content", self.reply)
        return payload


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str = "ok"
    agent: str
    version: str


# ---------------------------------------------------------------------------
# AP2-inspired payment models
# ---------------------------------------------------------------------------
# Maps AP2's three-layer mandate chain to the Foundry agent labor market:
#   IntentMandate  ≈  Match Policy's payment_terms + budget_contract
#   CartMandate    ≈  Agent's budget_contract (from discovery x_foundry)
#   PaymentMandate ≈  SettlementMandate (Foundry confirms payment)
#
# See: https://ap2-protocol.org/overview/
# See: mandate_signing.py for HMAC signature creation/verification
# ---------------------------------------------------------------------------


class MandateItem(BaseModel):
    """A single line item in a settlement, modeled after AP2 ``PaymentItem``.

    Examples::

        MandateItem(label="llm_tokens", amount=0.08, currency="USD")
        MandateItem(label="sandbox_compute", amount=0.05, currency="USD")
        MandateItem(label="task_reward", amount=10.0, currency="USD")
    """

    model_config = ConfigDict(extra="allow")

    label: str
    amount: float = 0.0
    currency: str = "USD"
    pending: bool = False


class FoundryMandate(BaseModel):
    """AP2-inspired payment mandate for Foundry agent settlements.

    Covers all three mandate types in a single envelope:

    * ``intent``     – Foundry host task brief with budget ceiling
    * ``cart``       – Agent's quote / bid with fee breakdown
    * ``settlement`` – Signed proof of verified payment

    The ``signature`` field contains an HMAC-SHA256 digest computed over
    all other fields using the shared ``AGENT_SECRET``.
    """

    model_config = ConfigDict(extra="allow")

    mandate_id: str = ""
    mandate_type: str = "settlement"  # intent | cart | settlement
    data_key: str = ""
    task_ref: str = ""
    agent_name: str = ""
    amount: float = 0.0
    currency: str = "USD"
    items: list[MandateItem] = Field(default_factory=list)
    payer_identity: str = ""
    payee_identity: str = ""
    signature: str = ""
    signed_at: str = ""
    verified: bool = False


class SettlementRecord(BaseModel):
    """Server-side settlement record stored in ``agent_usage_ledger``.

    Created by Foundry after the ops agent verifies task completion.
    The ``mandate`` field contains the full signed ``FoundryMandate``.
    """

    model_config = ConfigDict(extra="allow")

    agent_name: str = ""
    task_ref: str = ""
    settled_amount: float = 0.0
    currency: str = "USD"
    mandate: FoundryMandate = Field(default_factory=FoundryMandate)
    stripe_payment_intent_id: str = ""
    stripe_transfer_id: str = ""
    status: str = "settled"  # pending | settled | failed | refunded
    settled_at: str = ""


class SettlementNotification(BaseModel):
    """Payload delivered to an agent via ``bootstrap_actions`` when a
    settlement is completed.

    The agent should verify ``mandate.signature`` using its local
    ``AGENT_SECRET`` before trusting the settlement.
    """

    model_config = ConfigDict(extra="allow")

    action_type: str = "task_settled"
    mandate: FoundryMandate = Field(default_factory=FoundryMandate)
    settlement: SettlementRecord = Field(default_factory=SettlementRecord)
    message: str = ""
