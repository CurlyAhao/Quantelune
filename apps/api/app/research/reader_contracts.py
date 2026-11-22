"""Fixed Artifact reader authority; no native execution or client price attestation."""

import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from app.research.context_contracts import ContextModel
from app.research.egress import DisclosureSelection, credentials, failure

KIND = "reader"
OPERATION = "read_results"
MAX_SOURCE_BYTES = 512 * 1024
PAUSED_CODES = frozenset({"RESEARCH_MODEL_PAUSED", "RESEARCH_EGRESS_MODEL_PAUSED"})
PROFILE = {
    "version": 1,
    "sdk": "deepagents-0.7.21",
    "profile": "statforge:artifact-reader-v1",
    "durability": "sync",
    "tools": "result_facts/explain_results/write_todos",
    "native_execution": False,
}
PROFILE_V2 = {
    **PROFILE,
    "version": 2,
    "profile": "statforge:artifact-reader-v2",
    "saved_projection": "descriptive-v2",
}
OPERATION_V2 = "read_results_v2"


def view_operation(version):
    if type(version) is not int or version not in {1, 2}:
        raise failure("READER_CONTRACT_REFUSED")
    return OPERATION if version == 1 else OPERATION_V2


def view_version(parameters):
    if not isinstance(parameters, dict):
        raise failure("READER_CONTRACT_REFUSED")
    if parameters.get("operation") == OPERATION:
        return 1
    if parameters.get("operation") == OPERATION_V2:
        return 2
    raise failure("READER_CONTRACT_REFUSED")


def view_profile(version):
    view_operation(version)
    return PROFILE if version == 1 else PROFILE_V2


def view_from_profile(digest):
    from app.research.contracts import logical_hash

    for version in (1, 2):
        if digest == logical_hash(view_profile(version)):
            return version
    raise failure("READER_CONTRACT_REFUSED")


def source_view(document):
    value = document.get("version")
    if value not in {"reader-source-v1", "reader-source-v2"}:
        raise failure("READER_CONTRACT_REFUSED")
    return 1 if value == "reader-source-v1" else 2


def explanation(value, version):
    from app.schemas.preview_extensions import ExplanationRequest

    view_operation(version)
    if version == 2:
        from app.research.reader_saved_descriptive import ExplanationRequestV2

        return ExplanationRequestV2.model_validate(value)
    return ExplanationRequest.model_validate(value)


def tool_limit(version):
    view_operation(version)
    return 8192 if version == 1 else 64 * 1024


def selection(value) -> DisclosureSelection:
    selected = DisclosureSelection.model_validate(value)
    if (
        selected.datasets
        or not 1 <= len(selected.artifact_ids) <= 5
        or selected.purpose != "review"
        or selected.scope != "metadata_only"
        or selected.rows != 0
        or selected.acknowledge_raw
        or credentials(selected.model_dump(mode="json"))
    ):
        raise failure("READER_DISCLOSURE_REFUSED", 422)
    return selected


class ReaderRunCreate(ContextModel):
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
    selection: DisclosureSelection
    projection_version: StrictInt = Field(default=1, ge=1, le=2)

    @field_validator("selection")
    @classmethod
    def bounded_artifacts(cls, value):
        return selection(value)

    @field_validator("idempotency_key")
    @classmethod
    def private_key(cls, value):
        if credentials(value):
            raise ValueError("Invalid reader request identity.")
        return value


class ReaderControlRequest(ContextModel):
    expected_revision: StrictInt = Field(ge=0)
    action: Literal["pause", "resume", "cancel"]
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("idempotency_key")
    @classmethod
    def private_key(cls, value):
        if credentials(value):
            raise ValueError("Invalid reader control identity.")
        return value


@dataclass(frozen=True)
class ReaderToken:
    """Distinct non-native worker authority; never an ExecutionToken/PlanningToken."""

    owner_id: uuid.UUID
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    worker_id: str
    fence: int
