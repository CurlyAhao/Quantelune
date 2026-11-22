"""Case/file evidence counts and immutable summaries over the existing coding scope."""

import csv
import io
import json
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.models.dataset import Dataset, UploadStatus
from app.models.material import Material
from app.preview.write_fence import acquire_write_fence
from app.schemas.coding import CodingDocument
from app.services import coding_attributes, dataset_service, material_service, text_coding
from app.services.coding_relations import theme_names


def source_key(segment):
    if segment.material_id and segment.material_sha256 and segment.material_version:
        return (
            f"material:{segment.material_id}:{segment.material_sha256}:{segment.material_version}"
        )
    if segment.dataset_id and segment.dataset_sha256:
        return f"dataset\
:{segment.dataset_id}:{segment.dataset_sha256}:{segment.source_fingerprint or ''}"
    return None


async def inventory(db, project, user, state):
    """Include unannotated sources without inventing cases or filename associations."""
    from app.services import local_workspaces, multimodal_cases

    datasets = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == project, Dataset.upload_status != UploadStatus.DELETED
            )
        )
    ).all()
    materials = (
        await db.scalars(
            select(Material).where(Material.project_id == project, Material.user_id == user.id)
        )
    ).all()
    by_id = {str(d.id): d for d in datasets}
    metadata = {str(d.id): json.loads(d.preview_json or "{}") for d in datasets}
    from app.services.survey_import import latest

    surveys = latest(datasets)
    current_surveys = {str(r.id) for r in surveys.values()}
    current_answers = {
        a["dataset_id"]
        for r in surveys.values()
        for s in r.media_case["samples"]
        for a in s.get("answers", {}).values()
    }
    formats = {"txt", "md", "pdf", "png", "jpg", "jpeg", "gif", "webp", "wav", "mp4"}

    def original(d):
        meta = metadata[str(d.id)]
        if meta.get("survey_answer") and str(d.id) not in current_answers:
            return False
        return (
            d.raw_file
            and d.file_format in formats
            and not any(
                meta.get(k)
                for k in (
                    "media_case",
                    "media_result",
                    "visual_result",
                    "transcription",
                    "coding_summary",
                    "coding_matrix",
                    "preparation",
                )
            )
        )

    eligible = {str(d.id) for d in datasets if original(d)}
    cases = {c["key"]: c for c in state.get("case_scopes", [])}
    members = defaultdict(set)
    root = local_workspaces.project_root(project)
    for d in datasets:
        meta, identity = metadata[str(d.id)], str(d.id)
        if meta.get("survey") and identity not in current_surveys:
            continue
        descriptor = meta.get("media_case")
        group = str(meta.get("media_group_id") or identity)
        if descriptor and root:
            samples = (
                descriptor["samples"]
                if descriptor["kind"] == "media"
                else await multimodal_cases.linked_samples(db, d, root, descriptor)
            )
            for sample in samples:
                key = f"{group}:{sample['sample_id']}"
                cases.setdefault(
                    key,
                    {
                        "key": key,
                        "group_id": group,
                        "collection_id": identity,
                        "sample_id": str(sample["sample_id"]),
                        "title": sample.get("title") or str(sample["sample_id"]),
                        "group_title": meta.get("display_name") or d.original_filename,
                    },
                )
                refs = list(sample.get("members", []))
                for role, field in (
                    ("text", "text_id"),
                    ("audio", "audio_id"),
                    ("video_av", "video_id"),
                    ("image_path", "image_dataset_id"),
                ):
                    if sample.get(field):
                        pin = sample.get("files", {}).get(role, {}).get("sha256")
                        linked = by_id.get(str(sample[field]))
                        if (
                            pin is None
                            and descriptor["kind"] != "media"
                            and linked
                            and linked.local_file
                            and linked.source_path == str(sample.get(role, "")).replace("\\", "/")
                        ):
                            pin = (
                                linked.sha256
                            )  # Existing local-workspace path resolved to this exact dataset.
                        refs.append({"id": sample[field], "sha256": pin})
                for ref in refs:
                    source = by_id.get(str(ref.get("id")))
                    if source and ref.get("sha256") == source.sha256:
                        members[(str(source.id), source.sha256)].add(key)
        elif identity in eligible and not meta.get("survey_answer"):
            key = f"{group}:{identity}"
            # A standalone source is already an existing case choice in the coding catalog.
            cases.setdefault(
                key,
                {
                    "key": key,
                    "group_id": group,
                    "collection_id": identity,
                    "sample_id": identity,
                    "title": d.original_filename,
                    "group_title": d.original_filename,
                },
            )
            members[(identity, d.sha256)].add(key)
    probes = [
        SimpleNamespace(
            id=m.id,
            material_id=m.id,
            material_sha256=m.sha256,
            material_version=m.version,
            locator=SimpleNamespace(kind="text"),
        )
        for m in materials
        if m.mime_type in {"text/plain", "text/markdown", "application/pdf"}
    ]
    transcript = await text_coding.transcription_details(
        db, project, user, SimpleNamespace(segments=probes)
    )
    rows, covered = {}, set()
    for m in materials:
        source_id = json.loads(m.metadata_json or "{}").get("source_dataset_id")
        d = by_id.get(source_id) or next((d for d in datasets if d.material_id == str(m.id)), None)
        if source_id and not d:
            continue
        info = transcript.get(str(m.id), {})
        if info.get("status", "current") != "current":
            continue
        if d and (
            d.sha256 != m.sha256
            or d.material_id != str(m.id)
            or (str(d.id) not in eligible and not info.get("case"))
        ):
            continue
        if Path(m.filename).suffix.lower().lstrip(".") not in formats:
            continue
        belonging = set(members.get((str(d.id), m.sha256), set())) if d else set()
        case = info.get("case")
        if case and case["sample_collection_id"] in by_id:
            collection = by_id[case["sample_collection_id"]]
            group = str(metadata[str(collection.id)].get("media_group_id") or collection.id)
            belonging.add(f"{group}:{case['sample_id']}")
        key = f"material:{m.id}:{m.sha256}:{m.version}"
        rows[key] = {"id": key, "name": m.filename, "detail": f"v{m.version}", "cases": belonging}
        if d:
            covered.add(str(d.id))
    for identity in eligible - covered:
        d = by_id[identity]
        key = f"dataset:{d.id}:{d.sha256}:"
        rows[key] = {
            "id": key,
            "name": d.original_filename,
            "detail": d.source_path or "",
            "cases": members.get((identity, d.sha256), set()),
        }
    return cases, rows


