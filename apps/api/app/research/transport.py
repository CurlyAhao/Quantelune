"""Fixed HTTPS transport. Qualification is checked again at the actual send boundary."""

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Protocol

import httpx

from app.research.contracts import parse_document
from app.research.egress import (
    ENDPOINTS,
    MAX_RESPONSE_BYTES,
    PriceEvidence,
    WireProfile,
    credentials,
    failure,
    require_network_connection,
)
from app.research.security_contracts import CallSpecification


class Transport(Protocol):
    network: bool

    async def send(
        self, specification: CallSpecification, body: bytes, credential: str
    ) -> bytes: ...


class HttpTransport:
    network = True

    def __init__(self, catalog: Mapping[str, PriceEvidence]) -> None:
        self.catalog = catalog

    def guard(
        self, specification: CallSpecification, body: bytes, now_ms: int
    ) -> PriceEvidence | WireProfile:
        evidence = require_network_connection(specification, self.catalog, now_ms=now_ms)
        if (
            not specification.source_binding_sha256
            or len(body) != specification.input_bytes
            or hashlib.sha256(body).hexdigest() != specification.payload_sha256
            or evidence.output_token_field not in {"max_tokens", "max_completion_tokens"}
            or evidence.auth_header not in {"authorization", "api-key"}
            or evidence.usage_profile not in {"generic", "deepseek_cached"}
        ):
            raise failure("SEND_UNQUALIFIED", 503)
        try:
            value = parse_document(body.decode("utf-8"))
            token_field = evidence.output_token_field
            if (
                not isinstance(value, dict)
                or value.get("model") != specification.price.model
                or value.get("stream") is not False
                or token_field is None
                or (
                    bool({"max_tokens", "max_completion_tokens"} & set(value))
                    if specification.max_output_tokens is None
                    else type(value.get(token_field)) is not int
                    or value[token_field] != specification.max_output_tokens
                )
                or ({"max_tokens", "max_completion_tokens"} - {token_field}) & set(value)
                or (
                    evidence.thinking_mode is not None
                    and value.get("thinking") != {"type": evidence.thinking_mode}
                )
            ):
                raise ValueError("Wire/profile mismatch.")
        except (ValueError, TypeError, UnicodeError, KeyError):
            raise failure("SEND_UNQUALIFIED", 503) from None
        return evidence

    async def send(self, specification: CallSpecification, body: bytes, credential: str) -> bytes:
        from app.services.research_security_service import clock_ms

        evidence = self.guard(specification, body, clock_ms())
        if (
            not isinstance(credential, str)
            or not 8 <= len(credential) <= 4096
            or "\r" in credential
            or "\n" in credential
        ):
            raise failure("CREDENTIAL_INVALID", 503)
        if credentials(parse_document(body.decode("utf-8")), (credential,)):
            raise failure("CREDENTIAL_DISCLOSURE", 422)
        header = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        if evidence.auth_header == "authorization":
            header["Authorization"] = "Bearer " + credential
        else:
            header["api-key"] = credential
        try:
            async with asyncio.timeout(specification.timeout_ms / 1000):
                async with httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=specification.timeout_ms / 1000
                ) as client:
                    async with client.stream(
                        "POST",
                        ENDPOINTS[specification.price.provider],
                        headers=header,
                        content=body,
                    ) as response:
                        if response.status_code != 200:
                            raise failure("HTTP_UNCONFIRMED", 502)
                        if (
                            response.headers.get("content-encoding", "identity").lower()
                            != "identity"
                        ):
                            raise failure("RESPONSE_ENCODING", 502)
                        content = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise failure("RESPONSE_LIMIT", 502)
                            content.extend(chunk)
                        return bytes(content)
        except (httpx.HTTPError, TimeoutError):
            raise failure("TRANSPORT_UNKNOWN", 502) from None
