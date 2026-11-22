"""AI statistical assistant — provider gateway + structured review pipeline.

Every AI call produces a Pydantic-validated structured output; MockProvider is
used in all automated tests so no real API key is consumed by default.

Key principles:
* AI only interprets/drafts/explains — it never modifies analysis parameters
  or makes numeric decisions.
* Structured output with Pydantic validation; fallback to graceful errored
  result on parse failure.
* All calls logged (provider/model/prompt/tokens/latency) with hashed inputs;
  API key content is never persisted.
"""

from app.ai.provider import (
    LLMProvider,
    MockProvider,
    ModelSettings,
    OpenAICompatibleProvider,
    build_provider,
)

__all__ = [
    "LLMProvider",
    "MockProvider",
    "OpenAICompatibleProvider",
    "ModelSettings",
    "build_provider",
]