async def analyze(db, project, user, request):
    state = await text_coding.read(db, project, user)
    if state["revision"] != request.revision:
        raise text_coding.fail("标注已变化，请刷新证据矩阵后重试。")
    document = CodingDocument.model_validate(state["payload"])
    cases, source_rows = await inventory(db, project, user, state)
    selected_case = cases.get(request.case_key) if request.case_key else None
    if request.case_key and not selected_case:
        raise text_coding.fail("此案例已不可用，请重新选择范围。", 422)
    applied = coding_attributes.scoped(state, document, selected_case)
    eligible = set(applied["current_segment_ids"])
    names = theme_names(document.themes)
    themes = {str(t.id): t for t in document.themes}
    selected = (
        set(map(str, request.theme_ids))
        if request.theme_ids is not None
        else {str(t.id) for t in document.themes if t.is_codable}
    )
    if not selected <= themes.keys():
        raise text_coding.fail("主题已变化，请重新选择。", 422)
    columns = [
        {"id": key, "name": names[key], "is_group": not theme.is_codable}
        for key, theme in themes.items()
        if key in selected
    ]
    descendants = {}
    for column in columns:
        identity = column["id"]
        descendants[identity] = {identity} if themes[identity].is_codable else set()
        if column["is_group"]:
            for key, theme in themes.items():
                parent = theme.parent_id
                while parent:
                    if str(parent) == identity and theme.is_codable:
                        descendants[identity].add(key)
                        break
                    parent = themes[str(parent)].parent_id
    segment_cases = state.get("segment_cases", {})
    current = [
        s
        for s in document.segments
        if state.get("transcription_status", {}).get(str(s.id), "current") == "current"
    ]
    rows = {}
    # Retain real row identities before attribute filtering so filtered cells remain explicit zero.
    if request.row_kind == "case":
        rows = {
            key: {"id": key, "name": c["title"], "detail": c["group_title"]}
            for key, c in cases.items()
            if not selected_case or key == selected_case["key"]
        }
    else:
        rows = {
            key: {field: value for field, value in row.items() if field != "cases"}
            for key, row in source_rows.items()
            if not selected_case or selected_case["key"] in row["cases"]
        }
        for segment in current:
            if selected_case and segment_cases.get(str(segment.id)) != selected_case["key"]:
                continue
            key = source_key(segment)
            if key:
                rows[key] = {
                    "id": key,
                    "name": segment.filename,
                    "detail": f"v{segment.material_version}"
                    if segment.material_version
                    else "信号来源",
                }
    entries, unassociated, unavailable = [], 0, 0
    for segment in current:
        if str(segment.id) not in eligible:
            continue
        key = source_key(segment)
        if not key:
            unavailable += 1
            continue
        case_key = segment_cases.get(str(segment.id))
        if not case_key:
            unassociated += 1
        row_key = case_key if request.row_kind == "case" else key
        if row_key not in rows:
            continue
        labels = set(map(str, segment.theme_ids))
        memberships = [c["id"] for c in columns if descendants[c["id"]] & labels]
        if memberships:
            entries.append(
                {
                    "row_id": row_key,
                    "column_ids": memberships,
                    "case_key": case_key,
                    "segment": segment.model_dump(mode="json"),
                }
            )
    entries.sort(key=lambda e: (e["row_id"], e["segment"]["filename"], e["segment"]["id"]))
    cells = {(row_id, c["id"]): set() for row_id in rows for c in columns}
    for item in entries:
        for column in item["column_ids"]:
            cells[item["row_id"], column].add(item["segment"]["id"])
    return {
        "schema_version": 1,
        "coding_id": state["id"],
        "coding_revision": state["revision"],
        "row_kind": request.row_kind,
        "request": request.model_dump(mode="json"),
        "scope": applied,
        "rows": list(rows.values()),
        "columns": columns,
        "matrix": [[len(cells[key, c["id"]]) for c in columns] for key in rows],
        "entries": entries,
        "unassociated": unassociated,
        "unavailable": unavailable,
        "version_scope": "current_transcription_versions",
        "case_scopes": list(cases.values()),
    }


