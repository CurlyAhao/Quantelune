"""Versioned finite workspace contract. Templates carry configuration, never grants."""

import json
import uuid
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_serializer, model_validator

from app.schemas.analysis_jobs import AnalysisJob
from app.schemas.audio_spectrum import SpectrumRun
from app.schemas.coding import CodingDocument
from app.schemas.journal import JournalDocument, ProjectMemoDocument
from app.schemas.media_runs import MediaRun
from app.schemas.ml_drafts import MLEditorState
from app.schemas.preview import MLForm, PreviewScope, StatisticalForm, StrictModel
from app.schemas.textgrid import TextGridDocument
from app.schemas.voice_quality import VoiceQualityRun

SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ObjectRef(StrictModel):
    kind: Literal["data", "analysis", "figure", "material"]
    id: uuid.UUID


class RecordBase(StrictModel):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=160)


class PlotDraft(RecordBase):
    ai_question: str = Field(default="", max_length=4000)
    kind: Literal["plot"] = "plot"
    scope: PreviewScope
    code: str = Field(default="", max_length=32000)
    dpi: int = Field(default=150, ge=72, le=600, strict=True)


class PlotArtifact(StrictModel):
    object_key: str = Field(min_length=1, max_length=1024)
    sha256: SHA
    bytes: int = Field(ge=1, le=20000000)


class PlotCodeFile(RecordBase):
    """Internal binding to one owned IDE file; never accepted as a user save."""

    kind: Literal["plot_code_file"] = "plot_code_file"
    draft_id: uuid.UUID
    scope_sha256: SHA
    filename: str = Field(pattern=r"^plot-[a-f0-9]{32}\.py$")
    file_sha256: SHA
    code_sha256: SHA
    data_filename: str = Field(pattern=r"^data-[a-f0-9]{64}(?:-[a-f0-9]{32})?\.parquet$")
    data_sha256: SHA
    draft_revision: int = Field(ge=1)
    dpi: int = Field(ge=72, le=600, strict=True)


class PlotRun(RecordBase):
    kind: Literal["plot_run"] = "plot_run"
    draft_id: uuid.UUID
    draft_revision: int = Field(ge=1)
    request_id: uuid.UUID
    instance: uuid.UUID
    scope: PreviewScope
    code: str = Field(max_length=32000)
    dpi: int = Field(ge=72, le=600)
    status: Literal[
        "queued", "running", "cancel_requested", "cancelled", "failed", "interrupted", "completed"
    ]
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[Literal["svg", "png", "pdf", "renderer", "figure_state"], PlotArtifact] = Field(
        default_factory=dict
    )
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=1000)


class WorkspaceState(RecordBase):
    kind: Literal["workspace"] = "workspace"
    selected: ObjectRef | None = None
    tabs: list[ObjectRef] = Field(default_factory=list, max_length=20)
    view: Literal["table", "figure", "diagnostics", "record"] = "table"
    inspector: Literal["closed", "parameters", "style", "sources", "ai"] = "closed"
    explorer_width: int = Field(default=200, ge=160, le=260)
    inspector_width: int = Field(default=320, ge=280, le=420)
    scroll_top: int = Field(default=0, ge=0, le=1000000)


class AnalysisDraft(RecordBase):
    kind: Literal["analysis"] = "analysis"
    scope: PreviewScope
    statistics: StatisticalForm | None = None
    ml: MLForm | None = None
    ml_editor: MLEditorState | None = None
    research_question: str = Field(default="", max_length=1000)
    observation_unit: str = Field(default="", max_length=120)
    measurement_scales: dict[str, Literal["nominal", "ordinal", "continuous", "unknown"]] = Field(
        default_factory=dict, max_length=50
    )

    @model_validator(mode="after")
    def one_method_family(self):
        if (self.ml is not None or self.ml_editor is not None) and self.statistics is not None:
            raise ValueError("Choose statistics or ML for one draft")
        return self


class DataWorkspaceState(RecordBase):
    kind: Literal["data_workspace"] = "data_workspace"
    dataset_id: uuid.UUID


class ResearchInputState(RecordBase):
    """Editable input only; references grant neither execution nor model access."""

    kind: Literal["research_input"] = "research_input"
    context_key: str = Field(default="project", min_length=1, max_length=100)
    question: str = Field(default="", max_length=4000)
    workspace_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    dataset_id: uuid.UUID | None = None
    columns: dict[
        uuid.UUID,
        Annotated[list[Annotated[str, Field(max_length=256)]], Field(max_length=10)],
    ] = Field(default_factory=dict, max_length=5)

    @model_validator(mode="after")
    def session_scope(self):
        if self.session_id and not self.workspace_id:
            raise ValueError("A session requires its workspace")
        return self


