"""The normal project conversation needs a question, not a planning form."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class FigureTarget(BaseModel):
    id: uuid.UUID
    revision: int = Field(ge=1)


class AssistantTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    context_key: str = Field(default="project", min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=40000)
    dataset_id: uuid.UUID | None = None
    material_action: bool = False
    figure_target: FigureTarget | None = None
