import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.journal import JournalSave, ProjectMemoSave
from app.services import journals, project_memo

router = APIRouter(prefix="/api/v1/projects/{project_id}/journals", tags=["journals"])


@router.get("")
async def index(
    project_id: uuid.UUID,
    q: str = "",
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await journals.list_entries(db, project_id, user, q)


@router.get("/export")
async def export(
    project_id: uuid.UUID,
    journal_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await journals.export_html(db, project_id, user, journal_id)


@router.post("")
async def create(
    project_id: uuid.UUID,
    body: JournalSave,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await journals.save(db, project_id, user, body)


@router.get("/project-memo")
async def read_memo(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await project_memo.read(db, project_id, user)


@router.put("/project-memo")
async def save_memo(
    project_id: uuid.UUID,
    body: ProjectMemoSave,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await project_memo.save(db, project_id, user, body)


@router.get("/project-memo/export")
async def export_memo(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await project_memo.export_html(db, project_id, user)


@router.get("/project-memo/citations/{citation_id}/target")
async def memo_target(
    project_id: uuid.UUID,
    citation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await project_memo.target(db, project_id, user, citation_id)


@router.get("/{journal_id}")
async def read(
    project_id: uuid.UUID,
    journal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await journals.read(db, project_id, user, journal_id)


@router.put("/{journal_id}")
async def update(
    project_id: uuid.UUID,
    journal_id: uuid.UUID,
    body: JournalSave,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await journals.save(db, project_id, user, body, journal_id)


@router.delete("/{journal_id}")
async def remove(
    project_id: uuid.UUID,
    journal_id: uuid.UUID,
    revision: int = Query(ge=1),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await journals.remove(db, project_id, user, journal_id, revision)


@router.get("/{journal_id}/citations/{citation_id}/target")
async def target(
    project_id: uuid.UUID,
    journal_id: uuid.UUID,
    citation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await journals.target(db, project_id, user, journal_id, citation_id)
