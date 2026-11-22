"""Version-two projection of fixed saved numeric summaries, never a calculation."""

import math
import uuid
from typing import Literal

from pydantic import Field

from app.research.contracts import canonical_bytes
from app.research.egress import failure
from app.schemas.preview import StrictModel
from app.schemas.preview_extensions import ExplanationField


class DescriptiveField(StrictModel):
    kind: Literal["statistics"]
    run_id: uuid.UUID
    field: Literal["descriptive_statistics"]


class ExplanationRequestV2(StrictModel):
    fields: list[ExplanationField | DescriptiveField] = Field(min_length=1, max_length=12)
    interpretation: Literal["descriptive", "mean_difference", "association", "prediction"] = (
        "descriptive"
    )


METRICS = frozenset(
    {
        "mean",
        "median",
        "variance",
        "std",
        "min",
        "max",
        "q1",
        "q3",
        "iqr",
        "skewness",
        "kurtosis",
    }
)
TEXT = (
    "各变量的计数、均值、中位数及离散程度均来自保存的描述统计；"
    "变量的有效计数可能不同，缺失的区间或统计量保持不可用，不据此推断因果关系。"
)
PATH = "results[0].result.descriptive_statistics.variables"


def _number(value):
    return value is None or (type(value) in {int, float} and math.isfinite(value))


def project(snapshot):
    """Only known saved keys/identities; preserve zero, signed values and None."""
    try:
        return _project(snapshot)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise failure("READER_DESCRIPTIVE_UNPROVEN") from None


def _project(snapshot):
    if snapshot.kind != "statistics" or snapshot.document["run"]["method"] != "descriptive":
        return None
    run = snapshot.document["run"]
    names = run["input_config"]["variables"]
    rows = snapshot.document["results"]
    if (
        type(names) is not list
        or not 1 <= len(names) <= 50
        or any(type(name) is not str or not 1 <= len(name) <= 255 for name in names)
        or len(names) != len(set(names))
        or type(rows) is not list
        or len(rows) != 1
    ):
        raise failure("READER_DESCRIPTIVE_UNPROVEN")
    result = rows[0]["result"]
    saved = result["descriptive_statistics"]
    if (
        run["status"] != "completed"
        or result["method"] != "descriptive"
        or result["status"] != "completed"
        or type(saved) is not dict
        or set(saved) != {"variables"}
        or rows[0]["descriptive_statistics"] != saved
        or type(saved["variables"]) is not dict
        or set(saved["variables"]) != set(names)
    ):
        raise failure("READER_DESCRIPTIVE_UNPROVEN")
    for name in names:
        item = saved["variables"][name]
        if (
            type(item) is not dict
            or item.get("name") != name
            or type(item.get("count")) is not int
            or item["count"] < 0
            or set(item)
            != ({"name", "count"} if item["count"] == 0 else {"name", "count", "mean_ci"} | METRICS)
            or any(not _number(item[key]) for key in METRICS if key in item)
        ):
            raise failure("READER_DESCRIPTIVE_UNPROVEN")
        interval = item.get("mean_ci")
        if interval is not None and (
            type(interval) is not dict
            or set(interval) != {"lower", "upper", "confidence_level"}
            or any(not _number(interval[key]) for key in interval)
            or type(interval["confidence_level"]) not in {int, float}
            or not 0 < interval["confidence_level"] < 1
        ):
            raise failure("READER_DESCRIPTIVE_UNPROVEN")
    # Copy only validated fixed keys, never return a mutable saved-snapshot alias.
    from app.research.contracts import parse_document

    return parse_document(canonical_bytes(saved["variables"]).decode())


def augment(snapshot, facts):
    if snapshot.kind == "statistics":
        return {**facts, "descriptive_statistics": project(snapshot)}
    return facts
