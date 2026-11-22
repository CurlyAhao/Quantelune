"""Projection of saved evaluation metrics; never fills or recomputes a value."""

import math

from app.research.contracts import canonical_bytes, logical_hash
from app.research.plan_validation import failure
from app.research.prediction_evaluation import validate_configuration


def project(plan, step, protocol, result, binding):
    try:
        evaluation = validate_configuration(step.configuration, protocol)
        metrics = [evaluation.primary_metric, *evaluation.secondary_metrics]
        diagnostics, sample, estimates = (
            result["diagnostics"],
            result["sample"],
            result["estimates"],
        )
        expected = evaluation.model_dump(mode="json")
        if (
            result["method_version"] != "1.0.0"
            or result["alternative"] != "none"
            or result.get("p_value") is not None
            or result.get("confidence_interval") != {}
            or result.get("effect_size") != {}
            or type(estimates) is not dict
            or set(estimates) != set(metrics)
            or any(
                diagnostics.get(key) != value
                for key, value in {
                    "evaluation_version": expected["version"],
                    "operation": expected["operation"],
                    "actual": expected["actual"],
                    "predicted": expected["predicted"],
                    "primary_metric": expected["primary_metric"],
                    "secondary_metrics": expected["secondary_metrics"],
                }.items()
            )
            or diagnostics.get("training_performed") is not False
            or diagnostics.get("split_independently_verified") is not False
            or diagnostics.get("inference") != "not_applicable"
            or type(sample["original_n"]) is not int
            or sample["original_n"] < 1
            or type(sample["final_n"]) is not int
            or sample["final_n"] != sample["original_n"]
            or type(sample["excluded_n"]) is not int
            or sample["excluded_n"] != 0
            or sample["per_variable"] != {evaluation.actual.name: 0, evaluation.predicted.name: 0}
        ):
            raise ValueError("Saved evaluation evidence differs from its configuration.")
        facts = []

        def add(path, value):
            if type(value) not in {str, int, float, bool, type(None)} or (
                type(value) is float and not math.isfinite(value)
            ):
                raise ValueError("Invalid evaluation fact.")
            facts.append(dict(path=["results", 0, "result", *path], value=value))

        for key in ("method", "method_version", "status"):
            add([key], result[key])
        for key in ("original_n", "excluded_n", "final_n", "reason"):
            add(["sample", key], sample[key])
        if set(diagnostics["metric_status"]) != set(metrics):
            raise ValueError("Saved metric states differ.")
        for metric in metrics:
            value, state = estimates[metric], diagnostics["metric_status"][metric]
            if value is None:
                if (
                    metric != "r2"
                    or metric == evaluation.primary_metric
                    or state
                    not in (
                        {"status": "undefined", "reason": "insufficient_samples"},
                        {"status": "undefined", "reason": "constant_actual"},
                    )
                ):
                    raise ValueError("Primary or unexplained undefined metric.")
                if (state["reason"] == "insufficient_samples") != (sample["final_n"] < 2):
                    raise ValueError("Undefined R2 sample reason differs.")
                add(["diagnostics", "metric_status", metric, "reason"], state["reason"])
            elif (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or metric != "r2"
                and value < 0
                or state != {"status": "defined"}
                or metric == "r2"
                and value > 1
            ):
                raise ValueError("Saved metric is invalid.")
            add(["estimates", metric], value)
            add(["diagnostics", "metric_status", metric, "status"], state["status"])
        for key in (
            "evaluation_version",
            "operation",
            "primary_metric",
            "inference",
            "training_performed",
            "split_independently_verified",
        ):
            add(["diagnostics", key], diagnostics[key])
        for index, metric in enumerate(evaluation.secondary_metrics):
            add(["diagnostics", "secondary_metrics", index], metric)
        for key, version in result["software_versions"].items():
            if key in {"python", "numpy", "scipy", "statsmodels", "polars"}:
                add(["software_versions", key], version)
        value = dict(
            version="p4-result-facts-v1",
            computed=False,
            binding=binding,
            method=dict(id="evaluate_predictions", version=result["method_version"]),
            requested=dict(
                evaluation=expected,
                missing_value_policy="error_on_missing",
                alternative=None,
                confidence_level=None,
                alpha=None,
            ),
            execution=dict(
                alternative="none",
                missing_semantics="reject_missing",
                new_work_supported=True,
                capability_issues=[],
                per_variable_exclusions_are_engine_records=True,
                comparison_order=None,
            ),
            multiplicity=dict(
                declared=protocol.multiplicity.model_dump(mode="json")
                if protocol.multiplicity
                else None,
                plan_statistical_steps=sum(s.capability == "statistics" for s in plan.steps),
                adjustment_executed=False,
                study_family_verified=False,
            ),
            diagnostics=dict(
                planned=protocol.diagnostics,
                status="NOT_VERIFIED" if protocol.diagnostics else "NOT_APPLICABLE",
                recorded_count=0,
                recorded_pass_is_not_assumption_proof=True,
            ),
            facts=facts,
            availability=dict(ci="unavailable", effect="unavailable", p_value="unavailable"),
            wording=[
                "existing_predictions_only",
                "stored_rows_only",
                "no_training_or_repartition",
                "split_not_independently_verified",
                "no_inferential_test_or_ci",
            ],
        )
        value["projection_hash"] = logical_hash(value)
        if len(canonical_bytes(value)) > 256 * 1024:
            raise ValueError("Evaluation facts limit.")
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure("RESULT_FACTS_UNPROVEN") from None
