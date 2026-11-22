"""Shared recursive checks for owned credentials in non-key data."""

from typing import Any


def contains_secret(value: Any, secret: str) -> bool:
    """Inspect decoded strings, including JSON keys, without re-escaping them."""
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(
            contains_secret(k, secret) or contains_secret(v, secret) for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_secret(v, secret) for v in value)
    return False
