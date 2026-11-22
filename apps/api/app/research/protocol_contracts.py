"""Bounded protocol metadata; no data access or statistical execution."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from app.research.context_contracts import ContextModel, FieldReference
from app.research.contracts import canonical_bytes

PROTOCOL_MAX_BYTES = 64 * 1024
FreeText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]
MachineText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
VariableRole = Literal[
    "analysis",
    "outcome",
    "group",
    "predictor",
    "target",
    "exposure",
    "pairing_id",
    "time",
    "cluster",
    "unknown",
]
StudyDesign = Literal[
    "unknown",
    "descriptive",
    "association",
    "independent_groups",
    "paired",
    "one_way_groups",
    "cross_sectional",
    "prediction",
]
Estimand = Literal[
    "unknown",
    "summary",
    "association",
    "mean_difference",
    "distribution_difference",
    "paired_mean_difference",
    "paired_distribution_shift",
    "group_difference",
    "conditional_association",
    "prediction",
    "causal_effect",
]


class ProtocolVariable(ContextModel):
    reference: FieldReference
    role: VariableRole


class ProtocolPairing(ContextModel):
    layout: Literal["unknown", "wide_same_row", "long_pair_id"] = "unknown"
    description: FreeText | None = None
    conditions: list[MachineText] = Field(default_factory=list, max_length=2)
    pair_id: FieldReference | None = None


class ProtocolMultiplicity(ContextModel):
    policy: Literal[
        "unknown", "single_prespecified", "family_adjusted", "exploratory_unadjusted"
    ] = "unknown"
    family_id: MachineText | None = None
    planned_comparisons: int | None = Field(default=None, strict=True, ge=1)
    adjustment: Literal["unknown", "none", "holm", "bonferroni"] = "unknown"


class ProtocolReporting(ContextModel):
    alpha: float = Field(strict=True, allow_inf_nan=False, gt=0, lt=1)
    confidence_level: float = Field(strict=True, allow_inf_nan=False, ge=0.5, le=0.999)
    alternative: Literal["two_sided", "less", "greater"]
    comparison_order: Literal["recorded_engine_order"]


class ProtocolPrediction(ContextModel):
    task: Literal["unknown", "regression", "classification"] = "unknown"
    split_strategy: MachineText | None = None
    primary_metric: MachineText | None = None


class ResearchProtocolDocument(ContextModel):
    version: Literal["research-protocol-v1"] = "research-protocol-v1"
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    research_question: FreeText
    mode: Literal["unknown", "description", "inference", "prediction"] = "unknown"
    study_design: StudyDesign = "unknown"
    unit_of_analysis: MachineText | None = None
    population_and_sample: FreeText | None = None
    variables: list[ProtocolVariable] = Field(default_factory=list, max_length=50)
    estimand: Estimand = "unknown"
    independence: Literal["unknown", "independent", "paired", "clustered", "repeated"] = "unknown"
    group_definitions: list[MachineText] = Field(default_factory=list, max_length=20)
    pairing: ProtocolPairing | None = None
    cluster_structure: Literal["unknown", "none", "present"] = "unknown"
    time_structure: Literal["unknown", "none", "present"] = "unknown"
    missingness_context: FreeText | None = None
    missing_value_policy: Literal[
        "unknown", "listwise_delete", "pairwise_delete", "error_on_missing"
    ] = "unknown"
    candidate_methods: list[MachineText] = Field(default_factory=list, max_length=13)
    diagnostics: list[MachineText] = Field(default_factory=list, max_length=20)
    assumptions: list[FreeText] = Field(default_factory=list, max_length=20)
    multiplicity: ProtocolMultiplicity | None = None
    reporting: ProtocolReporting | None = None
    prediction: ProtocolPrediction | None = None
    analysis_intent: Literal["unknown", "exploratory", "confirmatory"] = "unknown"
    known_limitations: list[FreeText] = Field(default_factory=list, max_length=20)
    unresolved_questions: list[FreeText] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_document(self) -> Self:
        keys = [(v.reference.dataset_id, v.reference.name) for v in self.variables]
        if len(keys) != len(set(keys)):
            raise ValueError("A source field may have only one protocol role.")
        if self.pairing and self.pairing.pair_id:
            pair = self.pairing.pair_id
            if any(
                variable.reference.dataset_id == pair.dataset_id
                and variable.reference.name == pair.name
                and (variable.role != "pairing_id" or variable.reference != pair)
                for variable in self.variables
            ):
                raise ValueError("A pairing identifier cannot also be a measurement field.")
        pair_variables = [variable for variable in self.variables if variable.role == "pairing_id"]
        if pair_variables and (
            len(pair_variables) != 1
            or self.pairing is None
            or self.pairing.pair_id != pair_variables[0].reference
        ):
            raise ValueError("A pairing_id role must name the explicit pairing identifier.")
        for values in (self.candidate_methods, self.diagnostics):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate protocol machine identifiers.")
        if len(canonical_bytes(self.model_dump(mode="json"))) > PROTOCOL_MAX_BYTES:
            raise ValueError("Protocol exceeds the canonical content limit.")
        return self


class ProtocolIssue(ContextModel):
    code: MachineText
    field: MachineText
    severity: Literal["clarification", "blocked"]


class ProtocolAssessment(ContextModel):
    status: Literal["ready", "clarification", "blocked"]
    issues: list[ProtocolIssue]
    required_diagnostics: list[MachineText]
    alternatives: list[MachineText]
    limitations: list[MachineText]
