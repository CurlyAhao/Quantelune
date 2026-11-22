"""Project journals reuse the workspace revision store and coding source locations."""

import re
import uuid
from datetime import UTC, datetime
from html import escape

from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.workspace import WorkspaceRecord
from app.preview.workspace import load_record, read_record, save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.coding import CodingDocument
from app.schemas.journal import JournalCitation, JournalDocument
from app.schemas.material import CitationRequest
from app.schemas.workspace import SaveRecord
from app.services import material_service, text_coding


def fail(message, status=409):
    return StatForgeError(status, "JOURNAL_UNAVAILABLE", message)


async def load(db, project, user, identity):
    row = await load_record(db, identity, user, project)
    if row.kind != "journal":
        raise fail("这篇日志不存在，请返回日志列表。", 404)
    document = JournalDocument.model_validate_json(row.payload_json)
    if document.deleted:
        raise fail("这篇日志已删除，请返回日志列表。", 404)
    return row, document


async def read(db, project, user, identity):
    row, _ = await load(db, project, user, identity)
    return await read_record(db, row.id, user)


async def list_entries(db, project, user, query=""):
    await material_service.project_access(db, project, user.id)
    rows = (
        await db.scalars(
            select(WorkspaceRecord).where(
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.project_id == project,
                WorkspaceRecord.kind == "journal",
            )
        )
    ).all()
    entries = []
    needle = query.strip().casefold()
    for row in rows:
        doc = JournalDocument.model_validate_json(row.payload_json)
        if doc.deleted or (needle and needle not in (doc.title + "\n" + doc.body).casefold()):
            continue
        entries.append(
            {
                "id": str(row.id),
                "revision": row.revision,
                "title": doc.title,
                "author_name": doc.author_name,
                "created_at": doc.created_at.isoformat(),
                "updated_at": doc.updated_at.isoformat(),
                "excerpt": doc.body[:120],
                "citation_count": len(doc.citations),
            }
        )
    return sorted(entries, key=lambda entry: (entry["updated_at"], entry["id"]), reverse=True)


def theme_path(theme, themes):
    names, seen = [theme.name], {theme.id}
    parent = theme.parent_id
    while parent and parent not in seen:
        seen.add(parent)
        item = next((t for t in themes if t.id == parent), None)
        if not item:
            break
        names.insert(0, item.name)
        parent = item.parent_id
    return " / ".join(names)


async def snapshot(db, project, user, ref):
    if not ref.coding_id or not ref.coding_revision or not ref.segment_id:
        raise fail("请重新选择要插入的编码。", 422)
    row = await load_record(db, ref.coding_id, user, project)
    if row.kind != "coding":
        raise fail("编码记录不可用，请重新选择。", 422)
    state = await read_record(db, row.id, user, ref.coding_revision)
    document = CodingDocument.model_validate(state["payload"])
    segment = next((s for s in document.segments if s.id == ref.segment_id), None)
    if segment is None:
        raise fail("所选版本中没有这条编码，请重新选择。", 422)
    # Snapshot the saved segment, never text/locators supplied by the client.
    return JournalCitation(
        id=uuid.uuid4(),
        coding_id=row.id,
        coding_revision=ref.coding_revision,
        segment=segment.model_copy(deep=True),
        themes=[
            theme_path(t, document.themes) for t in document.themes if t.id in segment.theme_ids
        ],
        case_title=segment.sample_title or "",
        coder_name=segment.coder_name or "未记录编码者",
    )


async def save(db, project, user, body, identity=None):
    await acquire_write_fence(db, user.id)
    await material_service.project_access(db, project, user.id)
    previous = None
    if identity:
        row, previous = await load(db, project, user, identity)
        if row.revision != body.revision:
            raise StatForgeError(
                409, "JOURNAL_CONFLICT", "日志已在另一处修改。当前编辑仍保留，可以另存为一篇日志。"
            )
    elif body.revision:
        raise fail("请重新打开这篇日志。")
    citations = await retained_citations(db, project, user, body.citations, previous)
    now = datetime.now(UTC)
    if previous:
        doc = previous.model_copy(
            update={
                "title": body.title.strip(),
                "body": body.body,
                "citations": citations,
                "updated_at": now,
            }
        )
    else:
        coding = await text_coding.current(db, project, user)
        coder = text_coding.current_coder(
            CodingDocument.model_validate_json(coding.payload_json) if coding else CodingDocument(),
            user,
        )
        doc = JournalDocument(
            title=body.title.strip(),
            body=body.body,
            citations=citations,
            author_id=coder["id"],
            author_name=coder["name"],
            created_at=now,
            updated_at=now,
        )
    if not doc.title:
        raise fail("请填写日志标题。", 422)
    return await save_record(
        db,
        user,
        SaveRecord(project_id=project, expected_revision=body.revision, payload=doc),
        identity,
        journal_write=True,
    )


async def retained_citations(db, project, user, references, previous):
    retained = {c.id: c for c in previous.citations} if previous else {}
    citations, seen = [], set()
    for ref in references:
        if ref.id:
            citation = retained.get(ref.id)
            if citation is None:
                raise fail("这条摘录不属于当前记录，请重新选择。", 422)
        else:
            citation = await snapshot(db, project, user, ref)
        key = (citation.coding_id, citation.coding_revision, citation.segment.id)
        if key not in seen:
            citations.append(citation)
            seen.add(key)
    return citations


