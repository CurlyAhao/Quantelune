"""Deterministic evaluation only: no estimator, training, split or row deletion."""

import numpy as np

from app.statistics.base import StatisticalMethod
from app.statistics.context import StatisticalContext
from app.statistics.prediction_evaluation import RegressionEvaluation
from app.statistics.result import AssumptionCheck, SampleInfo
from app.statistics.types import MethodId, MethodStatus, MissingPolicy


class EvaluatePredictions(StatisticalMethod):
    method_id = MethodId.EVALUATE_PREDICTIONS
    version = "1.0.0"
    name = "Evaluate existing regression predictions"
    description = "Paired errors of stored actual and predicted values; no model fitting."
    min_variables = 0
    max_variables = 0
    allow_constant = True

    def validate(self, ctx):
        try:
            evaluation = RegressionEvaluation.model_validate(ctx.config.evaluation)
            if ctx.config.missing_policy != MissingPolicy.ERROR_ON_MISSING:
                raise ValueError("Evaluation requires explicit error_on_missing.")
            if evaluation.actual.dataset_sha256 != ctx.dataset_sha256:
                raise ValueError("Evaluation source digest differs from the executed snapshot.")
            if ctx.df.height == 0:
                raise ValueError("Evaluation needs at least one complete original row.")
            self._arrays(ctx, evaluation)
        except (ValueError, TypeError, OverflowError) as error:
            return [AssumptionCheck("existing_prediction_input", False, str(error))]
        return [
            AssumptionCheck("existing_prediction_input", True, "Complete aligned numeric rows.")
        ]

    @staticmethod
    def _arrays(ctx, evaluation):
        arrays = []
        for reference in (evaluation.actual, evaluation.predicted):
            if not ctx.has_column(reference.name):
                raise ValueError("Evaluation field is absent from the stored snapshot.")
            series = ctx.df[reference.name]
            if not (series.dtype.is_integer() or series.dtype.is_float()):
                raise ValueError("Evaluation rejects nonnumeric, boolean and coerced text values.")
            if series.null_count():
                raise ValueError("Evaluation does not delete or impute missing rows.")
            values = series.to_numpy().astype(np.float64)
            if not np.isfinite(values).all():
                raise ValueError("Evaluation rejects non-finite participating values.")
            if series.dtype.is_integer() and any(
                int(converted) != original
                for converted, original in zip(values, series.to_list(), strict=True)
            ):
                raise ValueError("Evaluation integer values lose precision in float64.")
            arrays.append(values)
        return arrays

    def execute(self, ctx: StatisticalContext):
        evaluation = RegressionEvaluation.model_validate(ctx.config.evaluation)
        actual, predicted = self._arrays(ctx, evaluation)
        metrics = [evaluation.primary_metric, *evaluation.secondary_metrics]
        with np.errstate(over="raise", invalid="raise", divide="raise", under="ignore"):
            try:
                with np.errstate(over="ignore"):
                    residual = actual - predicted
                if np.isfinite(residual).all():
                    scale = float(np.max(np.abs(residual)))
                    normalized = residual / scale if scale else residual
                else:
                    # Individual residuals can exceed float64 although the
                    # requested mean error remains finite. Never materialize
                    # that overflowing residual scale.
                    scale = float(max(np.max(np.abs(actual)), np.max(np.abs(predicted))))
                    normalized = actual / scale - predicted / scale
                mae = float(scale * np.mean(np.abs(normalized))) if "mae" in metrics else None
                rmse = float(scale * np.sqrt(np.mean(normalized**2))) if "rmse" in metrics else None
                reason = (
                    "insufficient_samples"
                    if len(actual) < 2
                    else "constant_actual"
                    if np.all(actual == actual[0])
                    else None
                )
                r2 = None
                if "r2" in metrics and reason is None:
                    with np.errstate(over="ignore"):
                        offset = actual - actual[0]
                    if not np.isfinite(offset).all():
                        offset = actual
                    a_scale = float(np.max(np.abs(offset)))
                    centered = offset / a_scale - np.mean(offset / a_scale)
                    # Scaled sums avoid overflow/underflow of squared raw values.
                    denominator = float(np.sum(centered**2))
                    if denominator == 0:
                        raise ValueError("Evaluation variance lost float64 precision.")
                    ratio = np.float64(scale) / np.float64(a_scale)
                    standardized_error = ratio * np.sqrt(np.sum(normalized**2) / denominator)
                    r2 = float(1 - standardized_error**2)
            except FloatingPointError as error:
                raise ValueError("Evaluation arithmetic exceeds finite float64 bounds.") from error
        values = {"mae": mae, "rmse": rmse, "r2": r2}
        if any(value is not None and not np.isfinite(value) for value in values.values()):
            raise ValueError("Evaluation produced non-finite metrics.")
        result = self.base_result(ctx)
        result.alternative = "none"
        result.sample = SampleInfo(
            len(actual),
            0,
            len(actual),
            "all original rows paired without filtering",
            {evaluation.actual.name: 0, evaluation.predicted.name: 0},
        )
        result.estimates = {metric: values[metric] for metric in metrics}
        result.diagnostics = {
            "evaluation_version": evaluation.version,
            "operation": evaluation.operation,
            "actual": evaluation.actual.model_dump(mode="json"),
            "predicted": evaluation.predicted.model_dump(mode="json"),
            "primary_metric": evaluation.primary_metric,
            "secondary_metrics": list(evaluation.secondary_metrics),
            "metric_status": {
                metric: {"status": "undefined", "reason": reason}
                if values[metric] is None
                else {"status": "defined"}
                for metric in metrics
            },
            "inference": "not_applicable",
            "training_performed": False,
            "split_independently_verified": False,
        }
        result.limitations = [
            "Errors describe only the bound stored rows and predictions.",
            "No training, independent holdout verification, hypothesis test or CI is performed.",
        ]
        if "r2" in metrics and reason:
            result.limitations.append(f"R2 undefined: {reason}.")
            if evaluation.primary_metric == "r2":
                result.status = MethodStatus.NOT_APPLICABLE
                result.error_code = "PREDICTION_PRIMARY_METRIC_UNDEFINED"
                result.error_message = reason
        return result


EVALUATE_PREDICTIONS = EvaluatePredictions()