class ResearchTask(RecordBase):
    """One research question, with independent input and retained analysis versions."""

    kind: Literal["research_task"] = "research_task"
    question: str = Field(default="", max_length=4000)
    dataset_id: uuid.UUID | None = None
    dataset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    plot_run_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    # Keep display numbers stable after unlinking a run from this task.
    plot_version_order: dict[uuid.UUID, list[uuid.UUID]] = Field(default_factory=dict)
    analysis_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    figure_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    archived_analysis_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    archived_figure_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    adopted_id: uuid.UUID | None = None
    statistics_draft: dict[str, Any] | None = None
    ml_draft: dict[str, Any] | None = None
    ml_editor: MLEditorState | None = None
    exploration_context: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def adopted_version(self):
        if self.adopted_id and self.adopted_id not in self.analysis_ids:
            raise ValueError("The adopted analysis must belong to this task")
        return self


SourceKind = Literal["statistics", "ml", "quality", "workflow", "figure", "citation", "plot_run"]
HistoryKind = Literal[
    "statistics",
    "ml",
    "quality",
    "workflow",
    "figure",
    "citation",
    "research",
    "analysis_run",
    "media_run",
    "analysis",
    "report",
    "research_input",
    "plan",
    "template",
    "session",
    "plot",
    "plot_run",
]


class ReportSource(StrictModel):
    kind: SourceKind
    id: uuid.UUID
    revision: int = Field(default=1, ge=1)
    sha256: SHA


class ReportBlock(StrictModel):
    id: uuid.UUID
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=30000)
    caption: str = Field(default="", max_length=2000)
    source: ReportSource | None = None
    source_snapshot: dict[str, Any] | None = None


class ReportDocument(RecordBase):
    kind: Literal["report"] = "report"
    subtitle: str = Field(default="", max_length=2000)
    blocks: list[ReportBlock] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_blocks(self):
        if len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError("Report blocks require distinct identifiers")
        if sum(len(block.body) for block in self.blocks) > 300000:
            raise ValueError("Report text is too large")
        if (
            len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode())
            > 4 * 1024 * 1024
        ):
            raise ValueError("Report sources are too large")
        return self


class HistoryLabel(RecordBase):
    kind: Literal["history_label"] = "history_label"
    target_kind: HistoryKind
    target_id: uuid.UUID


class HistoryCopyReceipt(RecordBase):
    kind: Literal["history_copy"] = "history_copy"
    input_sha256: SHA
    output_id: uuid.UUID
    output_kind: Literal["workspace", "workflow", "ml_draft"] = "workspace"
    dataset_id: uuid.UUID | None = None


class FigureSpec(StrictModel):
    schema_version: Literal[1, 2] = 1
    title: str = Field(min_length=1, max_length=160)
    kind: Literal["figure"] = "figure"
    scope: PreviewScope
    chart_type: Literal[
        "welch_groups_difference", "distribution", "correlation_scatter", "ols_coefficients"
    ] = "welch_groups_difference"
    renderer_version: Literal["workspace-figure-v1", "workspace-figure-v2"] = "workspace-figure-v1"
    fields: list[str] = Field(min_length=1, max_length=2)
    layers: list[
        Literal[
            "group_means",
            "difference_ci",
            "histogram",
            "box",
            "points",
            "coefficients",
            "coefficient_ci",
        ]
    ] = Field(default=["group_means", "difference_ci"], min_length=1, max_length=2)
    interval: Literal["mean_difference_ci", "none", "coefficient_ci"] = "mean_difference_ci"
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    tick_digits: int | None = Field(default=None, ge=0, le=8)
    line_weight: float = Field(default=1, ge=0.5, le=4)
    margin_scale: float = Field(default=1, ge=0.7, le=1.5)
    axis_label: str = Field(default="", max_length=100)
    group_order: list[str] = Field(default_factory=list, max_length=2)
    snapshot_id: uuid.UUID | None = None
    content_sha256: SHA | None = None
    unit: str = Field(default="", max_length=40)
    theme: Literal["research", "paper"] = "research"
    width: int = Field(default=1200, ge=600, le=4000)
    height: int = Field(default=800, ge=400, le=3000)
    dpi: int = Field(default=300, ge=72, le=600)
    font_size: int = Field(default=16, ge=8, le=32)
    caption: str = Field(default="", max_length=1000)

    @model_serializer(mode="wrap")
    def legacy_shape(self, handler):
        data = handler(self)
        if self.schema_version == 1:
            data.pop("snapshot_id", None)
            data.pop("content_sha256", None)
        return data

    @model_validator(mode="after")
    def versioned_figure(self):
        if len(set(self.layers)) != len(self.layers):
            raise ValueError("Duplicate figure layers")
        if self.schema_version == 1:
            if (
                self.chart_type != "welch_groups_difference"
                or self.renderer_version != "workspace-figure-v1"
                or self.interval != "mean_difference_ci"
                or len(self.group_order) != 2
                or not set(self.layers) <= {"group_means", "difference_ci"}
                or self.snapshot_id is not None
                or self.content_sha256 is not None
                or self.font_size < 10
            ):
                raise ValueError("Legacy figure contract mismatch")
        else:
            allowed = {
                "distribution": {"histogram", "box"},
                "correlation_scatter": {"points"},
                "ols_coefficients": {"coefficients", "coefficient_ci"},
            }
            if (
                self.renderer_version != "workspace-figure-v2"
                or self.chart_type not in allowed
                or not set(self.layers) <= allowed[self.chart_type]
                or self.group_order
                or self.snapshot_id is None
                or self.content_sha256 is None
                or self.font_size > 16
                or self.interval
                != ("coefficient_ci" if "coefficient_ci" in self.layers else "none")
            ):
                raise ValueError("Research figure contract mismatch")
        return self


