"""Pure, bounded Reviewer documents; structural integrity never grants authority."""

import math
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.research.context_contracts import ContextModel
from app.research.contracts import canonical_bytes, logical_hash
from app.research.egress import credentials
from app.research.protocol_contracts import ProtocolMultiplicity, ResearchProtocolDocument
from app.research.protocol_validation import protocol_hash
from app.research.reviewer_question import MAX_REVIEW_QUESTION_BYTES, reviewer_question_text

KIND = "reviewer"
OPERATION = "review_results"
MAX_INPUT_BYTES = 64 * 1024
MAX_REPORT_BYTES = 32 * 1024
PROFILE = {
    "version": 1,
    "sdk": "deepagents-0.7.21",
    "profile": "statforge:statistical-reviewer-v1",
    "durability": "sync",
    "tools": "read_review_evidence/submit_review/write_todos",
    "native_execution": False,
}

HashText = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100)]
MachineText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
LabelText = Annotated[str, StringConstraints(strict=True, max_length=1024)]
IdentityText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
QuestionText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=MAX_REVIEW_QUESTION_BYTES),
    AfterValidator(reviewer_question_text),
]
UuidText = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"),
]
PathSegment = (
    Annotated[str, StringConstraints(strict=True, max_length=255)]
    | Annotated[int, Field(strict=True, ge=0)]
)
MethodId = Literal[
    "descriptive",
    "pearson",
    "spearman",
    "kendall",
    "welch_t",
    "student_t",
    "mann_whitney",
    "paired_t",
    "wilcoxon",
    "one_way_anova",
    "welch_anova",
    "kruskal_wallis",
    "linear_regression",
]
IssueCode = Literal[
    "missing_design",
    "method_inapplicable",
    "numeric_mismatch",
    "unsupported_numeric_claim",
    "citation_mismatch",
    "unsafe_egress",
    "unsupported_causality",
    "unsupported_no_effect",
    "diagnostics_unverified",
    "multiplicity_unverified",
    "ci_effect_unavailable",
    "question_not_addressed",
    "incomplete_evidence",
]
AGGREGATE_ROOTS = frozenset(
    {
        "method",
        "method_version",
        "status",
        "alternative",
        "confidence_level",
        "p_value",
        "sample",
        "statistic",
        "effect_size",
        "estimates",
        "confidence_interval",
        "descriptive_statistics",
        "assumption_checks",
        "software_versions",
    }
)


