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
