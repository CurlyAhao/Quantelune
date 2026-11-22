"""Pure USD budget units and immutable, server-derived call contracts."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

UNIT = Decimal("0.00000001")
VERSION = "research-security-v1"
APPROVAL_TTL_MS = 600_000
MAX_APPROVAL_TTL_MS = 900_000
SINGLE_CALL_UNITS = 25_000_000
HARD_SINGLE_CALL_UNITS = 50_000_000
INPUT_OVERHEAD = 4096


@dataclass(frozen=True)
class Limits:
    cost: int
    attempts: int
    tokens: int
    milliseconds: int


DEFAULT_LIMITS = {
    "owner": Limits(1_000_000_000, 300, 5_000_000, 7_200_000),
    "project": Limits(500_000_000, 150, 2_000_000, 3_600_000),
    "workspace": Limits(200_000_000, 60, 1_000_000, 1_800_000),
    "run": Limits(100_000_000, 20, 400_000, 600_000),
}


class PriceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    provider: Literal["deepseek", "mimo", "glm", "opencode_zen"]
    model: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:/-]+$")
    currency: Literal["USD"] = "USD"
    input_per_million: str | None = None
    output_per_million: str | None = None
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified_at_ms: int | None = Field(default=None, ge=0)
    synthetic: bool = True
    pricing_mode: Literal["reviewed", "estimated", "unpriced"] = "reviewed"

    @field_validator("input_per_million", "output_per_million")
    @classmethod
    def price(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError("Invalid price ceiling.") from None
        if not number.is_finite() or number <= 0 or number > 10000:
            raise ValueError("Invalid price ceiling.")
        exponent = number.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -8:
            raise ValueError("Price precision exceeds eight decimal places.")
        return format(number, "f")

    @model_validator(mode="after")
    def price_presence(self):
        if self.pricing_mode == "unpriced":
            if self.input_per_million is not None or self.output_per_million is not None:
                raise ValueError("Unpriced calls cannot contain invented rates.")
        elif self.input_per_million is None or self.output_per_million is None:
            raise ValueError("Both rates are needed for cost estimation.")
        return self

    @model_serializer(mode="wrap")
    def serialize_price(self, handler):
        result = handler(self)
        if self.pricing_mode == "reviewed":
            result.pop("pricing_mode", None)
        return result


class CallSpecification(BaseModel):
    """Internal proposal input; a public approval request cannot supply this."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation: Literal["provider.call"] = "provider.call"
    purpose: Literal["planning", "analysis", "review"]
    scope: Literal["metadata_only", "sanitized_sample", "raw_data_approved"]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    price: PriceSnapshot
    input_bytes: int = Field(ge=1, le=128000)
    max_output_tokens: int | None = Field(default=None, ge=1)
    timeout_ms: int = Field(default=45000, ge=1000, le=60000)
    source_binding_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.source_binding_sha256 is None:
            result.pop("source_binding_sha256", None)
        return result


@dataclass(frozen=True)
class Estimate:
    cost: int
    input_tokens: int
    output_tokens: int
    milliseconds: int

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def cost_units(input_tokens: int, output_tokens: int, price: PriceSnapshot) -> int | None:
    if type(input_tokens) is not int or type(output_tokens) is not int:
        raise ValueError("Integer tokens required.")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Negative usage.")
    if price.pricing_mode == "unpriced":
        return None
    amount = (
        Decimal(input_tokens) * Decimal(price.input_per_million)
        + Decimal(output_tokens) * Decimal(price.output_per_million)
    ) / Decimal(1_000_000)
    return int((amount / UNIT).to_integral_value(rounding=ROUND_CEILING))


def estimate(specification: CallSpecification) -> Estimate:
    # Revalidate even an internally constructed Pydantic object.
    spec = CallSpecification.model_validate(specification.model_dump(mode="json"))
    incoming = spec.input_bytes + INPUT_OVERHEAD
    # Auto is an estimate for the existing budget ledger, never a wire limit.
    # Trusted actual usage can expand this reservation during settlement.
    outgoing = spec.max_output_tokens * 4 if spec.max_output_tokens is not None else 65536
    known_cost = cost_units(incoming, outgoing, spec.price)
    # The unchanged legacy journal requires a positive reservation field. For
    # explicitly unpriced calls, 1 is a storage marker, NEVER a monetary amount:
    # it is excluded from cost accounts and public APIs expose null costs.
    return Estimate(
        known_cost if known_cost is not None else 1, incoming, outgoing, spec.timeout_ms
    )


def policy_document() -> dict:
    return {
        "version": VERSION,
        "currency": "USD",
        "cost_unit": "1e-8 USD",
        "single_call_units": SINGLE_CALL_UNITS,
        "hard_single_call_units": HARD_SINGLE_CALL_UNITS,
        "limits": {key: vars(value) for key, value in DEFAULT_LIMITS.items()},
        "input_overhead": INPUT_OVERHEAD,
        "output_multiplier": 4,
        "approval_ttl_ms": APPROVAL_TTL_MS,
        "max_approval_ttl_ms": MAX_APPROVAL_TTL_MS,
    }
