"""Pure typed tool bindings; the caller owns authorization and every effect."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.schemas.ml import MLExperimentCreateRequest
from app.schemas.preview_extensions import PlanProposal
from app.schemas.statistics import StatisticalRunRequest


def build_tools(
    safe_call: Callable[[str, dict[str, Any]], Awaitable[Any]],
) -> list[BaseTool]:
    async def project_context() -> dict:
        """Read explicitly approved project title and research description."""
        return await safe_call("project_context", {})

    async def field_explanation() -> dict:
        """Read approved selected field names and physical types; no rows."""
        return await safe_call("field_explanation", {})

    async def quality_summary() -> dict:
        """Compute approved row count and selected-column null counts locally."""
        return await safe_call("quality_summary", {})

    async def result_facts() -> dict:
        """Read approved true selected Run facts; no diagnostics/predictions."""
        return await safe_call("result_facts", {})

    async def propose_statistics(spec: StatisticalRunRequest) -> dict:
        """Validate and propose a supported typed statistical configuration."""
        return await safe_call("propose_statistics", {"config": spec.model_dump()})

    async def execute_statistics(spec: StatisticalRunRequest) -> dict:
        """Execute true statistics only when local auto execution is approved."""
        return await safe_call("execute_statistics", {"config": spec.model_dump()})

    async def ml_preflight(spec: MLExperimentCreateRequest) -> dict:
        """Run actual full-data ML preflight; return approved aggregates only."""
        return await safe_call("ml_preflight", {"config": spec.model_dump()})

    async def execute_ml(spec: MLExperimentCreateRequest) -> dict:
        """Create and run a legal ML experiment only with local approval."""
        return await safe_call("execute_ml", {"config": spec.model_dump()})

    async def propose_plan(spec: PlanProposal) -> dict:
        """Save a finite typed plan for editing and local approval; never execute it."""
        return await safe_call("propose_plan", {"spec": spec.model_dump()})

    async def explain_results(spec: dict) -> dict:
        """Explain saved fields with spec={fields:[{kind,run_id,field}],interpretation}.

        kind: statistics|ml; run_id: true UUID.
        field: status|n|statistic|p_value|confidence_interval|group_means|scores.
        interpretation: descriptive|mean_difference|association|prediction.
        No other keys, values, prose or causal claims. Repair once.
        """
        return await safe_call("explain_results", {"spec": spec})

    # The SDK publishes raw docstring indentation. Preserve the original method's
    # continuation text while keeping the extracted function normally formatted.
    explain_results.__doc__ = (explain_results.__doc__ or "").replace(
        "\n        ", "\n            "
    )

    return [
        StructuredTool.from_function(coroutine=f)
        for f in (
            project_context,
            field_explanation,
            quality_summary,
            propose_statistics,
            execute_statistics,
            result_facts,
            ml_preflight,
            execute_ml,
            propose_plan,
            explain_results,
        )
    ]
