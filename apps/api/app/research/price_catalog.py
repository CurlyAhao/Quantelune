"""Boot-only operator pricing configuration; never supplied by an HTTP client or model.

The installation operator places research-price-catalog.json at APP_DATA_PATH.
It contains public reviewed pricing only, never credentials. Missing, invalid or
expired configuration keeps network execution unavailable. Each active provider
revision is bound by the server; client price acknowledgements are not evidence.
"""

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.exceptions import StatForgeError
from app.research.egress import PriceEvidence, credentials, require_network_price
from app.research.security_contracts import CallSpecification, PriceSnapshot

FILENAME = "research-price-catalog.json"
MAX_BYTES = 64 * 1024


class CatalogUnavailableError(ValueError):
    """Only fixed reason codes may escape the parser, never input contents."""


def _object_pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in rows:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _ordinary(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("link")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink, info.st_mode


@dataclass(frozen=True)
class PriceCatalog:
    profiles: Mapping[tuple[str, str], PriceEvidence]
    sha256: str

    def bind(
        self, unverified: PriceSnapshot, fingerprint: str, *, now_ms: int
    ) -> PriceEvidence | None:
        template = self.profiles.get((unverified.provider, unverified.model))
        if template is None or unverified.synthetic:
            return None
        expected = template.price
        if (
            unverified.currency != expected.currency
            or unverified.input_per_million != expected.input_per_million
            or unverified.output_per_million != expected.output_per_million
        ):
            return None
        evidence = replace(
            template,
            price=unverified.model_copy(
                update={"synthetic": False, "verified_at_ms": template.reviewed_at_ms}
            ),
            config_sha256=fingerprint,
        )
        # Preserve the exact same official-host, expiry, billing and wire checks
        # used immediately before transport. This does not send any request.
        probe = CallSpecification(
            purpose="planning",
            scope="metadata_only",
            payload_sha256="0" * 64,
            config_sha256=fingerprint,
            price=evidence.price,
            input_bytes=1,
        )
        try:
            require_network_price(probe, {fingerprint: evidence}, now_ms=now_ms)
        except StatForgeError:
            return None
        return evidence


def load(root: Path, *, now_ms: int) -> PriceCatalog:
    """Read only one fixed ordinary file beneath the trusted boot configuration root."""
    try:
        path = root / FILENAME
        _ordinary(path)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("type")
        if not 0 < before.st_size <= MAX_BYTES:
            raise ValueError("size")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _identity(before) != _identity(opened) or opened.st_nlink != 1:
                raise ValueError("identity")
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        _ordinary(path)
        if (
            len(raw) != before.st_size
            or _identity(before) != _identity(after)
            or _identity(before) != _identity(path.lstat())
        ):
            raise ValueError("changed")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs)
        if (
            type(document) is not dict
            or set(document) != {"version", "reviewed_at_ms", "valid_until_ms", "profiles"}
            or credentials(document)
        ):
            raise ValueError("document")
        if document["version"] != "server-price-catalog-v1":
            raise ValueError("version")
        reviewed, expires = document["reviewed_at_ms"], document["valid_until_ms"]
        if type(reviewed) is not int or type(expires) is not int:
            raise ValueError("time")
        if not 0 <= reviewed <= now_ms < expires:
            raise ValueError("expired")
        rows = document["profiles"]
        if type(rows) is not list or not 1 <= len(rows) <= 16:
            raise ValueError("profiles")
        profiles: dict[tuple[str, str], PriceEvidence] = {}
        keys = {
            "provider",
            "model",
            "currency",
            "input_per_million",
            "output_per_million",
            "official_source",
            "billing_bound_verified",
            "output_token_field",
            "auth_header",
            "usage_profile",
            "thinking_mode",
        }
        for row in rows:
            if type(row) is not dict or set(row) != keys:
                raise ValueError("profile")
            if (
                row["billing_bound_verified"] is not True
                or row["output_token_field"] not in {"max_tokens", "max_completion_tokens"}
                or row["auth_header"] not in {"authorization", "api-key"}
                or row["usage_profile"] not in {"generic", "deepseek_cached"}
                or row["thinking_mode"] not in {None, "disabled"}
                or type(row["official_source"]) is not str
            ):
                raise ValueError("wire")
            price = PriceSnapshot.model_validate(
                {
                    **{
                        key: row[key]
                        for key in (
                            "provider",
                            "model",
                            "currency",
                            "input_per_million",
                            "output_per_million",
                        )
                    },
                    "reference_sha256": "0" * 64,
                    "verified_at_ms": reviewed,
                    "synthetic": False,
                }
            )
            key = price.provider, price.model
            if key in profiles:
                raise ValueError("duplicate profile")
            profiles[key] = PriceEvidence(
                price=price,
                config_sha256="0" * 64,
                reviewed_at_ms=reviewed,
                valid_until_ms=expires,
                official_source=row["official_source"],
                billing_bound_verified=True,
                output_token_field=row["output_token_field"],
                auth_header=row["auth_header"],
                usage_profile=row["usage_profile"],
                thinking_mode=row["thinking_mode"],
            )
        result = PriceCatalog(MappingProxyType(profiles), hashlib.sha256(raw).hexdigest())
        if any(
            result.bind(value.price, "0" * 64, now_ms=now_ms) is None for value in profiles.values()
        ):
            raise ValueError("qualification")
        return result
    except FileNotFoundError:
        raise CatalogUnavailableError("CATALOG_ABSENT") from None
    except (OSError, ValueError, TypeError, RecursionError):
        raise CatalogUnavailableError("CATALOG_INVALID_OR_EXPIRED") from None
