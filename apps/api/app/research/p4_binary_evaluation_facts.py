"""Project saved binary counts/rates with exact bindings; never compute new facts."""

import math

from app.research.binary_prediction_evaluation import validate_configuration
from app.research.contracts import canonical_bytes, logical_hash
from app.research.plan_validation import failure


def project(plan, step, protocol, result, binding):
    try:
        evaluation = validate_configuration(step.configuration, protocol)
        expected = evaluation.model_dump(mode="json")
        metrics = [evaluation.primary_metric, *evaluation.secondary_metrics]
        diagnostics, sample, estimates = (
            result["diagnostics"],
            result["sample"],
            result["estimates"],
        )
        if (
            result["method_version"] != "1.0.0"
            or result["alternative"] != "none"
            or result.get("p_value") is not None
            or result.get("confidence_interval") != {}
            or result.get("effect_size") != {}
            or type(estimates) is not dict
            or set(estimates) != {"tp", "fp", "tn", "fn", *metrics}
            or any(
                canonical_bytes(diagnostics.get(key)) != canonical_bytes(value)
                for key, value in {
                    "evaluation_version": expected["version"],
                    "operation": expected["operation"],
                    "actual": expected["actual"],
                    "predicted": expected["predicted"],
                    "label_encoding": expected["label_encoding"],
                    "positive_label": expected["positive_label"],
                    "negative_label": 1 - expected["positive_label"],
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
            raise ValueError("Saved binary evaluation differs from its bound configuration.")
        if any(type(estimates[k]) is not int or estimates[k] < 0 for k in ("tp", "fp", "tn", "fn")):
            raise ValueError("Saved confusion counts are invalid.")
        tp, fp, tn, fn = [estimates[k] for k in ("tp", "fp", "tn", "fn")]
        if tp + fp + tn + fn != sample["final_n"]:
            raise ValueError("Saved confusion counts do not cover all original rows.")
        denominator_states = dict(
            recall=(tp + fn, "no_actual_positive"),
            accuracy=(sample["final_n"], None),
            precision=(tp + fp, "no_predicted_positive"),
            specificity=(tn + fp, "no_actual_negative"),
        )
        if set(diagnostics["metric_status"]) != set(metrics):
            raise ValueError("Saved binary metric states differ.")
        facts = []

        def add(path, value):
            if type(value) not in {str, int, float, bool, type(None)} or (
                type(value) is float and not math.isfinite(value)
            ):
                raise ValueError("Invalid saved binary fact.")
            facts.append(dict(path=["results", 0, "result", *path], value=value))

        for key in ("method", "method_version", "status"):
            add([key], result[key])
        for key in ("original_n", "excluded_n", "final_n", "reason"):
            add(["sample", key], sample[key])
        for key in ("tp", "fp", "tn", "fn"):
            add(["estimates", key], estimates[key])
        for metric in metrics:
            value, state = estimates[metric], diagnostics["metric_status"][metric]
            denominator, reason = denominator_states[metric]
            if value is None:
                if (
                    denominator != 0
                    or metric == evaluation.primary_metric
                    or state != {"status": "undefined", "reason": reason}
                ):
                    raise ValueError("Primary or unexplained undefined binary metric.")
                add(["diagnostics", "metric_status", metric, "reason"], reason)
            elif (
                denominator == 0
                or type(value) not in {int, float}
                or not math.isfinite(value)
                or not 0 <= value <= 1
                or state != {"status": "defined"}
            ):
                raise ValueError("Saved binary rate is invalid.")
            add(["estimates", metric], value)
            add(["diagnostics", "metric_status", metric, "status"], state["status"])
        for key in (
            "evaluation_version",
            "operation",
            "label_encoding",
            "positive_label",
            "negative_label",
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
            method=dict(id="evaluate_binary_predictions", version=result["method_version"]),
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
                "explicit_positive_label",
                "no_training_or_thresholding",
                "split_not_independently_verified",
                "no_inferential_test_or_ci",
                "performance_not_clinical_usability",
            ],
        )
        value["projection_hash"] = logical_hash(value)
        if len(canonical_bytes(value)) > 256 * 1024:
            raise ValueError("Binary evaluation facts limit.")
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure("RESULT_FACTS_UNPROVEN") from None
