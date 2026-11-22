"""Deterministic statistical analysis engine.

Design principles (mirroring the quality rule engine):

* Deterministic computation first — every statistic, p-value, confidence
  interval and effect size is computed numerically (scipy / numpy /
  statsmodels). AI never decides numbers.
* Method transparency — each method reports its assumptions, the missing-value
  policy applied, sample sizes before/after exclusion, and explicit
  limitations.
* Error isolation — a single method failure is captured as a failed result,
  never a crash of the whole run.
* JSON-safe — NaN / ±Inf are converted to ``None`` before serialisation.
"""

from app.statistics.registry import METHOD_REGISTRY, METHODSET_VERSION
from app.statistics.service import run_statistical_analysis
from app.statistics.types import Alternative, MethodId, MethodStatus, MissingPolicy

__all__ = [
    "METHOD_REGISTRY",
    "METHODSET_VERSION",
    "run_statistical_analysis",
    "Alternative",
    "MethodId",
    "MethodStatus",
    "MissingPolicy",
]
