"""Pydantic schemas for AI structured outputs (every AI call validates against these)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QualityFindingRecap(BaseModel):
    category: str = ""
    severity: str = "medium"
    title: str = ""
    explanation: str = ""
    impact: str = ""
    suggestion: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class QualityReview(BaseModel):
    summary: str = ""
    findings: list[QualityFindingRecap] = Field(default_factory=list)
    overall_assessment: str = ""
    limitations: list[str] = Field(default_factory=list)


class MethodRecipient(BaseModel):
    method_id: str = ""
    reasoning: str = ""
    assumptions_satisfied: bool = True
    risks: list[str] = Field(default_factory=list)


class MethodRecommendation(BaseModel):
    research_question_summary: str = ""
    candidates: list[MethodRecipient] = Field(default_factory=list)
    general_advice: str = ""
    limitations: list[str] = Field(default_factory=list)


class StatisticalResultExplanation(BaseModel):
    summary: str = ""
    estimates_plain_language: str = ""
    p_value_interpretation: str = ""
    confidence_interval_interpretation: str = ""
    effect_size_interpretation: str = ""
    practical_significance: str = ""
    assumptions_comments: str = ""
    caveats: list[str] = Field(default_factory=list)


class RegressionReviewItem(BaseModel):
    aspect: str = ""
    assessment: str = ""
    severity: str = "low"
    suggestion: str = ""


class RegressionReview(BaseModel):
    summary: str = ""
    items: list[RegressionReviewItem] = Field(default_factory=list)
    overall_risk: str = "low"
    limitations: list[str] = Field(default_factory=list)


class MLEvaluationReview(BaseModel):
    summary: str = ""
    overfit_assessment: str = ""
    class_balance_comment: str = ""
    metric_tradeoffs: str = ""
    model_selection_advice: str = ""
    limitations: list[str] = Field(default_factory=list)


class LeakageReview(BaseModel):
    summary: str = ""
    flagged: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    remediation_advice: str = ""
    limitations: list[str] = Field(default_factory=list)
