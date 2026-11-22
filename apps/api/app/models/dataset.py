"""Dataset and Variable domain models."""

import enum
import json
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class VariableRole(str, enum.Enum):
    FEATURE = "feature"
    TARGET = "target"
    IDENTIFIER = "identifier"
    TIME = "time"
    GROUP = "group"
    WEIGHT = "weight"
    SENSITIVE = "sensitive"
    IGNORE = "ignore"


class SemanticType(str, enum.Enum):
    NUMERIC_CONTINUOUS = "numeric_continuous"
    NUMERIC_DISCRETE = "numeric_discrete"
    CATEGORICAL_NOMINAL = "categorical_nominal"
    CATEGORICAL_ORDINAL = "categorical_ordinal"
    DATETIME = "datetime"
    TEXT = "text"
    BOOLEAN = "boolean"
    IDENTIFIER = "identifier"


class UploadStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class Dataset(Base, UUIDMixin, TimestampMixin):
    """An uploaded dataset within a project."""

    __tablename__ = "datasets"

    @property
    def media_case(self) -> dict | None:
        return json.loads(self.preview_json or "{}").get("media_case")

    @property
    def material_id(self) -> str | None:
        return json.loads(self.preview_json or "{}").get("material_id")

    @property
    def visual_result(self) -> dict | None:
        return json.loads(self.preview_json or "{}").get("visual_result")

    @property
    def media_result(self) -> dict | None:
        return json.loads(self.preview_json or "{}").get("media_result")

    @property
    def transcription(self) -> dict | None:
        value = json.loads(self.preview_json or "{}").get("transcription")
        return (
            {
                key: value[key]
                for key in ("run_id", "version", "source_id", "method")
                if key in value
            }
            if value
            else None
        )

    @property
    def preparation_operation(self) -> str | None:
        steps = json.loads(self.preview_json or "{}").get("preparation", {}).get("steps", [])
        return (steps[-1].get("recipe") or {}).get("operation") if steps else None

    @property
    def preparation_change(self) -> str | None:
        steps = json.loads(self.preview_json or "{}").get("preparation", {}).get("steps", [])
        if not steps:
            return None
        step = steps[-1]
        recipe = step.get("recipe") or {}
        column, target = recipe.get("column"), recipe.get("target")
        if recipe.get("operation") == "rename" and column and target:
            return f"{column} → {target}"
        if column and step.get("label"):
            return f"{step['label']} · {column}"
        return step.get("label")

    @property
    def transcript_pair(self) -> dict | None:
        return (json.loads(self.preview_json or "{}").get("transcription") or {}).get(
            "paired_selection"
        )

    @property
    def coding_summary(self) -> dict | None:
        return json.loads(self.preview_json or "{}").get("coding_summary")

    @property
    def coding_matrix(self) -> dict | None:
        data = json.loads(self.preview_json or "{}").get("coding_matrix")
        return (
            {key: data[key] for key in ("row_kind", "coding_revision", "saved_at")}
            if data
            else None
        )

    @property
    def coding_agreement(self) -> dict | None:
        data = json.loads(self.preview_json or "{}").get("coding_agreement")
        return {key: data[key] for key in ("modality", "revision", "saved_at")} if data else None

    @property
    def signal_session(self) -> bool:
        return bool(json.loads(self.preview_json or "{}").get("signal_session"))

    @property
    def raw_file(self) -> bool:
        return bool(json.loads(self.preview_json or "{}").get("raw_file"))

    @property
    def local_file(self) -> bool:
        return bool(json.loads(self.preview_json or "{}").get("local_file"))

    @property
    def source_dataset_id(self) -> str | None:
        return json.loads(self.preview_json or "{}").get("source_dataset_id")

    @property
    def prepared_source_sha256(self) -> str | None:
        return (json.loads(self.preview_json or "{}").get("prepared_source") or {}).get("sha256")

    @property
    def multimodal(self) -> dict | None:
        metadata = json.loads(self.preview_json or "{}").get("multimodal")
        if not metadata:
            return None
        return {key: value for key, value in metadata.items() if key != "source_object_key"}

    @property
    def sheet_name(self) -> str | None:
        return json.loads(self.preview_json or "{}").get("sheet_name")

    @property
    def csv_encoding(self) -> str | None:
        return json.loads(self.preview_json or "{}").get("csv_encoding")

    @property
    def source_path(self) -> str | None:
        return json.loads(self.preview_json or "{}").get("source_path")

    @property
    def display_name(self) -> str | None:
        meta = json.loads(self.preview_json or "{}")
        transcript = meta.get("transcription")
        summary = meta.get("coding_summary") if meta.get("display_name") == "样本主题汇总" else None
        if transcript or summary:
            created = self.created_at
            local_time = (created if created.tzinfo else created.replace(tzinfo=UTC)).astimezone()
            if transcript:
                label = "人工修订" if transcript.get("method") == "manual_revision" else "机器转录"
                version = f" v{transcript['version']}" if transcript.get("version") else ""
                return f"{self.original_filename} · {label}{version} · {local_time:%m-%d %H:%M:%S}"
            revision = summary.get("coding_revision")
            label = f" · 编码第{revision}版" if revision is not None else ""
            return f"样本主题汇总{label} · {local_time:%m-%d %H:%M:%S}"
        return meta.get("display_name")

    @property
    def preparation_parent_id(self) -> str | None:
        return json.loads(self.preview_json or "{}").get("preparation", {}).get("parent_id")

    @property
    def preparation_steps(self) -> int:
        return len(json.loads(self.preview_json or "{}").get("preparation", {}).get("steps", []))

    @property
    def source_version(self) -> int:
        return json.loads(self.preview_json or "{}").get("file_version", {}).get("number", 1)

    @property
    def source_parent_id(self) -> str | None:
        return json.loads(self.preview_json or "{}").get("file_version", {}).get("parent_id")

    @property
    def source_root_id(self) -> str:
        return (
            json.loads(self.preview_json or "{}")
            .get("file_version", {})
            .get("root_id", str(self.id))
        )

    project_id = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # File identity
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_format: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Object storage
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)

    # Parsed metadata
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status tracking
    upload_status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus), default=UploadStatus.PENDING, nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Legacy compat (kept for existing rows; prefer new fields above)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    file_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)

    # Relationships
    project = relationship("Project", back_populates="datasets")
    variables = relationship(
        "Variable",
        back_populates="dataset",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    profiles = relationship(
        "DatasetProfile",
        back_populates="dataset",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    quality_runs = relationship(
        "QualityRun",
        back_populates="dataset",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    statistical_runs = relationship(
        "StatisticalRun",
        back_populates="dataset",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    ml_experiments = relationship(
        "MLExperiment",
        back_populates="dataset",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class Variable(Base, UUIDMixin, TimestampMixin):
    """A column/variable within a dataset."""

    __tablename__ = "variables"

    dataset_id = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    semantic_type: Mapped[SemanticType | None] = mapped_column(Enum(SemanticType), nullable=True)
    role: Mapped[VariableRole] = mapped_column(
        Enum(VariableRole), default=VariableRole.FEATURE, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    unique_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nullable: Mapped[bool | None] = mapped_column(nullable=True)
    sample_values_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    dataset = relationship("Dataset", back_populates="variables")
