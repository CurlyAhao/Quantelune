"""Quality-check orchestration: assemble context, run rules, score.

``run_quality_check`` is the single entry point used by the API layer. It is
pure (no I/O) apart from operating on the in-memory DataFrame, so it can run in
a worker thread and is fully deterministic for a given input + config + version.
"""

from __future__ import annotations

import logging
from typing import Any

import polars as pl

from app.quality.context import RuleContext
from app.quality.executor import RuleExecutor, category_summary
from app.quality.registry import RULESET_VERSION
from app.quality.result import RuleResult
from app.quality.rules import (
    OHLCV_RULESET_VERSION,
    build_general_registry,
    build_ohlcv_registry,
    detect_ohlcv_mapping,
)
from app.quality.scoring import compute_score
from app.quality.thresholds import ThresholdConfig
from app.quality.types import RuleStatus

logger = logging.getLogger("statforge.quality")


def run_quality_check(
    df: pl.DataFrame,
    profile: dict[str, Any],
    thresholds: ThresholdConfig,
    dataset_sha256: str = "",
    enable_ohlcv: bool = False,
    ohlcv_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the full rule set against a dataset and return a JSON-able report.

    The general ruleset always runs. When ``enable_ohlcv`` is true the optional
    OHLCV pack also runs, using ``ohlcv_mapping`` (auto-detected if omitted).
    """
    sampled = bool(profile.get("overview", {}).get("sampled", False))
    sample_size = profile.get("overview", {}).get("sample_size")

    # Resolve the OHLCV mapping (explicit wins; otherwise auto-detect).
    mapping: dict[str, str] = {}
    ohlcv_enabled = enable_ohlcv
    if ohlcv_enabled:
        mapping = ohlcv_mapping or detect_ohlcv_mapping(df.columns)
        # If nothing could be mapped, keep the pack but rules will self-skip.

    ctx = RuleContext(
        df=df,
        profile=profile,
        thresholds=thresholds,
        dataset_sha256=dataset_sha256,
        sampled=sampled,
        sample_size=sample_size,
        ohlcv_mapping=mapping,
    )

    general_registry = build_general_registry()
    general = RuleExecutor(general_registry).run(ctx)
    results: list[RuleResult] = list(general.results)
    rulesets = [RULESET_VERSION]
    rule_count = len(general_registry)

    ohlcv_counts: dict[str, int] | None = None
    if ohlcv_enabled:
        ohlcv_registry = build_ohlcv_registry()
        ohlcv = RuleExecutor(ohlcv_registry).run(ctx)
        results.extend(ohlcv.results)
        rulesets.append(OHLCV_RULESET_VERSION)
        ohlcv_counts = ohlcv.counts()
        rule_count += len(ohlcv_registry)

    # Re-tally across the combined result set.
    counts = _tally(results)
    counts["total_rules"] = rule_count
    score = compute_score(results)

    findings = [r.to_dict() for r in results]

    return {
        "ruleset_versions": rulesets,
        "thresholds": thresholds.to_dict(),
        "sampled": sampled,
        "sample_size": sample_size,
        "ohlcv_enabled": ohlcv_enabled,
        "ohlcv_mapping": mapping,
        "total_rules": rule_count,
        "counts": counts,
        "general_counts": general.counts(),
        "ohlcv_counts": ohlcv_counts,
        "score": score,
        "category_summary": category_summary(results),
        "findings": findings,
    }


def _tally(results: list[RuleResult]) -> dict[str, int]:
    counts = {
        "passed": 0,
        "warning": 0,
        "failed": 0,
        "error": 0,
        "skipped": 0,
    }
    for r in results:
        if r.status is RuleStatus.PASSED:
            counts["passed"] += 1
        elif r.status is RuleStatus.WARNING:
            counts["warning"] += 1
        elif r.status is RuleStatus.FAILED:
            counts["failed"] += 1
        elif r.status is RuleStatus.ERROR:
            counts["error"] += 1
        elif r.status is RuleStatus.SKIPPED:
            counts["skipped"] += 1
    counts["total"] = len(results)
    return counts
