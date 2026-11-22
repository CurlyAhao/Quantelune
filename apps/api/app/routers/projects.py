"""Project CRUD endpoints with ownership and soft delete."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.project import Project
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services import audit_service
from app.services.project_access import owned_project

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[Project]:
    """List the current user's projects."""
    result = await db.execute(
        select(Project)
        .where(
            Project.owner_id == current_user.id,
            Project.is_deleted.is_(False),
        )
        .order_by(Project.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> Project:
    """Create a new project owned by the current user."""
    project = Project(**payload.model_dump(), owner_id=current_user.id)
    db.add(project)
    await db.flush()
    await db.refresh(project)
    await audit_service.record(
        db,
        event_type="project.create",
        entity_type="project",
        entity_id=project.id,
        actor=current_user,
        project_id=project.id,
        after={"title": project.title},
    )
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> Project:
    """Get a single project by ID."""
    project = await owned_project(db, project_id, current_user.id)
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> Project:
    """Update a project."""
    project = await owned_project(db, project_id, current_user.id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    await db.flush()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Soft-delete a project."""
    project = await owned_project(db, project_id, current_user.id)

    project.is_deleted = True
    await db.flush()
    await audit_service.record(
        db,
        event_type="project.delete",
        entity_type="project",
        entity_id=project.id,
        actor=current_user,
        project_id=project.id,
        before={"title": project.title},
    )