class _ReviewModel(ContextModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


def _fresh[Document: ContextModel](value: Document) -> Document:
    # Frozen models can still contain mutable lists/dicts. Never reuse their validation result.
    return type(value).model_validate(value.model_dump(mode="python", warnings="error"))


def _bounded_json(value: Any, maximum: int, depth: int = 0) -> Any:
    """Copy plain finite JSON, rejecting cycles/deep or object-bearing containers first."""
    if depth > 20:
        raise ValueError("Review JSON nesting exceeds the bounded limit.")
    if value is None or type(value) in {bool, int, str}:
        result = value
    elif type(value) is float and math.isfinite(value):
        result = value
    elif type(value) is list:
        result = [_bounded_json(item, maximum, depth + 1) for item in value]
    elif type(value) is dict and all(type(key) is str for key in value):
        result = {key: _bounded_json(item, maximum, depth + 1) for key, item in value.items()}
    else:
        raise ValueError("Review requires plain finite JSON.")
    if depth == 0 and len(canonical_bytes(result)) > maximum:
        raise ValueError("Review exceeds the canonical content limit.")
    return result


def _private(value: str) -> str:
    if not value.strip() or credentials(value):
        raise ValueError("Invalid review request identity.")
    return value


@dataclass(frozen=True)
class ReviewerToken:
    """Distinct worker marker, whose authority must be proved by the control plane."""

    owner_id: uuid.UUID
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    worker_id: str
    fence: int


class ReviewStepReference(_ReviewModel):
    acceptance_id: uuid.UUID
    step_id: ShortText

    _nonblank = field_validator("step_id")(_private)


class ReviewerRunCreate(_ReviewModel):
    source_version: HashText
    idempotency_key: IdentityText
    question: QuestionText
    steps: list[ReviewStepReference] = Field(min_length=1, max_length=5)

    _identity = field_validator("idempotency_key")(_private)

    @model_validator(mode="after")
    def bounded_request(self) -> Self:
        keys = [(step.acceptance_id, step.step_id) for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate review step references.")
        value = self.model_dump(mode="json")
        _bounded_json(value, MAX_INPUT_BYTES)
        if credentials(value):
            raise ValueError("Review request contains credentials.")
        return self


class ReviewerControlRequest(_ReviewModel):
    expected_revision: StrictInt = Field(ge=0)
    action: Literal["pause", "resume", "cancel"]
    idempotency_key: IdentityText

    _identity = field_validator("idempotency_key")(_private)


class _Method(_ReviewModel):
    id: MethodId
    version: ShortText

    _version = field_validator("version")(_private)


class _ProtocolBinding(_ReviewModel):
    version: Literal["research-protocol-binding-v1"]
    version_id: UuidText
    protocol_hash: HashText
    generation: StrictInt = Field(ge=1)
    plan_hash: HashText


class _ArtifactCitation(_ReviewModel):
    bytes: StrictInt = Field(gt=0)
    id: UuidText
    logical_hash: HashText
    media_type: MachineText
    sha256: HashText


class _Citation(_ReviewModel):
    artifacts: list[_ArtifactCitation] = Field(min_length=1, max_length=100)
    attempt_id: UuidText
    attempt_number: StrictInt = Field(ge=1)
    completion_hash: HashText
    dataset_id: UuidText
    dataset_sha256: HashText
    domain_kind: Literal["statistics"]
    domain_run_id: UuidText
    input_hash: HashText
    kind: Literal["native_result"]
    run_id: UuidText


class _Root(_ReviewModel):
    artifact_id: UuidText
    artifact_sha256: HashText
    artifact_logical_hash: HashText
    root_artifact_id: UuidText
    root_sha256: HashText
    root_logical_hash: HashText
    research_run_id: UuidText
    attempt_id: UuidText
    attempt_number: StrictInt = Field(ge=1)
    completion_hash: HashText
    input_hash: HashText
    domain_kind: Literal["statistics"]
    domain_run_id: UuidText


class _Binding(_ReviewModel):
    acceptance_id: UuidText
    acceptance_hash: HashText
    plan_version_id: UuidText
    plan_hash: HashText
    step_id: ShortText
    step_document_hash: HashText
    step_result_hash: HashText
    full_result_hash: HashText
    protocol: _ProtocolBinding
    citation: _Citation
    root: _Root


class _Requested(_ReviewModel):
    alternative: Literal["two_sided", "less", "greater"]
    confidence_level: float = Field(strict=True, allow_inf_nan=False, ge=0.5, le=0.999)
    missing_value_policy: Literal["listwise_delete", "pairwise_delete", "error_on_missing"]
    alpha: float | None = Field(strict=True, allow_inf_nan=False, gt=0, lt=1)


class _Execution(_ReviewModel):
    alternative: Literal["none", "omnibus", "two_sided", "less", "greater"]
    missing_semantics: Literal[
        "per_variable_nonmissing",
        "complete_original_pairs",
        "complete_model_rows",
        "complete_outcome_group_rows",
        "reject_missing",
        "unsupported_request",
    ]
    new_work_supported: StrictBool
    capability_issues: list[MachineText] = Field(max_length=13)
    per_variable_exclusions_are_engine_records: StrictBool
    comparison_order: list[LabelText] | None = Field(max_length=100)


class _Multiplicity(_ReviewModel):
    declared: ProtocolMultiplicity | None
    plan_statistical_steps: StrictInt = Field(ge=1)
    adjustment_executed: StrictBool
    study_family_verified: StrictBool


class _Diagnostics(_ReviewModel):
    planned: list[MachineText] = Field(max_length=20)
    status: Literal["NOT_VERIFIED"]
    recorded_count: StrictInt = Field(ge=0, le=200)
    recorded_pass_is_not_assumption_proof: StrictBool


class _Availability(_ReviewModel):
    ci: Literal["available", "unavailable"]
    effect: Literal["available", "unavailable"]
    p_value: Literal["available", "unavailable"]


class _Fact(_ReviewModel):
    path: list[PathSegment] = Field(min_length=4, max_length=20)
    value: Any

    @field_validator("value")
    @classmethod
    def scalar(cls, value: Any) -> Any:
        if value is None or type(value) in {bool, int}:
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if type(value) is str and len(value) <= 1024:
            return value
        raise ValueError("Review facts must be bounded finite scalar values.")

    @field_validator("path")
    @classmethod
    def aggregate_path(cls, value: list[str | int]) -> list[str | int]:
        if value[:3] != ["results", 0, "result"] or value[3] not in AGGREGATE_ROOTS:
            raise ValueError("Review facts require an original aggregate result path.")
        if value[3] in {"method", "method_version", "status"} and len(value) != 4:
            raise ValueError("Review method and status facts require an exact scalar path.")
        return value


class _Projection(_ReviewModel):
    version: Literal["p4-result-facts-v1"]
    computed: StrictBool
    binding: _Binding
    method: _Method
    requested: _Requested
    execution: _Execution
    multiplicity: _Multiplicity
    diagnostics: _Diagnostics
    facts: list[_Fact] = Field(max_length=4096)
    availability: _Availability
    wording: list[
        Literal[
            "association_not_causation",
            "non_significance_not_no_effect",
            "raw_p_not_effect_size",
            "diagnostics_not_verified",
            "study_family_not_verified",
            "no_pairwise_contrast_from_omnibus",
            "distribution_not_median",
            "descriptive_sample_only",
        ]
    ] = Field(max_length=8)
    projection_hash: HashText

    @model_validator(mode="after")
    def consistent_projection(self) -> Self:
        if (
            self.computed
            or self.multiplicity.adjustment_executed
            or (
                self.multiplicity.study_family_verified
                or not self.execution.per_variable_exclusions_are_engine_records
                or not self.diagnostics.recorded_pass_is_not_assumption_proof
            )
        ):
            raise ValueError("Review projection verification flags do not match the projector.")
        paths = [tuple(fact.path) for fact in self.facts]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate review fact paths.")
        headers = {
            "method": self.method.id,
            "method_version": self.method.version,
            "status": "completed",
        }
        for fact in self.facts:
            if len(fact.path) == 4 and fact.path[3] in headers:
                if fact.value != headers[fact.path[3]]:
                    raise ValueError("Review fact contradicts its method or completion header.")
        if self.binding.protocol.plan_hash != self.binding.plan_hash:
            raise ValueError("Review Plan binding mismatch.")
        return self


class ReviewEvidence(_ReviewModel):
    """Server projection only; a plausible DTO/hash is never publication or owner proof."""

    artifact_id: uuid.UUID
    result_facts: dict[str, Any]

    @field_validator("result_facts", mode="before")
    @classmethod
    def projection(cls, value: Any) -> dict[str, Any]:
        value = _bounded_json(value, MAX_INPUT_BYTES)
        if type(value) is not dict:
            raise ValueError("Review evidence requires a projection object.")
        projected = _Projection.model_validate(value)
        # Validate a complete exact metadata shape without normalizing the original projection.
        if canonical_bytes(projected.model_dump(mode="json")) != canonical_bytes(value):
            raise ValueError("Review evidence is not an exact projection.")
        if value["projection_hash"] != logical_hash(
            {key: item for key, item in value.items() if key != "projection_hash"}
        ):
            raise ValueError("Review projection hash mismatch.")
        if credentials(value):
            raise ValueError("Review evidence contains credentials.")
        return value

    @model_validator(mode="after")
    def bound_artifact(self) -> Self:
        if self.result_facts["binding"]["root"]["root_artifact_id"] != str(self.artifact_id):
            raise ValueError("Review root Artifact mismatch.")
        return self


class ReviewInput(_ReviewModel):
    version: Literal["research-review-context-v1"]
    question: QuestionText
    protocol: ResearchProtocolDocument
    protocol_version_id: uuid.UUID
    protocol_hash: HashText
    generation: StrictInt = Field(ge=1)
    sources: list[ReviewEvidence] = Field(min_length=1, max_length=5)

    @field_validator("protocol", mode="before")
    @classmethod
    def fresh_protocol(cls, value: Any) -> Any:
        return _fresh(value) if isinstance(value, ResearchProtocolDocument) else value

    @model_validator(mode="after")
    def consistent_input(self) -> Self:
        if self.protocol_hash != protocol_hash(self.protocol):
            raise ValueError("Review Protocol hash mismatch.")
        if len({source.artifact_id for source in self.sources}) != len(self.sources):
            raise ValueError("Duplicate review Artifacts.")
        for source in self.sources:
            binding = source.result_facts["binding"]["protocol"]
            if binding != {
                "version": "research-protocol-binding-v1",
                "version_id": str(self.protocol_version_id),
                "protocol_hash": self.protocol_hash,
                "generation": self.generation,
                "plan_hash": source.result_facts["binding"]["plan_hash"],
            }:
                raise ValueError("Review captured Protocol binding mismatch.")
        value = self.model_dump(mode="json")
        _bounded_json(value, MAX_INPUT_BYTES)
        if credentials(value):
            raise ValueError("Review input contains credentials.")
        return self


def review_input_hash(value: ReviewInput) -> str:
    return logical_hash(_fresh(value).model_dump(mode="json"))


class ReviewReference(_ReviewModel):
    kind: Literal["question", "protocol", "artifact"]
    source_index: StrictInt | None = Field(default=None, ge=0)
    path: list[PathSegment] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def scoped_reference(self) -> Self:
        if self.kind == "artifact":
            if self.source_index is None:
                raise ValueError("Artifact reference requires a source index.")
        elif self.source_index is not None or (self.kind == "question" and self.path):
            raise ValueError("Review reference has an invalid scope.")
        return self


class ReviewIssue(_ReviewModel):
    code: IssueCode
    severity: Literal["info", "low", "medium", "high", "critical"]
    references: list[ReviewReference] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def distinct_references(self) -> Self:
        refs = [canonical_bytes(ref.model_dump(mode="json")) for ref in self.references]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate review references.")
        return self


class ReviewReport(_ReviewModel):
    version: Literal["research-review-report-v1"]
    input_hash: HashText
    disposition: Literal["no_issues_detected", "changes_required", "insufficient_evidence"]
    issues: list[ReviewIssue] = Field(max_length=32)
    authorizes_execution: Literal[False] = False
    expert_review: Literal[False] = False

    @field_validator("authorizes_execution", "expert_review", mode="before")
    @classmethod
    def no_authority(cls, value: Any) -> bool:
        if type(value) is not bool or value:
            raise ValueError("A Reviewer report cannot grant authority or expert approval.")
        return False

    @model_validator(mode="after")
    def consistent_report(self) -> Self:
        if (self.disposition == "no_issues_detected") != (not self.issues):
            raise ValueError("Review disposition contradicts its issues.")
        if self.disposition == "changes_required" and not any(
            issue.severity in {"medium", "high", "critical"} for issue in self.issues
        ):
            raise ValueError("Changes require a medium or higher issue.")
        if self.disposition == "insufficient_evidence" and not any(
            issue.code
            in {
                "missing_design",
                "incomplete_evidence",
                "diagnostics_unverified",
                "multiplicity_unverified",
                "ci_effect_unavailable",
            }
            for issue in self.issues
        ):
            raise ValueError("Insufficient evidence requires an evidence issue.")
        _bounded_json(self.model_dump(mode="json"), MAX_REPORT_BYTES)
        return self


def _protocol_reference(path: list[str | int], protocol: dict[str, Any]) -> None:
    value: Any = protocol
    for segment in path:
        if type(value) is dict and type(segment) is str and segment in value:
            value = value[segment]
        elif type(value) is list and type(segment) is int and segment < len(value):
            value = value[segment]
        else:
            raise ValueError("Review Protocol reference is unavailable.")


def validate_review_report(report: ReviewReport, context: ReviewInput) -> ReviewReport:
    """Validate structure and exact provenance; never score statistical/model quality."""
    checked = _fresh(report)
    ctx = _fresh(context)
    if checked.input_hash != review_input_hash(ctx):
        raise ValueError("Review report input hash mismatch.")
    seen: set[tuple[str, tuple[bytes, ...]]] = set()
    protocol = ctx.protocol.model_dump(mode="json")
    for issue in checked.issues:
        refs = tuple(
            sorted(canonical_bytes(ref.model_dump(mode="json")) for ref in issue.references)
        )
        key = (issue.code, refs)
        if key in seen:
            raise ValueError("Duplicate review issues and references.")
        seen.add(key)
        for reference in issue.references:
            if reference.kind == "protocol":
                _protocol_reference(reference.path, protocol)
            elif reference.kind == "artifact":
                index = reference.source_index
                if index is None or index >= len(ctx.sources):
                    raise ValueError("Review Artifact source is unavailable.")
                if reference.path and reference.path not in [
                    fact["path"] for fact in ctx.sources[index].result_facts["facts"]
                ]:
                    raise ValueError("Review Artifact reference is not an original fact path.")
    return checked
