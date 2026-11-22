"""Explicit, bounded local configuration and per-turn disclosure contracts."""

import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.output_length import normalize_output_settings
from app.schemas.material import CitationReference
from app.schemas.ml import MLExperimentCreateRequest
from app.schemas.statistics import StatisticalRunRequest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderConfig(StrictModel):
    provider: Literal["deepseek", "mimo", "glm", "opencode_zen"]
    model: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:/-]+$")
    currency: Literal["USD", "CNY"] = "USD"
    pricing_mode: Literal["off", "estimate"] = "off"
    input_price_per_million: Decimal | None = Field(default=None, gt=0, le=10000, decimal_places=8)
    output_price_per_million: Decimal | None = Field(default=None, gt=0, le=10000, decimal_places=8)
    budget: Decimal | None = Field(default=None, gt=0, le=1000, decimal_places=8)
    output_length_mode: Literal["auto", "manual"] = "auto"
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_input_bytes: int = Field(default=32000, ge=1024, le=128000)
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
                    if value.get("input_price_per_million")
                    and value.get("output_price_per_million")
                    else "off"
                )
            if value["pricing_mode"] == "off":
                value["input_price_per_million"] = value["output_price_per_million"] = value[
                    "budget"
                ] = None
        return value

    @model_validator(mode="after")
    def manual_output(self):
        if self.pricing_mode == "estimate" and (
            self.input_price_per_million is None or self.output_price_per_million is None
        ):
            raise ValueError("Both estimation rates are required when enabled.")
        if self.output_length_mode == "manual" and self.max_output_tokens is None:
            raise ValueError("Enter a manual output limit.")
        return self


class SaveProviderConfig(ProviderConfig):
    key: SecretStr | None = None


class StatisticalForm(StatisticalRunRequest):
    model_config = ConfigDict(extra="forbid")


class MLForm(MLExperimentCreateRequest):
    model_config = ConfigDict(extra="forbid")


class PreviewScope(StrictModel):
    project_id: uuid.UUID
    dataset_id: uuid.UUID | None = None
    dataset_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    schema_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    columns: list[str] = Field(default_factory=list, max_length=1024)
    statistical_run_id: uuid.UUID | None = None
    ml_run_id: uuid.UUID | None = None
    statistical_config: StatisticalForm | None = None
    ml_config: MLForm | None = None
    material_references: list[CitationReference] = Field(default_factory=list, max_length=5)
    session_id: uuid.UUID | None = None
    session_revision: int | None = Field(default=None, ge=1)


class TurnRequest(StrictModel):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    scope: PreviewScope
    question: str = Field(min_length=1, max_length=4000)
    config_revision: int = Field(ge=1)
    disclosure_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_categories: list[
        Literal["project", "schema", "quality", "results", "form", "materials", "history", "rules"]
    ]
    approve_question: bool = False
    approve_tool_summaries: bool = False
    local_auto_execute: bool = False
