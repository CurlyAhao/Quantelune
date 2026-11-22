"""Research journals and immutable excerpts of already saved coding."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.coding import CodedSegment
from app.schemas.material import Strict


class JournalCitation(Strict):
    id: uuid.UUID
    coding_id: uuid.UUID
    coding_revision: int = Field(ge=1)
    segment: CodedSegment
    themes: list[str]
    case_title: str = ""
    coder_name: str = ""


class ResearchNote(Strict):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=500_000)
    author_id: str
    author_name: str
    created_at: datetime
    updated_at: datetime
    citations: list[JournalCitation] = Field(default_factory=list)
    deleted: bool = False


class JournalDocument(ResearchNote):
    kind: Literal["journal"] = "journal"


class ProjectMemoDocument(ResearchNote):
    kind: Literal["project_memo"] = "project_memo"


class JournalReference(Strict):
    id: uuid.UUID | None = None
    coding_id: uuid.UUID | None = None
    coding_revision: int | None = Field(default=None, ge=1)
    segment_id: uuid.UUID | None = None


class JournalSave(Strict):
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=500_000)
    citations: list[JournalReference] = Field(default_factory=list, max_length=500)


class ProjectMemoSave(Strict):
    revision: int = Field(ge=0)
    body: str = Field(default="", max_length=500_000)
    citations: list[JournalReference] = Field(default_factory=list, max_length=500)
