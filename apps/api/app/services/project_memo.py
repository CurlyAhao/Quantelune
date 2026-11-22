"""One project memo, using the existing note revisions, citations and export."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.workspace import WorkspaceRecord
from app.preview.workspace import read_record, save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.coding import CodingDocument
from app.schemas.journal import ProjectMemoDocument
from app.schemas.workspace import SaveRecord
from app.services import journals, material_service, text_coding


async def current(db, project, user):
    await material_service.project_access(db, project, user.id)
    rows = (
        await db.scalars(
            select(WorkspaceRecord).where(
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.project_id == project,
                WorkspaceRecord.kind == "project_memo",
            )
        )
    ).all()
    if len(rows) > 1:
        raise StatForgeError(
            409, "MEMO_UNAVAILABLE", "项目备忘存在重复记录，未修改任何内容，请保留项目并联系支持。"
        )
    return rows[0] if rows else None


async def read(db, project, user):
    row = await current(db, project, user)
    return await read_record(db, row.id, user) if row else None


async def save(db, project, user, body):
    # Serialize first creation as well as subsequent revision writes.
    await acquire_write_fence(db, user.id)
    row = await current(db, project, user)
    if body.revision != (row.revision if row else 0):
        raise StatForgeError(
            409, "MEMO_CONFLICT", "项目备忘已在另一处更新。当前编辑仍保留，请核对最新内容后保存。"
        )
    previous = ProjectMemoDocument.model_validate_json(row.payload_json) if row else None
    citations = await journals.retained_citations(db, project, user, body.citations, previous)
    now = datetime.now(UTC)
    if previous:
        doc = previous.model_copy(
            update={"body": body.body, "citations": citations, "updated_at": now}
        )
    else:
        coding = await text_coding.current(db, project, user)
        coder = text_coding.current_coder(
            CodingDocument.model_validate_json(coding.payload_json) if coding else CodingDocument(),
            user,
        )
        doc = ProjectMemoDocument(
            title="项目备忘",
            body=body.body,
            citations=citations,
            author_id=coder["id"],
            author_name=coder["name"],
            created_at=now,
            updated_at=now,
        )
    return await save_record(
        db,
        user,
        SaveRecord(project_id=project, expected_revision=body.revision, payload=doc),
        row.id if row else None,
        memo_write=True,
    )


async def load(db, project, user):
    row = await current(db, project, user)
    if row is None:
        raise StatForgeError(404, "MEMO_UNAVAILABLE", "请先保存项目备忘。")
    return row, ProjectMemoDocument.model_validate_json(row.payload_json)


async def target(db, project, user, citation_id):
    _, doc = await load(db, project, user)
    citation = next((c for c in doc.citations if c.id == citation_id), None)
    if citation is None:
        raise StatForgeError(404, "MEMO_UNAVAILABLE", "这条摘录不在当前项目备忘中。")
    return await journals.citation_target(db, project, user, citation)


async def export_html(db, project, user):
    row, doc = await load(db, project, user)
    return journals.html_document("项目备忘", [journals.html_entry(row.id, doc)])
