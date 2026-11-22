"""AI statistical assistant service — structured review and advisory functions.

Each function:
1. Accepts deterministic results from the statistics / ML / quality engines.
2. Builds a focused system + user prompt (no large prompts scattered in code).
3. Calls the active LLM provider for a Pydantic-validated structured response.
4. On failure returns a graceful fallback, never crashes the caller.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.provider import LLMProvider, build_provider
from app.ai.schemas import (
    LeakageReview,
    MethodRecommendation,
    MLEvaluationReview,
    QualityReview,
    RegressionReview,
    StatisticalResultExplanation,
)

_PROMPT_ID_QUALITY = "data-quality-review-v1"
_PROMPT_ID_METHOD = "method-advisor-v1"
_PROMPT_ID_STAT_RESULT = "statistical-result-explanation-v1"
_PROMPT_ID_REGRESSION = "regression-review-v1"
_PROMPT_ID_ML_EVAL = "ml-evaluation-review-v1"
_PROMPT_ID_LEAKAGE = "leakage-review-v1"


def _provider() -> LLMProvider:
    return build_provider()


async def review_quality(
    quality_data: dict, provider: LLMProvider | None = None
) -> tuple[QualityReview | None, dict[str, Any]]:
    """Explain data-quality findings in plain language."""
    provider = provider or _provider()
    messages = [
        {
            "role": "user",
            "content": (
                "Review the following data-quality run and explain the key issues "
                "in plain language, with practical remediation suggestions. Focus "
                "on the most impactful findings (highest severity / coverage).\n\n"
                + json.dumps(quality_data, ensure_ascii=False, default=str)
            ),
        }
    ]
    result, meta = await provider.structured_generate(
        messages=messages,
        response_schema=QualityReview,
    )
    meta["prompt_id"] = _PROMPT_ID_QUALITY
    return result, meta


async def recommend_method(
    context: dict, provider: LLMProvider | None = None
) -> tuple[MethodRecommendation | None, dict[str, Any]]:
    """Suggest statistical methods given the research question and data profile."""
    provider = provider or _provider()
    messages = [
        {
            "role": "user",
            "content": (
                "A user wants to run a statistical analysis. Based on their "
                "research question, variables, data types and quality results, "
                "recommend 1-3 candidate methods with reasoning, assumptions "
                "needed, and risks. Never recommend clearly inappropriate "
                "methods.\n\n" + json.dumps(context, ensure_ascii=False, default=str)
            ),
        }
    ]
    result, meta = await provider.structured_generate(
        messages=messages,
        response_schema=MethodRecommendation,
    )
    meta["prompt_id"] = _PROMPT_ID_METHOD
    return result, meta


async def explain_statistical_result(
    result: dict, provider: LLMProvider | None = None
) -> tuple[StatisticalResultExplanation | None, dict[str, Any]]:
    """Interpret a statistical result (p-value, CI, effect size) in plain language."""
    provider = provider or _provider()
    messages = [
        {
            "role": "user",
            "content": (
                "Explain this statistical result in plain language suitable for a "
                "student or researcher. Interpret the p-value faithfully (do not "
                "call a non-significant result 'no effect'). Explain the confidence "
                "interval and effect size. Mention practical vs statistical "
                "significance.\n\n" + json.dumps(result, ensure_ascii=False, default=str)
            ),
        }
    ]
    result_out, meta = await provider.structured_generate(
        messages=messages,
        response_schema=StatisticalResultExplanation,
    )
    meta["prompt_id"] = _PROMPT_ID_STAT_RESULT
    return result_out, meta


async def review_regression(
    regression_data: dict, provider: LLMProvider | None = None
) -> tuple[RegressionReview | None, dict[str, Any]]:
    """Review a regression model (coefficients, diagnostics, specification)."""
    provider = provider or _provider()
    messages = [
        {
            "role": "user",
            "content": (
                "Review this regression model output. Check for specification, "
                "collinearity (VIF), residual patterns, over-controlling, and "
                "interpretation boundaries. Flag any issues constructively.\n\n"
                + json.dumps(regression_data, ensure_ascii=False, default=str)
            ),
        }
    ]
    result, meta = await provider.structured_generate(
        messages=messages,
        response_schema=RegressionReview,
    )
    meta["prompt_id"] = _PROMPT_ID_REGRESSION
    return result, meta


async def review_ml_evaluation(
    ml_data: dict, provider: LLMProvider | None = None
) -> tuple[MLEvaluationReview | None, dict[str, Any]]:
    """Review ML experiment results — overfit gap, imbalance, metric tradeoffs."""
    provider = provider or _provider()
    messages = [
        {
            "role": "user",
            "content": (
                "Review this machine-learning evaluation. Assess the overfit gap "
                "between CV and test metrics, class imbalance if present, tradeoffs "
                "among metrics, and model selection advice. Do not pick a single "
                "'best' model based on one metric.\n\n"
                + json.dumps(ml_data, ensure_ascii=False, default=str)
            ),
        }
    ]
    result, meta = await provider.structured_generate(
        messages=messages,
        response_schema=MLEvaluationReview,
    )
    meta["prompt_id"] = _PROMPT_ID_ML_EVAL
    return result, meta


async def review_leakage(
    leakage_data: dict, provider: LLMProvider | None = None
) -> tuple[LeakageReview | None, dict[str, Any]]:
    """Review ML data-leakage check results."""
    provider = provider or _provider()
    messages = [
        {
            "role": "user",
            "content": (
                "Review these data-leakage check results. Flag the ones that need "
                "immediate attention (critical/high severity, failing). Suggest "
                "remediation steps and estimate the risk level.\n\n"
                + json.dumps(leakage_data, ensure_ascii=False, default=str)
            ),
        }
    ]
    result, meta = await provider.structured_generate(
        messages=messages,
        response_schema=LeakageReview,
    )
    meta["prompt_id"] = _PROMPT_ID_LEAKAGE
    return result, meta
