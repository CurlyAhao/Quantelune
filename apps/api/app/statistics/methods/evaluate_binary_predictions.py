"""Exact confusion counts from existing labels; no fit, threshold or row deletion."""

from app.statistics.base import StatisticalMethod
from app.statistics.binary_prediction_evaluation import BinaryPredictionEvaluation
from app.statistics.result import AssumptionCheck, SampleInfo
from app.statistics.types import MethodId, MethodStatus, MissingPolicy


class EvaluateBinaryPredictions(StatisticalMethod):
    method_id = MethodId.EVALUATE_BINARY_PREDICTIONS
    version = "1.0.0"
    name = "Evaluate existing binary predictions"
    description = "Bound stored integer 0/1 labels and an explicit positive label; no training."
    min_variables = 0
    max_variables = 0
    allow_constant = True

    @staticmethod
    def _labels(ctx, evaluation):
        if ctx.config.missing_policy != MissingPolicy.ERROR_ON_MISSING:
            raise ValueError("Binary evaluation requires explicit error_on_missing.")
        if evaluation.actual.dataset_sha256 != ctx.dataset_sha256:
            raise ValueError("Binary evaluation source digest differs from the executed snapshot.")
        if not ctx.df.height:
            raise ValueError("Binary evaluation needs at least one complete original row.")
        labels = []
        for reference in (evaluation.actual, evaluation.predicted):
            if not ctx.has_column(reference.name):
                raise ValueError("Binary evaluation field is absent from the stored snapshot.")
            series = ctx.df[reference.name]
            if not series.dtype.is_integer() or series.null_count():
                raise ValueError("Binary labels must be complete integers, without coercion.")
            values = series.to_list()
            if any(type(value) is not int or value not in {0, 1} for value in values):
                raise ValueError("Binary labels must be exactly integer 0 or 1.")
            labels.append(values)
        return labels

    def validate(self, ctx):
        try:
            evaluation = BinaryPredictionEvaluation.model_validate(ctx.config.evaluation)
            self._labels(ctx, evaluation)
        except (ValueError, TypeError) as error:
            return [AssumptionCheck("existing_binary_prediction_input", False, str(error))]
        return [
            AssumptionCheck("existing_binary_prediction_input", True, "Complete aligned 0/1 rows.")
        ]

    def execute(self, ctx):
        evaluation = BinaryPredictionEvaluation.model_validate(ctx.config.evaluation)
        actual, predicted = self._labels(ctx, evaluation)
        counts = dict(tp=0, fp=0, tn=0, fn=0)
        positive = evaluation.positive_label
        for a, p in zip(actual, predicted, strict=True):
            key = (
                "tp"
                if a == positive and p == positive
                else ("fn" if a == positive else "fp" if p == positive else "tn")
            )
            counts[key] += 1
        tp, fp, tn, fn = [counts[key] for key in ("tp", "fp", "tn", "fn")]
        n = len(actual)
        assert sum(counts.values()) == n
        ratios = dict(
            recall=(tp, tp + fn, "no_actual_positive"),
            accuracy=(tp + tn, n, None),
            precision=(tp, tp + fp, "no_predicted_positive"),
            specificity=(tn, tn + fp, "no_actual_negative"),
        )
        metrics = [evaluation.primary_metric, *evaluation.secondary_metrics]
        values, states = {}, {}
        for metric in metrics:
            numerator, denominator, reason = ratios[metric]
            values[metric] = numerator / denominator if denominator else None
            states[metric] = (
                {"status": "defined"} if denominator else dict(status="undefined", reason=reason)
            )
        result = self.base_result(ctx)
        result.alternative = "none"
        result.sample = SampleInfo(
            n,
            0,
            n,
            "all original rows paired without filtering",
            {evaluation.actual.name: 0, evaluation.predicted.name: 0},
        )
        result.estimates = {**counts, **values}
        result.diagnostics = dict(
            evaluation_version=evaluation.version,
            operation=evaluation.operation,
            actual=evaluation.actual.model_dump(mode="json"),
            predicted=evaluation.predicted.model_dump(mode="json"),
            label_encoding=evaluation.label_encoding,
            positive_label=positive,
            negative_label=1 - positive,
            primary_metric=evaluation.primary_metric,
            secondary_metrics=list(evaluation.secondary_metrics),
            metric_status=states,
            inference="not_applicable",
            training_performed=False,
            split_independently_verified=False,
        )
        result.limitations = [
            "Metrics describe only bound stored integer 0/1 labels "
            "and the explicit positive label.",
            "No training, thresholding, independent holdout verification, hypothesis test or CI.",
            "Held-out performance does not establish clinical usability or deployment safety.",
        ]
        for metric, state in states.items():
            if state["status"] == "undefined":
                result.limitations.append(f"{metric} undefined: {state['reason']}.")
        if values[evaluation.primary_metric] is None:
            result.status = MethodStatus.NOT_APPLICABLE
            result.error_code = "PREDICTION_PRIMARY_METRIC_UNDEFINED"
            result.error_message = states[evaluation.primary_metric]["reason"]
        return result


EVALUATE_BINARY_PREDICTIONS = EvaluateBinaryPredictions()
