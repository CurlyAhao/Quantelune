"""Typed local read receipts; no model prose, rows, arguments or credential references."""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field, ValidationError

from app.preview.context import canonical, digest, invalid
from app.schemas.preview import PreviewScope, StrictModel

READ_CATEGORIES = {
    "project_context": "project",
    "field_explanation": "schema",
    "quality_summary": "quality",
    "result_facts": "results",
}
READ_LIMIT = 8192
EVIDENCE_LIMIT = 32768


class ReadSource(StrictModel):
    project_id: str
    dataset_id: str | None
    dataset_sha256: str | None
    schema_sha256: str | None
    columns: list[str]


class ReadBinding(StrictModel):
    tool: Literal["project_context", "field_explanation", "quality_summary", "result_facts"]
    category: Literal["project", "schema", "quality", "results"]
    disclosure_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: ReadSource
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProjectSummary(StrictModel):
    title: str
    description: str | None


class ReadColumn(StrictModel):
    name: str
    dtype: str
    inferred_type: str


class FieldSummary(StrictModel):
    dataset_sha256: str
    columns: list[ReadColumn] = Field(max_length=50)


class QualitySummary(StrictModel):
    row_count: int = Field(ge=0, strict=True)
    null_counts: dict[str, int]


class ProjectEvidence(StrictModel):
    kind: Literal["project_context"]
    state: Literal["observed"]
    summary: ProjectSummary
    binding: ReadBinding


class FieldEvidence(StrictModel):
    kind: Literal["field_explanation"]
    state: Literal["observed"]
    summary: FieldSummary
    binding: ReadBinding


class QualityEvidence(StrictModel):
    kind: Literal["quality_summary"]
    state: Literal["observed"]
    summary: QualitySummary
    binding: ReadBinding


SUMMARIES: dict[str, type[StrictModel]] = {
    "project_context": ProjectSummary,
    "field_explanation": FieldSummary,
    "quality_summary": QualitySummary,
}
RECEIPTS: dict[str, type[StrictModel]] = {
    "project_context": ProjectEvidence,
    "field_explanation": FieldEvidence,
    "quality_summary": QualityEvidence,
}
STAT_KEYS = {
    "kind",
    "project_id",
    "dataset_id",
    "schema_sha256",
    "run_id",
    "dataset_sha256",
    "method",
    "status",
    "n",
    "statistic_name",
    "statistic",
    "p_value",
}
ML_KEYS = {
    "kind",
    "project_id",
    "dataset_id",
    "schema_sha256",
    "run_id",
    "dataset_sha256",
    "status",
    "n",
    "primary_metric",
    "scores",
}


def source(scope: PreviewScope) -> dict[str, Any]:
    return scope.model_dump(
        mode="json",
        include={"project_id", "dataset_id", "dataset_sha256", "schema_sha256", "columns"},
    )


def seal(record: dict[str, Any], tool: str, scope: PreviewScope, disclosure: str) -> dict[str, Any]:
    result = {
        **record,
        "binding": {
            "tool": tool,
            "category": READ_CATEGORIES[tool],
            "disclosure_hash": disclosure,
            "source": source(scope),
        },
    }
    result["binding"]["evidence_digest"] = digest(result)
    return result


def project_records(
    tool: str, value: Any, scope: PreviewScope, disclosure: str
) -> list[dict[str, Any]]:
    if tool == "result_facts":
        if not isinstance(value, list):
            raise invalid()
        for fact in value:
            if not isinstance(fact, dict) or set(fact) != (
                STAT_KEYS if fact.get("kind") == "statistics" else ML_KEYS
            ):
                raise invalid()
        return [seal(fact, tool, scope, disclosure) for fact in value]
    summary = SUMMARIES[tool].model_validate(value).model_dump(mode="json")
    record = seal({"kind": tool, "state": "observed", "summary": summary}, tool, scope, disclosure)
    validate_record(record, scope, disclosure, [READ_CATEGORIES[tool]])
    return [record]


def validate_record(
    record: dict[str, Any], scope: PreviewScope, disclosure: str, approved: Sequence[str]
) -> None:
    try:
        binding = ReadBinding.model_validate(record["binding"]).model_dump(mode="json")
        tool = binding["tool"]
        if (
            binding["category"] != READ_CATEGORIES[tool]
            or binding["category"] not in approved
            or binding["disclosure_hash"] != disclosure
            or binding["source"] != source(scope)
        ):
            raise invalid()
        unsigned = {
            **record,
            "binding": {k: v for k, v in binding.items() if k != "evidence_digest"},
        }
        if digest(unsigned) != binding["evidence_digest"]:
            raise invalid()
        if tool == "result_facts":
            expected = STAT_KEYS if record.get("kind") == "statistics" else ML_KEYS
            if set(record) != expected | {"binding"} or record.get("kind") not in {
                "statistics",
                "ml",
            }:
                raise invalid()
            for key in ("project_id", "dataset_id", "dataset_sha256", "schema_sha256"):
                if record[key] != source(scope)[key]:
                    raise invalid()
            expected_run = (
                scope.statistical_run_id if record["kind"] == "statistics" else scope.ml_run_id
            )
            if record.get("run_id") != str(expected_run):
                raise invalid()
        else:
            RECEIPTS[tool].model_validate(record)
            if record["kind"] != tool:
                raise invalid()
            if tool == "field_explanation":
                cols = record["summary"]["columns"]
                if (
                    record["summary"]["dataset_sha256"] != scope.dataset_sha256
                    or len(cols) != len(scope.columns)
                    or {c["name"] for c in cols} != set(scope.columns)
                ):
                    raise invalid()
            if tool == "quality_summary":
                nulls = record["summary"]["null_counts"]
                if set(nulls) != set(scope.columns) or any(
                    type(v) is not int or not 0 <= v <= record["summary"]["row_count"]
                    for v in nulls.values()
                ):
                    raise invalid()
    except (KeyError, TypeError, ValueError, ValidationError):
        raise invalid() from None
    if len(canonical(record).encode()) > READ_LIMIT:
        raise invalid()