class GenerateFigureData(StrictModel):
    scope: PreviewScope
    chart_type: Literal["distribution", "correlation_scatter", "ols_coefficients"]
    fields: list[str] = Field(min_length=1, max_length=2)
    bins: int = Field(default=12, ge=1, le=100, strict=True)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")


class FigureData(RecordBase):
    """Internal immutable projection; never accepted by the general save path."""

    kind: Literal["figure_data"] = "figure_data"
    scope: PreviewScope
    chart_type: Literal["distribution", "correlation_scatter", "ols_coefficients"]
    fields: list[str] = Field(min_length=1, max_length=2)
    input_config: dict[str, Any]
    source_result_sha256: SHA
    sample_rule: str
    selection_sha256: SHA | None = None
    params: dict[str, Any]
    engine_version: str
    n: int = Field(ge=1)
    displayed_n: int = Field(ge=0)
    seed: int | None = None
    data: dict[str, Any]
    content_sha256: SHA
    request_id: str
    input_hash: SHA


class PlanStep(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
    capability: Literal["statistics", "ridge"]
    capability_version: Literal["1"] = "1"
    depends_on: list[str] = Field(default_factory=list, max_length=7)
    statistics: StatisticalForm | None = None
    ml: MLForm | None = None
    expected_output: Literal["statistical_run", "ml_run"]
    approval: Literal["explicit_local_execution"] = "explicit_local_execution"

    @model_validator(mode="after")
    def matching_contract(self):
        if self.capability == "statistics":
            if (
                self.statistics is None
                or self.ml is not None
                or self.expected_output != "statistical_run"
            ):
                raise ValueError("Statistics step contract mismatch")
        elif (
            self.ml is None
            or self.statistics is not None
            or self.expected_output != "ml_run"
            or [x.value for x in self.ml.model_ids] != ["ridge"]
            or self.ml.task_type.value != "regression"
            or self.ml.model_params
        ):
            raise ValueError("Only the existing Ridge contract is supported")
        return self


class PlanBody(RecordBase):
    kind: Literal["plan"] = "plan"
    scope: PreviewScope
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    research_question: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def finite_dag(self):
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen or not set(step.depends_on) <= seen:
                raise ValueError("Dependencies must refer to earlier distinct steps")
            seen.add(step.id)
        return self


class TemplateBody(RecordBase):
    kind: Literal["template"] = "template"
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    required_columns: dict[str, str] = Field(min_length=1, max_length=50)
    research_question: str = Field(default="", max_length=1000)


class SessionMessage(StrictModel):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    question: str = Field(min_length=1, max_length=4000)
    scope: PreviewScope
    turn_id: uuid.UUID


class SessionBody(RecordBase):
    kind: Literal["session"] = "session"
    scope: PreviewScope
    messages: list[SessionMessage] = Field(default_factory=list, max_length=12)
    confirmed_information: str = Field(default="", max_length=2000)
    personal_preferences: str = Field(default="", max_length=1000)
    project_rules: str = Field(default="", max_length=1000)
    task_requirements: str = Field(default="", max_length=1000)


RecordPayload = Annotated[
    WorkspaceState
    | DataWorkspaceState
    | CodingDocument
    | JournalDocument
    | ProjectMemoDocument
    | TextGridDocument
    | AnalysisJob
    | MediaRun
    | SpectrumRun
    | VoiceQualityRun
    | ResearchInputState
    | ResearchTask
    | ReportDocument
    | HistoryLabel
    | HistoryCopyReceipt
    | PlotDraft
    | PlotRun
    | PlotCodeFile
    | AnalysisDraft
    | FigureSpec
    | FigureData
    | PlanBody
    | TemplateBody
    | SessionBody,
    Field(discriminator="kind"),
]
RECORD_ADAPTER: TypeAdapter = TypeAdapter(RecordPayload)


class SaveRecord(StrictModel):
    project_id: uuid.UUID
    expected_revision: int = Field(default=0, ge=0)
    payload: RecordPayload


class ExecutePlan(StrictModel):
    revision: int = Field(ge=1)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    approve_local_execution: bool = False


class BindTemplate(StrictModel):
    revision: int = Field(ge=1)
    scope: PreviewScope
    title: str = Field(min_length=1, max_length=160)
