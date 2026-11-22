"""Portable project exchange through the existing sources, cases and coding document."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.material import Citation, Material
from app.preview.workspace import save_record
from app.schemas.coding import CodingDocument, LocalCoder, Theme
from app.schemas.material import CitationRequest
from app.schemas.workspace import SaveRecord
from app.services import local_workspaces as local
from app.services import material_service, project_sources, storage_service, text_coding
from app.services import refi_project_format as refi
from app.services.archive_staging import ExpandedBudget, require_disk_space


def invalid(message):
    return StatForgeError(422, "QDPX_INVALID", message)


def safe_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:180] or "材料"


def exchange_path(project):
    root = local.project_root(project)
    return root / ".statforge" / "qdpx-import.json" if root else None


def imported(project):
    path = exchange_path(project)
    return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}


def state_for(project):
    return json.loads(project.snapshot_json or "{}").get("qdpx_exchange") or imported(project.id)


def retain_state(project, state):
    snapshot = json.loads(project.snapshot_json or "{}")
    snapshot["qdpx_exchange"] = state
    project.snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)


def text_boundaries(text):
    """Normalized REFI codepoint boundaries -> original UTF-8-sig decoded boundaries."""
    bounds, offset = [0], 0
    while offset < len(text):
        offset += 2 if text.startswith("\r\n", offset) else 1
        bounds.append(offset)
    return bounds


def locator_for(material, selection):
    coords, kind = selection["coordinates"], selection["kind"]
    if kind == "PlainTextSelection":
        pages = json.loads(material.extraction_json)["pages"]
        page = pages[0]
        text, bounds = page["text"], text_boundaries(page["text"])
        start, end = coords["startPosition"], coords["endPosition"]
        if not 0 <= start < end < len(bounds):
            raise ValueError("文字范围超出原文")
        start, end = bounds[start], bounds[end]
        return {
            "kind": "text",
            "page": 1,
            "start": start,
            "end": end,
            "text_sha256": page["sha256"],
            "quote": text[start:end],
        }
    if kind == "PictureSelection":
        meta = json.loads(material.metadata_json)
        width, height = meta.get("width"), meta.get("height")
        if not width or not height:
            raise ValueError("未能读取图片原始尺寸")
        x1, y1, x2, y2 = (coords[k] for k in ("firstX", "firstY", "secondX", "secondY"))
        if not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
            raise ValueError("编码区域超出原图")
        return {
            "kind": "region",
            "x": x1 / width,
            "y": y1 / height,
            "width": (x2 - x1) / width,
            "height": (y2 - y1) / height,
        }
    if kind in {"AudioSelection", "VideoSelection"}:
        return {
            "kind": "time",
            "start_seconds": coords["begin"] / 1000,
            "end_seconds": coords["end"] / 1000,
        }
    raise ValueError("此类 PDF 区域编码尚未接入；原 PDF 已保留")


def issue_text(issue):
    names = {
        "Variable": "属性定义",
        "VariableValue": "属性值",
        "Note": "项目备注",
        "NoteRef": "备注引用",
        "SelectionRef": "案例片段引用",
        "Set": "集合",
        "Link": "链接",
        "Graph": "关系图",
        "Transcript": "转录映射",
        "Representation": "替代表示",
        "TextDescription": "文字描述",
        "Coding": "整份材料编码",
    }
    if match := re.match(r"Unsupported (\w+): (\d+) \((.*)\)", issue):
        return f"{names.get(match[1], match[1])} {match[2]} 项\
（{match[3]}）尚未接入；原包保留，本次导出不含这些项。"
    if match := re.match(r"Case (.*?) \([^)]*\): (\d+) unresolved source reference", issue):
        return (
            f"案例「{match[1]}」的 {match[2]} 个材料引用无法对应，未建立这些关联；其余材料已保留。"
        )
    return "未导入的交换记录：" + issue


async def make_material(db, user, project, row, path):
    mime, details, extraction = material_service.inspect(path, row.original_filename)
    identity = uuid.uuid4()
    key = f"m/{identity.hex}/source"
    if not local.reference_object(project.id, key, path):
        raise invalid("无法关联导入材料，请重新选择工作区目录。")
    details.update(source_dataset_id=str(row.id), source_path=row.source_path, local_file=True)
    result = Material(
        id=identity,
        user_id=user.id,
        project_id=project.id,
        filename=row.original_filename,
        mime_type=mime,
        file_size=row.file_size,
        sha256=row.sha256,
        version=1,
        object_key=key,
        metadata_json=json.dumps(details),
        extraction_json=json.dumps(extraction),
    )
    db.add(result)
    await db.flush()
    meta = json.loads(row.preview_json)
    meta["material_id"] = str(identity)
    row.preview_json = json.dumps(meta)
    return result


async def case_collection(db, user, project, cases, sources):
    """Cases share the existing immutable material-group descriptor and member IDs."""
    from app.services.multimodal_cases import SCHEMA

    samples = []
    for case in cases:
        members = [sources[k] for k in case["source_guids"] if k in sources]
        primary = next(
            (r for r in members if r.file_format in {"mp4", "wav"}), members[0] if members else None
        )
        sample = {
            "sample_id": case["guid"],
            "refi_case_guid": case["guid"],
            "case_title": case["name"],
            "title": case["name"],
            "display_title": case["name"],
            "members": [{"id": str(r.id), "sha256": r.sha256} for r in members],
            "files": {},
        }
        if primary:
            sample["primary_id"] = str(primary.id)
            key = {"mp4": "video_av", "wav": "audio", "txt": "text", "png": "image_path"}.get(
                primary.file_format
            )
            if key:
                sample[key] = primary.source_path
                sample["files"][key] = {"id": str(primary.id), "sha256": primary.sha256}
                sample[
                    {
                        "video_av": "video_id",
                        "audio": "audio_id",
                        "text": "text_id",
                        "image_path": "image_dataset_id",
                    }[key]
                ] = str(primary.id)
        samples.append(sample)
    if not samples:
        return None
    descriptor = {
        "schema": SCHEMA,
        "kind": "media",
        "identity": "qdpx:" + str(project.id),
        "title": "导入案例",
        "samples": samples,
    }
    with tempfile.TemporaryDirectory(prefix="sf-qdpx-case-") as folder:
        path = Path(folder) / "cases.sfmedia"
        path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
        row = await project_sources.store(
            db, project.id, user.id, str(path), path.name, "联合查看/cases.sfmedia"
        )
    row.preview_json = json.dumps(
        {
            **json.loads(row.preview_json),
            "media_case": descriptor,
            "media_group_id": str(row.id),
            "display_name": descriptor["title"],
        }
    )
    row.upload_status = UploadStatus.READY
    return row


async def restore(db, user, archive_path, directory):
    from app.preview.write_fence import acquire_write_fence

    await acquire_write_fence(db, user.id)
    parent = Path(directory).resolve(strict=True)
    local.checked_path(parent)
    if not parent.is_dir() or parent.drive.upper() == "C:":
        raise invalid("请选择 G 盘或 H 盘中的工作区保存目录。")
    budget = ExpandedBudget()
    try:
        package = refi.read_package(archive_path, budget)
        themes = [
            Theme(
                id=c["guid"],
                name=c["name"],
                parent_id=c["parent_guid"],
                is_codable=c["is_codable"],
                color_rgb=c.get("color"),
                memo=c["description"],
            )
            for c in package["codes"]
        ]
        coders = [LocalCoder(id=u["guid"], name=u["name"]) for u in package["users"]]
        CodingDocument(themes=themes, local_coders=coders)
    except (ValueError, OSError) as exc:
        raise invalid(f"无法读取此 QDPX 项目：{exc}") from None
    root = parent / safe_name(package["name"])
    suffix = 2
    while root.exists():
        root = parent / f"{safe_name(package['name'])} ({suffix})"
        suffix += 1
    root.mkdir()
    try:
        relative_paths, issues = {}, [issue_text(i) for i in package["issues"]]
        with ZipFile(archive_path) as archive:
            for source in package["sources"]:
                member = source.get("member_path")
                if not member and "plain_text_content" not in source:
                    continue
                name = safe_name(source["name"])
                if not Path(name).suffix:
                    name += Path(member).suffix if member else ".txt"
                relative = f"材料/{name}"
                if relative.casefold() in {p.casefold() for p in relative_paths.values()}:
                    relative = f"材料/{Path(name).stem} ({source['guid'][:8]}){Path(name).suffix}"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if member:
                    with archive.open(member) as src, path.open("xb") as dst:
                        budget.copy(src, dst, path.parent)
                else:
                    data = source["plain_text_content"].encode("utf-8")
                    budget.consume(len(data))
                    require_disk_space(path.parent, len(data))
                    path.write_bytes(data)
                relative_paths[source["guid"]] = relative
        # Keep unsupported objects in the original package, separate from live editable objects.
        internal = root / ".statforge"
        internal.mkdir()
        require_disk_space(
            internal, archive_path.stat().st_size + len(package["original_xml"].encode("utf-8"))
        )
        shutil.copyfile(archive_path, internal / "original.qdpx")
        (internal / "original.qde").write_text(
            package["original_xml"], encoding="utf-8", newline=""
        )
    except Exception as exc:
        shutil.rmtree(root)
        if isinstance(exc, (ValueError, OSError)):
            raise invalid(f"无法读取此 QDPX 项目：{exc}") from None
        raise
    project = await local.open_folder(db, user.id, str(root))
    project.title = package["name"]
    rows = list((await db.scalars(select(Dataset).where(Dataset.project_id == project.id))).all())
    by_path = {r.source_path: r for r in rows}
    source_rows, materials, source_map = {}, {}, {}
    for source in package["sources"]:
        relative = relative_paths.get(source["guid"])
        if relative is None:
            continue
        row = by_path[relative]
        source_rows[source["guid"]] = row
        meta = json.loads(row.preview_json)
        meta["refi_source"] = {
            "guid": source["guid"],
            "name": source["name"],
            "kind": source["kind"],
        }
        row.preview_json = json.dumps(meta)
        source_map[source["guid"]] = {
            "dataset_id": str(row.id),
            "sha256": row.sha256,
            "relative_path": relative,
        }
        try:
            materials[source["guid"]] = await make_material(db, user, project, row, root / relative)
        except StatForgeError as exc:
            if exc.error_code != "MATERIAL_FORMAT_UNSUPPORTED":
                raise
            issues.append(
                f"{source['name']}：已保留原文件，当前格式不能在内置播放器预览，可从工作区文件夹打开。"
            )
    collection = await case_collection(db, user, project, package["cases"], source_rows)
    segments, imported_segments = [], {}
    coder_names = {u["guid"]: u["name"] for u in package["users"]}
    for source in package["sources"]:
        material = materials.get(source["guid"])
        for selected in source["selections"]:
            if not material:
                if selected["codings"]:
                    issues.append(
                        f"{source[('name')]}：{
                            len(
                                selected[
                                    (
                                        'codin\
gs'
                                    )
                                ]
                            )
                        } 条编码未接入预览，原记录保留。"
                    )
                continue
            try:
                locator = locator_for(material, selected)
                request = CitationRequest(
                    material_sha256=material.sha256,
                    material_version=1,
                    reviewed=True,
                    locator=locator,
                )
                material_service.validate_locator(material, request)
            except (ValueError, StatForgeError) as exc:
                issues.append(
                    f"{source['name']}：一处编码位置无法读取（{str(exc)[:160]}），原记录保留。"
                )
                continue
            citation = await material_service.save_citation(db, material.id, user.id, request)
            for coding in selected["codings"]:
                segment = text_coding.segment_from_citation(
                    citation,
                    [coding["code_guid"]],
                    selected.get("description", ""),
                    identity=uuid.UUID(coding["guid"]),
                    origin="manual",
                )
                segment.coder_id, segment.coder_name = (
                    coding["user_guid"],
                    coder_names[coding["user_guid"]],
                )
                case = next(
                    (c for c in package["cases"] if source["guid"] in c["source_guids"]), None
                )
                if case and collection:
                    segment.sample_id, segment.sample_title = case["guid"], case["name"]
                    segment.sample_collection_id = collection.id
                segments.append(segment)
                imported_segments[str(segment.id)] = {
                    "source_guid": source["guid"],
                    "source_name": source["name"],
                    "source_sha": material.sha256,
                    "selection": selected,
                    "coding": coding,
                    "locator": segment.locator.model_dump(mode="json"),
                }
    document = CodingDocument(
        themes=themes,
        segments=segments,
        local_coders=coders,
        active_coder_id=coders[0].id if coders else None,
    )
    coding_record = await save_record(
        db,
        user,
        SaveRecord(project_id=project.id, expected_revision=0, payload=document),
        commit=False,
        coding_write=True,
    )
    state = {
        "sources": source_map,
        "segments": imported_segments,
        "case_group_id": str(collection.id) if collection else None,
        "original_cases": package["cases"],
        "users": package["users"],
    }
    from app.services.refi_project_notes import restore_notes

    note_counts = await restore_notes(
        db, user, project, package, coding_record, document, state, issues
    )
    summary = {
        "sources": len(source_rows),
        "codes": len(themes),
        "cases": len(package["cases"]),
        "links": sum(sum(g in source_rows for g in c["source_guids"]) for c in package["cases"]),
        "codings": len(segments),
        **note_counts,
        "items": issues,
    }
    state["summary"] = summary
    retain_state(project, state)
    local.write_json(internal / "qdpx-import.json", state)
    await db.commit()
    await local.save_project(db, project, user)
    return {"project": {"id": str(project.id)}, "workspace_path": str(root), "summary": summary}


def coords_for(segment, material, original):
    locator = segment.locator.model_dump(mode="json")
    # Exact original pixel/ms values survive round trips; only edited locations are quantized.
    if original and locator == original["locator"]:
        return original["selection"]["kind"], original["selection"]["coordinates"]
    if locator["kind"] == "text":
        pages = json.loads(material.extraction_json)["pages"]
        page = next(p for p in pages if p["page"] == locator["page"])
        text = page["text"]
        if text[locator["start"] : locator["end"]] != locator["quote"]:
            raise invalid("原文位置已变化，请先重新定位该文字编码。")

        def offset(value):
            return len(text[:value].replace("\r\n", "\n").replace("\r", "\n"))

        return "PlainTextSelection", {
            "startPosition": offset(locator["start"]),
            "endPosition": offset(locator["end"]),
        }
    if locator["kind"] == "region":
        meta = json.loads(material.metadata_json)
        w, h = meta["width"], meta["height"]
        return "PictureSelection", {
            "firstX": round(locator["x"] * w),
            "firstY": round(locator["y"] * h),
            "secondX": round((locator["x"] + locator["width"]) * w),
            "secondY": round((locator["y"] + locator["height"]) * h),
        }
    if locator["kind"] == "time":
        return (
            "VideoSelection" if material.mime_type.startswith("video/") else "AudioSelection"
        ), {
            "begin": round(locator["start_seconds"] * 1000),
            "end": round(locator["end_seconds"] * 1000),
        }
    raise invalid("此项目含尚不能写入 QDPX 的信号或 PDF 编码，请使用原有项目导出保留全部内容。")


def stable_guid(value):
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "statforge:" + str(value)))


async def capture(db, project, user, destination):
    state = state_for(project)
    original_selections = {}
    origin = exchange_path(project.id)
    if origin and (origin.parent / "original.qdpx").is_file():
        original_selections = {
            s["guid"]: s
            for source in refi.read_package(origin.parent / "original.qdpx")["sources"]
            for s in source["selections"]
        }
    record = await text_coding.current(db, project.id, user)
    document = (
        CodingDocument.model_validate_json(record.payload_json) if record else CodingDocument()
    )
    rows = list(
        (
            await db.scalars(
                select(Dataset)
                .where(
                    Dataset.project_id == project.id, Dataset.upload_status != UploadStatus.DELETED
                )
                .order_by(Dataset.created_at)
            )
        ).all()
    )
    materials = list(
        (await db.scalars(select(Material).where(Material.project_id == project.id))).all()
    )
    by_material = {str(m.id): m for m in materials}
    citation_dates = {
        str(c.id): c.created_at.isoformat()
        for c in (await db.scalars(select(Citation).where(Citation.project_id == project.id))).all()
    }
    source_ids, sources, files = {}, {}, {}
    kind_by_suffix = {
        "txt": "TextSource",
        "md": "TextSource",
        "png": "PictureSource",
        "jpg": "PictureSource",
        "jpeg": "PictureSource",
        "gif": "PictureSource",
        "webp": "PictureSource",
        "wav": "AudioSource",
        "mp3": "AudioSource",
        "mp4": "VideoSource",
        "pdf": "PDFSource",
    }
    for row in rows:
        meta = json.loads(row.preview_json or "{}")
        kind = kind_by_suffix.get(row.file_format)
        if not row.raw_file or not kind or row.media_case:
            continue
        ref = meta.get("refi_source", {})
        key = ref.get("guid", str(row.id))
        if key in sources:
            key = stable_guid(f"source:{key}:{row.id}")
        path = storage_service.get_local_object_path(row.object_key)
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != row.sha256:
            raise invalid(
                f"{row.original_filename} 已变化，请刷新材料后再导出；旧编码不会自动移到新文件。"
            )
        source_ids[str(row.id)] = key
        sources[key] = {
            "guid": key,
            "name": ref.get("version_label") or ref.get("name", row.original_filename),
            "kind": ref.get("kind", kind),
            "selections": [],
        }
        files[key] = path
    users = {u["guid"]: u for u in state.get("users", [])}
    users.update(
        {
            stable_guid(c["id"]): {"guid": stable_guid(c["id"]), "name": c["name"]}
            for c in text_coding.local_coders(document, user)
        }
    )
    selections = {}
    for segment in document.segments:
        material = by_material.get(str(segment.material_id))
        if not material:
            raise invalid("存在无法导出的编码材料，请从标注中回查该材料。")
        await material_service.verify_version(material)
        source_id = source_ids.get(json.loads(material.metadata_json).get("source_dataset_id"))
        if not source_id:
            raise invalid(f"{segment.filename} 的编码来源不在可导出材料中。")
        original = state.get("segments", {}).get(str(segment.id))
        kind, coords = coords_for(segment, material, original)
        selection_guid = (
            original["selection"]["guid"]
            if original
            else stable_guid("selection:" + str(segment.id))
        )
        fingerprint = json.dumps([source_id, kind, coords, segment.memo], sort_keys=True)
        if selection_guid in selections and selections[selection_guid][0] != fingerprint:
            selection_guid = stable_guid("selection:" + str(segment.id))
        if selection_guid not in selections:
            selection_meta = original_selections.get(
                selection_guid, original["selection"] if original else {}
            )
            selected = {
                "guid": selection_guid,
                "kind": kind,
                "coordinates": coords,
                "name": original["selection"].get("name", "") if original else "",
                "description": segment.memo,
                "codings": [],
                "created_at": selection_meta.get("created_at")
                if original
                else citation_dates.get(str(segment.citation_id)),
                "user_guid": selection_meta.get("user_guid")
                if original
                else stable_guid(segment.coder_id or user.id),
            }
            sources[source_id]["selections"].append(selected)
            selections[selection_guid] = (fingerprint, selected)
        selected = selections[selection_guid][1]
        author = stable_guid(segment.coder_id or user.id)
        users.setdefault(author, {"guid": author, "name": segment.coder_name or "研究者"})
        for index, theme in enumerate(segment.theme_ids):
            selected["codings"].append(
                {
                    "guid": str(segment.id) if index == 0 else stable_guid(f"{segment.id}:{theme}"),
                    "code_guid": str(theme),
                    "user_guid": author,
                    "created_at": original["coding"].get("created_at")
                    if original
                    else citation_dates.get(str(segment.citation_id)),
                }
            )
    groups = {}
    for row in rows:
        if row.media_case:
            meta = json.loads(row.preview_json)
            groups[meta.get("media_group_id", str(row.id))] = row
    cases = []
    for group, row in groups.items():
        for sample in row.media_case.get("samples", []):
            members = sample.get("members", []) or list(sample.get("files", {}).values())
            refs = list(
                dict.fromkeys(source_ids[m["id"]] for m in members if m.get("id") in source_ids)
            )
            cases.append(
                {
                    "guid": sample.get("refi_case_guid")
                    or stable_guid(group + ":" + sample["sample_id"]),
                    "name": sample.get("case_title") or sample.get("title") or sample["sample_id"],
                    "source_guids": refs,
                }
            )
    from app.services.refi_codebook import PALETTE

    exported = {
        "name": project.title,
        "users": list(users.values()),
        "sources": list(sources.values()),
        "cases": cases,
        "codes": [
            {
                "guid": str(c.id),
                "name": c.name,
                "parent_guid": str(c.parent_id) if c.parent_id else None,
                "is_codable": c.is_codable,
                "color": c.color_rgb or PALETTE[c.color],
                "description": c.memo,
            }
            for c in document.themes
        ],
    }
    from app.services.refi_project_notes import capture_notes

    await capture_notes(db, project, user, state, exported, selections)
    refi.write_package(destination, exported, files)