async def remove(db, project, user, identity, revision):
    await acquire_write_fence(db, user.id)
    _, previous = await load(db, project, user, identity)
    doc = previous.model_copy(update={"deleted": True, "updated_at": datetime.now(UTC)})
    await save_record(
        db,
        user,
        SaveRecord(project_id=project, expected_revision=revision, payload=doc),
        identity,
        journal_write=True,
    )
    return {"deleted": True}


async def target(db, project, user, identity, citation_id):
    _, doc = await load(db, project, user, identity)
    citation = next((c for c in doc.citations if c.id == citation_id), None)
    if not citation:
        raise fail("这条摘录不在当前日志中。", 404)
    return await citation_target(db, project, user, citation)


async def citation_target(db, project, user, citation):
    segment = citation.segment
    try:
        if segment.material_id:
            material = await material_service.load(db, segment.material_id, user.id, project)
            await material_service.verify_version(material)
            material_service.validate_locator(
                material,
                CitationRequest(
                    material_sha256=segment.material_sha256,
                    material_version=segment.material_version,
                    locator=segment.locator,
                ),
            )
        else:
            from app.services.dataset_service import get_dataset_with_access

            dataset = await get_dataset_with_access(db, segment.dataset_id, user.id)
            if dataset.project_id != project or dataset.sha256 != segment.dataset_sha256:
                raise fail("信号版本已变化。")
    except StatForgeError:
        raise fail("这条摘录的原材料版本已不可用，保存的摘录仍可查看和导出。") from None
    # Journals return to the project material, regardless of a past task's current data.
    return {**segment.model_dump(mode="json"), "task_id": None}


def location_text(segment):
    loc = segment.locator
    if loc.kind == "text":
        return f"第 {loc.page} 页 · 字符 [{loc.start}, {loc.end})", loc.quote
    if loc.kind == "region":
        return (
            f"图像区域 · 左 {loc.x * 100:g}% · 上 {loc.y * 100:g}% \
· 宽 {loc.width * 100:g}% · 高 {loc.height * 100:g}%",
            loc.note,
        )
    quote = loc.transcription.quote if loc.kind == "time" and loc.transcription else ""
    return f"{loc.start_seconds:g}–{loc.end_seconds:g} 秒", quote or getattr(loc, "note", "")


def html_entry(identity, doc):
    e = escape
    refs = []
    for index, citation in enumerate(doc.citations, 1):
        s = citation.segment
        position, quote = location_text(s)
        version = (
            f"材料 v{s.material_version}"
            if s.material_version
            else "信号版本 " + (s.dataset_sha256 or "")[:12]
        )
        if s.locator.kind == "time" and s.locator.transcription:
            version += f" · 转录 v{s.locator.transcription.version}"
        labels = [
            s.filename,
            version,
            position,
            "主题：" + ("、".join(citation.themes) or "仅备注"),
            "编码者：" + citation.coder_name,
        ]
        if citation.case_title:
            labels.append("案例：" + citation.case_title)
        refs.append(
            f'<li><p class="source">{e(" · ".join(labels))}</p>'
            f"<blockquote>{e(quote)}</blockquote>"
            + (f"<p>编码备注：{e(s.memo)}</p>" if s.memo else "")
            + "</li>"
        )
    created = doc.created_at.isoformat(timespec="seconds")
    updated = doc.updated_at.isoformat(timespec="seconds")
    return (
        f'<article id="{doc.kind}-{identity}"><h1>{e(doc.title)}</h1>'
        f'<p class="meta">作者：{e(doc.author_name)}<br>创建：\
<time>{created}</time><br>更新：<time>{updated}</time></p>'
        f'<div class="body">{e(doc.body)}</div>'
        + ("<h2>编码摘录</h2><ol>" + "".join(refs) + "</ol>" if refs else "")
        + "</article>"
    )


async def export_html(db, project, user, identity=None):
    ids = (
        [str(identity)]
        if identity
        else [item["id"] for item in await list_entries(db, project, user)]
    )
    articles, title = [], "研究日志"
    for value in ids:
        _, doc = await load(db, project, user, uuid.UUID(value))
        articles.append(html_entry(value, doc))
        if identity:
            title = doc.title
    return html_document(title, articles)


def html_document(title, articles):
    html = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'
        + escape(title)
        + "</title><style>body{max-width:900px;margin:40px \
auto;padding:0 24px;color:#203c35;font:16px/1.7 system-ui,sans-serif}"
        "article{padding-bottom:32px;margin-bottom:40px;border-bottom:1px \
solid #ccd6d0}h1{font-size:26px}h2{font-size:18px}"
        ".body,blockquote{white-space:pre-wrap;overflow-wrap:anywhere}.meta,.source{font-size:14px;color:#52645d}"
        "blockquote{margin:8px \
0;padding-left:16px;border-left:3px solid #698c7d}li{padding-bottom:12px}"
        "@media print{article{break-after:page}}</style><body>"
        + "".join(articles)
        + "</body></html>"
    )
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .") or "研究日志"
    return {"html": html, "filename": filename + ".html"}
