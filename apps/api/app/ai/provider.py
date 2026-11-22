"""AI provider abstraction and implementations.

- ``LLMProvider``  — protocol for structured generation calls
- ``OpenAICompatibleProvider`` — generic OpenAI-compatible chat completions endpoint
- ``MockProvider`` — deterministic synthetic responses for explicit test injection
- ``UnavailableProvider`` — stable no-network failure for incomplete configuration
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings


@runtime_checkable
class LLMProvider(Protocol):
    """A structured AI generation backend."""

    @property
    def ready(self) -> bool:
        """Whether this provider has complete local configuration."""
        ...

    async def structured_generate(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[BaseModel | None, dict[str, Any]]:
        """Return (parsed Pydantic instance or None on failure, metadata dict)."""
        ...


@dataclass
class ModelSettings:
    model: str = ""
    temperature: float = 0.3
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class OpenAICompatibleProvider:
    """Generic OpenAI-compatible chat completions via httpx."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = (base_url or settings.ai_base_url or "").rstrip("/")
        key = api_key
        if key is None and settings.ai_api_key is not None:
            key = settings.ai_api_key.get_secret_value()
        self._api_key = key or ""
        self._timeout = timeout

    @property
    def ready(self) -> bool:
        return bool(self._base_url and self._api_key)

    async def structured_generate(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[BaseModel | None, dict[str, Any]]:
        model_name = model or settings.ai_model or ""
        schema_json = response_schema.model_json_schema()
        system_msg = (
            "You are a statistical assistant for the Quantelune workbench. "
            "Respond ONLY with valid JSON matching the requested schema. "
            "Do not include markdown fences or commentary."
        )
        system_msg += (
            f"\nExpected JSON schema:\n{json.dumps(schema_json, indent=2, ensure_ascii=False)}"
        )
        body: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_msg},
                *messages,
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        body.update(extra or {})

        started = time.time()
        meta: dict[str, Any] = {
            "provider": "openai_compatible",
            "model": model_name,
            "input_hash": _hash(json.dumps(messages, ensure_ascii=False)),
            "latency_ms": 0,
            "token_usage": None,
            "error": None,
        }
        try:
            resp = await httpx.AsyncClient(timeout=self._timeout).post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            meta["latency_ms"] = round((time.time() - started) * 1000)
            if resp.status_code != 200:
                meta["error"] = f"HTTP {resp.status_code}: {resp.text[:500]}"
                return None, meta
            data = resp.json()
            usage = data.get("usage")
            meta["token_usage"] = usage
            content = data["choices"][0]["message"]["content"]
            parsed = response_schema.model_validate_json(content)
            return parsed, meta
        except (httpx.RequestError, json.JSONDecodeError, ValidationError, KeyError) as exc:
            meta["latency_ms"] = round((time.time() - started) * 1000)
            meta["error"] = f"{type(exc).__name__}: {exc}"[:500]
            return None, meta


class MockProvider:
    """Deterministic synthetic responses for testing — never calls a real API."""

    @property
    def ready(self) -> bool:
        return True

    async def structured_generate(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[BaseModel | None, dict[str, Any]]:
        _ = messages, model, temperature, max_tokens, extra
        # Build a synthetic instance from the schema's default values / type hints.
        fields = response_schema.model_fields
        values: dict[str, Any] = {}
        for name, field_info in fields.items():
            annotation = field_info.annotation
            origin = getattr(annotation, "__origin__", None)
            args = getattr(annotation, "__args__", ())
            # Provide simple mock values per type.
            if annotation is str:
                values[name] = f"Mock {name}"
            elif origin is list:
                inner = args[0] if args else str
                if hasattr(inner, "model_fields"):
                    values[name] = [{}]
                else:
                    values[name] = [
                        f"mock_{name}_0" if inner is str else (0 if inner in (int, float) else True)
                    ]
            elif annotation is int:
                values[name] = 0
            elif annotation is float:
                values[name] = 0.0
            elif annotation is bool:
                values[name] = False
            elif annotation is dict or getattr(annotation, "__origin__", None) is dict:
                values[name] = {}
            else:
                values[name] = None
        try:
            instance = response_schema(**values)
        except Exception:  # noqa: BLE001
            instance = None
        meta = {
            "provider": "mock",
            "model": "mock",
            "input_hash": "mock",
            "latency_ms": 1,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "error": None,
        }
        return instance, meta


class UnavailableProvider:
    """No-network provider used when the configured AI service is unusable."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    @property
    def ready(self) -> bool:
        return False

    async def structured_generate(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[BaseModel | None, dict[str, Any]]:
        _ = messages, response_schema, model, temperature, max_tokens, extra
        return None, {
            "provider": "unavailable",
            "model": "",
            "input_hash": None,
            "latency_ms": 0,
            "token_usage": None,
            "error": f"AI_NOT_READY: {self._reason}",
        }


def build_provider() -> LLMProvider:
    """Return a configured provider, never an implicit synthetic provider."""
    if not settings.ai_enabled:
        return UnavailableProvider("AI is disabled.")
    if settings.ai_provider != "openai_compatible":
        return UnavailableProvider(f"Unsupported AI provider: {settings.ai_provider}.")
    if not settings.ai_model:
        return UnavailableProvider("AI_MODEL is required.")
    key = None
    if settings.ai_api_key is not None:
        key = settings.ai_api_key.get_secret_value()
    if not key or not settings.ai_base_url:
        return UnavailableProvider("AI_BASE_URL and AI_API_KEY are required.")
    return OpenAICompatibleProvider(base_url=settings.ai_base_url, api_key=key)
