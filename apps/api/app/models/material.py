"""Task-owned immutable materials and reviewed source locations on fresh preview DBs."""

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Material(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "materials"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(default=1)
    object_key: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    extraction_json: Mapped[str] = mapped_column(Text, default="{}")


class Citation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "material_citations"
    material_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("materials.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    material_sha256: Mapped[str] = mapped_column(String(64))
    material_version: Mapped[int] = mapped_column()
    revision: Mapped[int] = mapped_column(default=1)
    reviewed: Mapped[bool] = mapped_column(default=False)
    locator_json: Mapped[str] = mapped_column(Text)
