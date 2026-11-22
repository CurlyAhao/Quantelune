"""Bridge REFI notes to existing revisioned research journals and project memo."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.workspace import WorkspaceRecord
from app.preview.workspace import save_record
from app.schemas.journal import JournalCitation, JournalDocument, ProjectMemoDocument
from app.schemas.workspace import SaveRecord
from app.services import journals, project_memo


def _hash(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _date(value, fallback):
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            pass
    return fallback


def _citations(body, guid, document, coding_record, state, sources):
    if not coding_record:
        return []
    found = []
    patterns = re.findall(
        (
            '\\*\\*CODED SEGMENT:\\*\\*\\s*"([^"\\r\\n]'
            '*)"[\\s\\S]*?\\[(\\d+)-(\\d+)\\][^\\r\\n]*?'
            ",\\s*File:\\s*(.*?),\\s*Coder:\\s*(.*?)"
            "\\."
        ),
        body,
    )
    for segment in document.segments:
        info = state.get("segments", {}).get(str(segment.id), {})
        selection = info.get("selection", {})
        explicit = guid is not None and guid in selection.get("note_refs", [])
        coords = selection.get("coordinates", {})
        source_name = info.get("source_name") or sources.get(info.get("source_guid"), "")
        quote = getattr(segment.locator, "quote", None)
        inferred = any(
            quote == q
            and coords.get("startPosition") == int(a)
            and coords.get("endPosition") == int(b)
            and source_name == filename
            and segment.coder_name == coder
            for q, a, b, filename, coder in patterns
        )
        if explicit or inferred:
            found.append((segment, explicit))
    # Inference must identify exactly one segment for each textual excerpt.
    inferred = [segment for segment, explicit in found if not explicit]
    found = [
        (segment, explicit)
        for segment, explicit in found
        if explicit
        or sum(
            s.locator == segment.locator
            and s.coder_id == segment.coder_id
            and s.material_id == segment.material_id
            for s in inferred
        )
        == 1
    ]
    return [
        JournalCitation(
            id=uuid.uuid4(),
            coding_id=uuid.UUID(str(coding_record["id"])),
            coding_revision=coding_record["revision"],
            segment=segment.model_copy(deep=True),
            themes=[
                journals.theme_path(t, document.themes)
                for t in document.themes
                if t.id in segment.theme_ids
            ],
            case_title=segment.sample_title or "",
            coder_name=segment.coder_name or "未记录编码者",
        )
        for segment, _ in found
    ]


async def restore_notes(
    db, user, project, package, coding_record, document, state, issues, *, merge=False
):
    now = datetime.now(UTC)
    project_id = project.id
    names = {u["guid"]: u["name"] for u in package.get("users", [])}
    sources = {s["guid"]: s["name"] for s in package.get("sources", [])}
    mappings = state.setdefault("notes", {})
    counts = {"journals": 0, "memo": False}

    async def create_journal(note, key, body):
        author = note.get("user_guid") or ""
        created = _date(note.get("created_at"), now)
        doc = JournalDocument(
            title=(note.get("name") or "未命名日志")[:160],
            body=body,
            author_id=author,
            author_name=names.get(author, "未记录作者"),
            created_at=created,
            updated_at=_date(note.get("modified_at"), created),
            citations=_citations(body, note.get("guid"), document, coding_record, state, sources),
        )
        saved = await save_record(
            db,
            user,
            SaveRecord(project_id=project_id, expected_revision=0, payload=doc),
            commit=False,
            journal_write=True,
        )
        mappings[key] = {
            "record_id": str(saved["id"]),
            "revision": saved["revision"],
            "metadata": dict(note),
            "body_hash": _hash(body),
        }
        counts["journals"] += 1

    for note in package.get("notes", []):
        body = note.get("body") or ""
        guid = note["guid"]
        body_hash = _hash(body)
        key = guid
        existing = mappings.get(key)
        if existing and existing.get("metadata") == note:
            continue
        if existing:
            key = guid + ":" + _hash(json.dumps(note, sort_keys=True, ensure_ascii=False))
            if key in mappings:
                continue
            issues.append(f"日志「{note.get('name') or guid}」与已有版本不同，已另存。")
        if not body.lstrip("\ufeff").strip():
            mappings[key] = {
                "record_id": None,
                "revision": None,
                "metadata": dict(note),
                "body_hash": body_hash,
            }
            issues.append("空白 QDPX 备注已保留原始记录，未创建空日志。")
            continue
        await create_journal(note, key, body)

    body = package.get("description") or ""
    if body.strip():
        metadata = dict(package.get("metadata") or {})
        old = state.get("project_description")
        if not old or old.get("body_hash") != _hash(body):
            row = await project_memo.current(db, project_id, user)
            author = metadata.get("creatingUserGUID") or ""
            created = _date(metadata.get("creationDateTime"), now)
            if row:
                current = ProjectMemoDocument.model_validate_json(row.payload_json)
                if current.body != body:
                    key = "description:" + _hash(body)
                    if key not in mappings:
                        note = {
                            "guid": None,
                            "name": "导入项目说明",
                            "body": body,
                            "user_guid": author,
                            "created_at": metadata.get("creationDateTime"),
                            "modified_at": metadata.get("modifiedDateTime"),
                        }
                        await create_journal(note, key, body)
                        issues.append("导入项目说明与现有项目备忘不同，已另存为日志。")
                elif not merge:
                    state["project_description"] = {
                        "record_id": str(row.id),
                        "revision": row.revision,
                        "body_hash": _hash(body),
                        "metadata": metadata,
                    }
            else:
                doc = ProjectMemoDocument(
                    title="项目备忘",
                    body=body,
                    author_id=author,
                    author_name=names.get(author, "未记录作者"),
                    created_at=created,
                    updated_at=_date(metadata.get("modifiedDateTime"), created),
                    citations=_citations(body, None, document, coding_record, state, sources),
                )
                saved = await save_record(
                    db,
                    user,
                    SaveRecord(project_id=project_id, expected_revision=0, payload=doc),
                    commit=False,
                    memo_write=True,
                )
                state["project_description"] = {
                    "record_id": str(saved["id"]),
                    "revision": saved["revision"],
                    "body_hash": _hash(body),
                    "metadata": metadata,
                }
                counts["memo"] = True
    return counts


async def capture_notes(db, project, user, state, exported, selections):
    rows = (
        await db.scalars(
            select(WorkspaceRecord).where(
                WorkspaceRecord.project_id == project.id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind.in_(["journal", "project_memo"]),
            )
        )
    ).all()
    by_id = {str(row.id): row for row in rows}
    mapped_ids = set()
    notes = []
    used_guids = set()

    def refs(doc, guid):
        for citation in doc.citations:
            info = state.get("segments", {}).get(str(citation.segment.id), {})
            original = info.get("selection", {}).get("guid")
            if not original:
                original = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL, "statforge:selection:" + str(citation.segment.id)
                    )
                )
            if original in selections:
                selected = selections[original][1]
                if info and (
                    info.get("locator") != citation.segment.locator.model_dump(mode="json")
                    or selected["coordinates"] != info.get("selection", {}).get("coordinates")
                ):
                    continue
                selected.setdefault("note_refs", [])
                if guid not in selected["note_refs"]:
                    selected["note_refs"].append(guid)

    def readable_body(doc):
        parts = [doc.body]
        for citation in doc.citations:
            position, quote = journals.location_text(citation.segment)
            if quote and quote in doc.body:
                continue
            parts.append(
                f"编码摘录：{quote}\n{citation.segment.filename} · {position} · "
                f"主题：{'、'.join(citation.themes)} · 编码者：{citation.coder_name}"
            )
        return "\n\n".join(parts)

    def author_id(doc):
        if not doc.author_id:
            return None
        try:
            return str(uuid.UUID(doc.author_id))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, "statforge:" + doc.author_id))

    for key, entry in state.get("notes", {}).items():
        original = dict(entry.get("metadata") or {})
        guid = original.get("guid")
        if not guid or original.get("body") is None:
            continue
        row = by_id.get(entry.get("record_id"))
        if entry.get("record_id") and row is None:
            continue
        if guid in used_guids:
            guid = str(uuid.uuid5(uuid.NAMESPACE_URL, "statforge-note:" + key))
        used_guids.add(guid)
        original["guid"] = guid
        if row:
            mapped_ids.add(str(row.id))
            doc = JournalDocument.model_validate_json(row.payload_json)
            if doc.deleted:
                continue
            if row.revision != entry.get("revision"):
                original.update(
                    name=doc.title, body=readable_body(doc), modified_at=doc.updated_at.isoformat()
                )
            refs(doc, guid)
        notes.append(original)

    for row in rows:
        if row.kind != "journal" or str(row.id) in mapped_ids:
            continue
        doc = JournalDocument.model_validate_json(row.payload_json)
        if doc.deleted:
            continue
        guid = str(row.id)
        notes.append(
            {
                "guid": guid,
                "name": doc.title,
                "body": readable_body(doc),
                "user_guid": author_id(doc),
                "created_at": doc.created_at.isoformat(),
                "modified_at": doc.updated_at.isoformat(),
            }
        )
        refs(doc, guid)
    exported["notes"] = notes
    metadata = dict((state.get("project_description") or {}).get("metadata") or {})
    exported["metadata"] = metadata
    memo = next((row for row in rows if row.kind == "project_memo"), None)
    if memo:
        doc = ProjectMemoDocument.model_validate_json(memo.payload_json)
        exported["description"] = doc.body
        if memo.revision != (state.get("project_description") or {}).get("revision"):
            metadata["modifiedDateTime"] = doc.updated_at.isoformat()
    else:
        exported["description"] = ""
    # Preserve original imported user identities; never manufacture a name for unknown GUIDs.
    users = {u["guid"]: u for u in exported.get("users", [])}
    for u in state.get("users", []):
        users.setdefault(u["guid"], u)
    for row in rows:
        doc = (
            JournalDocument if row.kind == "journal" else ProjectMemoDocument
        ).model_validate_json(row.payload_json)
        key = author_id(doc)
        if key and doc.author_name and doc.author_name != "未记录作者":
            users.setdefault(key, {"guid": key, "name": doc.author_name})
    exported["users"] = list(users.values())