def safe(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export(data, format):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    scope, revision = data["scope"]["scope_label"], data["coding_revision"]
    if format == "matrix":
        writer.writerow(
            [
                "范围",
                "编码版本",
                "行类型",
                "对象ID",
                "对象",
                "归属",
                *[safe(f"{c['name']} [{c['id']}]") for c in data["columns"]],
            ]
        )
        for row, counts in zip(data["rows"], data["matrix"]):
            writer.writerow(
                [
                    safe(scope),
                    revision,
                    data["row_kind"],
                    row["id"],
                    safe(row["name"]),
                    safe(row["detail"]),
                    *counts,
                ]
            )
    else:
        writer.writerow(
            [
                "范围",
                "编码版本",
                "对象ID",
                "对象",
                "主题ID",
                "主题",
                "标注ID",
                "材料",
                "材料ID",
                "来源SHA256",
                "来源版本",
                "位置类型",
                "页码",
                "起点",
                "终点",
                "区域X",
                "区域Y",
                "区域宽",
                "区域高",
                "通道",
                "窗口",
                "原文",
                "备注",
                "编码者ID",
                "编码者",
                "标注来源",
                "案例ID",
                "位置记录",
            ]
        )
        rows = {r["id"]: r for r in data["rows"]}
        columns = {c["id"]: c for c in data["columns"]}
        for entry in data["entries"]:
            s, loc = entry["segment"], entry["segment"]["locator"]
            for column in entry["column_ids"]:
                writer.writerow(
                    [
                        safe(scope),
                        revision,
                        entry["row_id"],
                        safe(rows[entry["row_id"]]["name"]),
                        column,
                        safe(columns[column]["name"]),
                        s["id"],
                        safe(s["filename"]),
                        s.get("material_id") or s.get("dataset_id"),
                        s.get("material_sha256") or s.get("dataset_sha256"),
                        s.get("material_version"),
                        loc["kind"],
                        loc.get("page"),
                        loc.get("start", loc.get("start_seconds")),
                        loc.get("end", loc.get("end_seconds")),
                        loc.get("x"),
                        loc.get("y"),
                        loc.get("width"),
                        loc.get("height"),
                        safe(" / ".join(loc.get("channel_ids", []))),
                        loc.get("window_index"),
                        safe(loc.get("quote") or (loc.get("transcription") or {}).get("quote", "")),
                        safe(s.get("memo")),
                        s.get("coder_id"),
                        safe(s.get("coder_name")),
                        s.get("origin"),
                        entry.get("case_key"),
                        safe(json.dumps(loc, ensure_ascii=False)),
                    ]
                )
    return stream.getvalue().encode("utf-8-sig")


async def save(db, project, user, request):
    import polars as pl

    await acquire_write_fence(db, user.id)
    data = await analyze(db, project, user, request)
    if not data["columns"] or not data["rows"]:
        raise text_coding.fail("当前范围没有可保存的矩阵，请选择案例、文件和主题。", 422)
    names, used = [], {"对象ID", "对象", "归属"}
    for column in data["columns"]:
        name, index = column["name"], 2
        while name in used:
            name = f"{column['name']} ({index})"
            index += 1
        names.append(name)
        used.add(name)
    values = [
        {"对象ID": r["id"], "对象": r["name"], "归属": r["detail"], **dict(zip(names, counts))}
        for r, counts in zip(data["rows"], data["matrix"])
    ]
    title = "证据矩阵 · " + ("案例" if data["row_kind"] == "case" else "文件")
    with tempfile.TemporaryDirectory(prefix="sf-coding-matrix-") as folder:
        path = Path(folder) / "matrix.parquet"
        pl.DataFrame(values).write_parquet(path)
        dataset = await dataset_service.upload_dataset(
            db, project, user.id, str(path), title + ".parquet", allow_duplicate=True
        )
    data["saved_at"] = datetime.now(UTC).isoformat()
    meta = json.loads(dataset.preview_json or "{}")
    meta.update({"display_name": title, "coding_matrix": data})
    dataset.preview_json = json.dumps(meta, ensure_ascii=False)
    await db.commit()
    return {"id": str(dataset.id), "title": title, "saved_at": data["saved_at"]}


async def snapshot(db, project, user, identity):
    dataset = await dataset_service.get_dataset_with_access(db, identity, user.id)
    data = json.loads(dataset.preview_json or "{}").get("coding_matrix")
    if dataset.project_id != project or not data:
        raise text_coding.fail("此汇总不属于当前项目或已不可读取。", 404)
    return data


async def history(db, project, user):
    await material_service.project_access(db, project, user.id)
    datasets = await dataset_service.list_project_datasets(db, project, user.id)
    result = []
    for dataset in datasets:
        meta = json.loads(dataset.preview_json or "{}")
        data = meta.get("coding_matrix")
        if data and dataset.upload_status != "deleted":
            result.append(
                {
                    "id": str(dataset.id),
                    "title": meta.get("display_name") or dataset.original_filename,
                    "saved_at": data["saved_at"],
                    "scope_label": data["scope"]["scope_label"],
                }
            )
    return sorted(result, key=lambda r: r["saved_at"], reverse=True)
