"""Registry of available statistical methods."""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

from app.statistics.types import METHOD_CATEGORIES, MethodId

if TYPE_CHECKING:
    from app.statistics.base import StatisticalMethod

METHODSET_VERSION = "stat-methodset-v1"


def _builtin_methods() -> list[StatisticalMethod]:
    from app.statistics.methods import ALL_METHODS

    return ALL_METHODS


class MethodRegistry:
    """Maps method ids to implementations and exposes self-describing metadata."""

    def __init__(self, methods: list[StatisticalMethod] | None = None) -> None:
        self._lock = Lock()
        self._methods = None if methods is None else self._index(methods)

    @staticmethod
    def _index(methods: list[StatisticalMethod]) -> dict[MethodId, StatisticalMethod]:
        indexed: dict[MethodId, StatisticalMethod] = {}
        for m in methods:
            if m.method_id in indexed:
                raise ValueError(f"Duplicate statistical method id: {m.method_id}")
            indexed[m.method_id] = m
        return indexed

    def _loaded(self) -> dict[MethodId, StatisticalMethod]:
        # Publish the complete original method set atomically. Failed imports
        # leave it uninitialized; concurrent first requests cannot see a subset.
        with self._lock:
            if self._methods is None:
                self._methods = self._index(_builtin_methods())
            return self._methods

    def get(self, method_id: MethodId) -> StatisticalMethod:
        return self._loaded()[method_id]

    def has(self, method_id: MethodId) -> bool:
        return method_id in self._loaded()

    def all(self) -> list[StatisticalMethod]:
        return list(self._loaded().values())

    def describe(self) -> list[dict]:
        """Self-describing metadata for the /statistics/methods endpoint."""
        out = []
        for m in self._loaded().values():
            out.append(
                {
                    "method_id": m.method_id.value,
                    "name": m.name,
                    "description": m.description,
                    "category": METHOD_CATEGORIES.get(m.method_id, "other"),
                    "version": m.version,
                    "min_variables": m.min_variables,
                    "max_variables": m.max_variables,
                    "needs_group": m.needs_group,
                    "requires_numeric": m.requires_numeric,
                    "manual_configuration_supported": m.method_id
                    not in {
                        MethodId.EVALUATE_PREDICTIONS,
                        MethodId.EVALUATE_BINARY_PREDICTIONS,
                    },
                }
            )
        return out


METHOD_REGISTRY = MethodRegistry()
