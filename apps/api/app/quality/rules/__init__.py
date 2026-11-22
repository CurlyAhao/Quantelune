"""Rule set assembly: builds the general and optional OHLCV registries."""

from __future__ import annotations

from app.quality.registry import RULESET_VERSION, RuleRegistry
from app.quality.rules.column_rules import COLUMN_RULES
from app.quality.rules.dataset_rules import DATASET_RULES
from app.quality.rules.ohlcv_rules import OHLCV_RULES, detect_ohlcv_mapping
from app.quality.rules.temporal_rules import TEMPORAL_RULES

# Optional OHLCV ruleset version (tracked separately so enabling/disabling the
# domain pack is explicit and reproducible).
OHLCV_RULESET_VERSION = "ohlcv-ruleset-v1"


def build_general_registry() -> RuleRegistry:
    """The always-on rule set (18 deterministic rules)."""
    registry = RuleRegistry()
    for rule in [*DATASET_RULES, *COLUMN_RULES, *TEMPORAL_RULES]:
        registry.register(rule)
    return registry


def build_ohlcv_registry() -> RuleRegistry:
    """The optional crypto/financial OHLCV rule pack."""
    registry = RuleRegistry()
    for rule in OHLCV_RULES:
        registry.register(rule)
    return registry


__all__ = [
    "RULESET_VERSION",
    "OHLCV_RULESET_VERSION",
    "build_general_registry",
    "build_ohlcv_registry",
    "detect_ohlcv_mapping",
]
