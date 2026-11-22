"""Safe formal provider requests. Clients cannot provide price attestations."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.output_length import normalize_output_settings
from app.research.egress import DisclosureSelection
from app.research.security_contracts import PriceSnapshot


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    provider: str
    model: str
    pricing_mode: Literal["off", "estimate"] = "off"
    input_per_million: str | None = None
    output_per_million: str | None = None
    currency: str = "USD"
    output_length_mode: Literal["auto", "manual"] = "auto"
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_input_bytes: int = Field(default=32000, ge=1024, le=128000)
    timeout_ms: int = Field(default=45000, ge=1000, le=60000)
    price_ceiling_confirmed: bool = False
    billing_limit_acknowledged: bool = False

    @model_validator(mode="before")
    @classmethod
    def output_defaults(cls, value):
        value = normalize_output_settings(value)
        if isinstance(value, dict):
            value = dict(value)
            if "pricing_mode" not in value:
                value["pricing_mode"] = (
                    "estimate"
                    if value.get("input_per_million") and value.get("output_per_million")
                    else "off"
                )
            if value["pricing_mode"] == "off":
                value["input_per_million"] = value["output_per_million"] = None
        return value

    @model_validator(mode="after")
    def manual_output(self):
        if self.output_length_mode == "manual" and self.max_output_tokens is None:
            raise ValueError("Enter a manual output limit.")
        return self

    def unverified_price(self, fingerprint: str) -> PriceSnapshot:
        return PriceSnapshot.model_validate(
            {
                "provider": self.provider,
                "model": self.model,
                "currency": self.currency,
                "input_per_million": self.input_per_million,
                "output_per_million": self.output_per_million,
                "reference_sha256": fingerprint,
                "verified_at_ms": None,
                "synthetic": False,
                "pricing_mode": "estimated" if self.pricing_mode == "estimate" else "unpriced",
            }
        )


class ProviderSave(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    expected_revision: int = Field(ge=0)
    settings: ProviderSettings
    key: SecretStr | None = None
    source_configuration_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    new_connection: bool = False
    display_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("source_configuration_id")
    @classmethod
    def source_identifier(cls, value: str | None) -> str | None:
        if value is not None and str(uuid.UUID(value)) != value:
            raise ValueError("Invalid configuration identifier.")
        return value


class ProviderDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    expected_revision: int = Field(ge=0)


class ProviderConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    provider: Literal["deepseek", "mimo", "glm", "opencode_zen"]
    model: str = Field(min_length=1, max_length=200)
    key: SecretStr | None = None
    configuration_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    expected_revision: int = Field(default=0, ge=0)
    timeout_ms: int = Field(default=15000, ge=1000, le=60000)

    @field_validator("model")
    @classmethod
    def selected_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model is required.")
        return value.strip()


class ProviderToggle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    expected_revision: int = Field(ge=0)
    enabled: bool


class ProviderRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    limit: int = Field(default=20, ge=1, le=20)


class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    proposal_key: str = Field(min_length=1, max_length=200)
    selection: DisclosureSelection


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "deny", "revoke"]
    selection: DisclosureSelection | None = None


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    approval_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    call_key: str = Field(min_length=1, max_length=200)
    selection: DisclosureSelection

    @field_validator("approval_id")
    @classmethod
    def canonical_identifier(cls, value: str) -> str:
        if str(uuid.UUID(value)) != value:
            raise ValueError("Invalid approval identifier.")
        return value
