"""StatForge data-quality rule engine.

A deterministic, extensible, testable engine for explaining data-quality
issues. No AI is involved — every finding traces to a named rule with a
documented threshold and remediation hint.
"""

from __future__ import annotations

from app.quality.registry import RULESET_VERSION, RuleRegistry
from app.quality.service import run_quality_check
from app.quality.thresholds import ThresholdConfig, default_config
from app.quality.types import Category, RuleStatus, Severity

__all__ = [
    "RULESET_VERSION",
    "RuleRegistry",
    "run_quality_check",
    "ThresholdConfig",
    "default_config",
    "Category",
    "RuleStatus",
    "Severity",
]
