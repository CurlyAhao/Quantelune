"""Two-coder text agreement over complete, current, version-pinned sources."""

import csv
import io
import json
from bisect import bisect_left
from collections import defaultdict
from datetime import UTC
from types import SimpleNamespace

from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.material import Material
from app.schemas.coding import CodingDocument
from app.schemas.material import CitationRequest, TextLocation
from app.services import coding_attributes, coding_relations, material_service, text_coding

PAGE_SIZE = 40
UNIT = "Unicode 字符；CRLF 统一为 LF；区间含起点、不含终点"


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        elif end > start:
            result.append([start, end])
    return result


def compare(length, a, b):
    """Sweep interval endpoints, never allocate a cell for each character."""
    events = defaultdict(lambda: [0, 0])
    for side, intervals in enumerate((union(a), union(b))):
        for start, end in intervals:
            events[start][side] += 1
            events[end][side] -= 1
    events[0]
    events[length]
    counts = {"both": 0, "only_a": 0, "only_b": 0, "neither": 0}
    previous, active, differences = 0, [0, 0], []
    for point in sorted(events):
        kind = (
            "both"
            if all(active)
            else "only_a"
            if active[0]
            else "only_b"
            if active[1]
            else "neither"
        )
        counts[kind] += point - previous
        if point > previous and kind in {"only_a", "only_b"}:
            differences.append((previous, point, kind))
        active = [active[i] + events[point][i] for i in (0, 1)]
        previous = point
    return counts, differences


def metrics(counts):
    n = sum(counts.values())
    if not n:
        return {"total": 0, "agreement": None, "coded_overlap": None, "kappa": None}
    both, a, b, neither = (counts[k] for k in ("both", "only_a", "only_b", "neither"))
    coded = both + a + b
    # Integer form of (Po - Pe) / (1 - Pe) avoids cancellation near agreement=1.
    expected = (both + a) * (both + b) + (b + neither) * (a + neither)
    denominator = n * n - expected
    return {
        "total": n,
        "agreement": (both + neither) / n,
        "coded_overlap": both / coded if coded else None,
        "kappa": ((both + neither) * n - expected) / denominator if denominator else None,
    }


async def sources(db, project, user, state, modality="text"):
    from app.services import local_workspaces, multimodal_cases

    catalog = await coding_attributes.catalog(db, project, user)
    datasets = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == project, Dataset.upload_status != UploadStatus.DELETED
            )
        )
    ).all()
    by_id = {str(d.id): d for d in datasets}
    memberships = defaultdict(set)
    root = local_workspaces.project_root(project)
    for case in catalog["cases"]:
        d = by_id.get(case["collection_id"])
        if not d:
            continue
        if case["sample_id"] == str(d.id) and d.raw_file:
            memberships[(str(d.id), d.sha256)].add(case["key"])
    if root:
        for d in datasets:
            descriptor = json.loads(d.preview_json or "{}").get("media_case")
            if not descriptor:
                continue
            group = str(json.loads(d.preview_json or "{}").get("media_group_id") or d.id)
            samples = (
                descriptor["samples"]
                if descriptor["kind"] == "media"
                else await multimodal_cases.linked_samples(db, d, root, descriptor)
            )
            for sample in samples:
                refs = list(sample.get("members", []))
                for key, field in (
                    ("text", "text_id"),
                    ("audio", "audio_id"),
                    ("video_av", "video_id"),
                    ("image_path", "image_dataset_id"),
                ):
                    if sample.get(field):
                        pin = sample.get("files", {}).get(key, {}).get("sha256")
                        linked = by_id.get(str(sample[field]))
                        if (
                            pin is None
                            and descriptor["kind"] != "media"
                            and linked
                            and linked.local_file
                            and linked.source_path == str(sample.get(key, "")).replace("\\", "/")
                        ):
                            pin = linked.sha256
                        refs.append({"id": sample[field], "sha256": pin})
                for ref in refs:
                    member = by_id.get(str(ref.get("id")))
                    if member and ref.get("sha256") == member.sha256:
                        memberships[(str(member.id), member.sha256)].add(
                            f"{group}:{sample['sample_id']}"
                        )
    materials = (
        await db.scalars(
            select(Material).where(Material.project_id == project, Material.user_id == user.id)
        )
    ).all()
    result, excluded, probes = [], [], []
    for material in materials:
        kind = (
            "text"
            if material.mime_type in {"text/plain", "text/markdown", "application/pdf"}
            else material.mime_type.split("/")[0]
        )
        if kind not in {"text", "image", "audio", "video"} or modality not in {"all", kind}:
            continue
        source_id = json.loads(material.metadata_json or "{}").get("source_dataset_id")
        if kind != "text" and source_id:
            current_dataset = by_id.get(str(source_id))
            if (
                not current_dataset
                or current_dataset.sha256 != material.sha256
                or current_dataset.material_id != str(material.id)
            ):
                continue
        pages = json.loads(material.extraction_json or "{}").get("pages", [])
        if kind == "text" and not any(p.get("text") for p in pages):
            continue
        try:
            await material_service.verify_version(material)
            for page in pages:
                if page.get("text"):
                    material_service.validate_locator(
                        material,
                        CitationRequest(
                            material_sha256=material.sha256,
                            material_version=material.version,
                            reviewed=True,
                            locator=TextLocation(
                                kind="text",
                                page=page["page"],
                                start=0,
                                end=1,
                                text_sha256=page["sha256"],
                                quote=page["text"][:1],
                            ),
                        ),
                    )
        except StatForgeError:
            excluded.append(f"{material.filename}：原文件或提取文字已变化，请重新打开材料")
            continue
        if not source_id:
            source_id = next(
                (
                    str(d.id)
                    for d in datasets
                    if d.material_id == str(material.id) and d.sha256 == material.sha256
                ),
                None,
            )
        cases = memberships.get((source_id, material.sha256), set())
        if kind == "text":
            probe = SimpleNamespace(
                id=material.id,
                material_id=material.id,
                material_sha256=material.sha256,
                material_version=material.version,
                locator=SimpleNamespace(kind="text"),
            )
            probes.append(probe)
        result.append({"material": material, "kind": kind, "pages": pages, "cases": cases})
    details = await text_coding.transcription_details(
        db, project, user, SimpleNamespace(segments=probes)
    )
    current = []
    for source in result:
        material = source["material"]
        info = details.get(str(material.id), {})
        if info.get("status", "current") != "current":
            excluded.append(f"{material.filename}：不是当前可用的转录版本")
        else:
            case = info.get("case")
            if case:
                d = by_id.get(case["sample_collection_id"])
                if d:
                    group = str(json.loads(d.preview_json or "{}").get("media_group_id") or d.id)
                    source["cases"].add(f"{group}:{case['sample_id']}")
            current.append(source)
    return current, catalog["cases"], excluded


async def choices(db, project, user):
    state = await text_coding.read(db, project, user)
    document = CodingDocument.model_validate(state["payload"])
    items, cases, excluded = await sources(db, project, user, state, "all")
    coders = {c["id"]: c["name"] for c in text_coding.local_coders(document, user)}
    for segment in document.segments:
        if segment.coder_id:
            coders.setdefault(segment.coder_id, segment.coder_name or "已记录编码者")
    return {
        "revision": state["revision"],
        "coders": [{"id": k, "name": v} for k, v in coders.items()],
        "files": [
            {
                "id": str(s["material"].id),
                "name": s["material"].filename,
                "version": s["material"].version,
                "kind": s["kind"],
            }
            for s in items
        ],
        "cases": cases,
        "excluded": excluded,
    }


async def analyze(db, project, user, request, export=False, full=False):
    state = await text_coding.read(db, project, user)
    if state["revision"] != request.revision:
        raise text_coding.fail("标注已更新，请重新读取比较结果。")
    document = CodingDocument.model_validate(state["payload"])
    coders = {c["id"]: c["name"] for c in text_coding.local_coders(document, user)}
    coders.update(
        {s.coder_id: s.coder_name or "已记录编码者" for s in document.segments if s.coder_id}
    )
    if (
        request.coder_a == request.coder_b
        or not {request.coder_a, request.coder_b} <= coders.keys()
    ):
        raise text_coding.fail("请选择两位不同的编码者。", 422)
    theme = next((t for t in document.themes if t.id == request.theme_id and t.is_codable), None)
    if not theme:
        raise text_coding.fail("请选择一个可编码主题。", 422)
    items, cases, excluded = await sources(db, project, user, state, request.modality)
    case = next((c for c in cases if c["key"] == request.case_key), None)
    if request.case_key and not case:
        raise text_coding.fail("案例已不可用，请重新选择范围。", 422)
    conditions = coding_attributes.validated_conditions(document)
    scope = coding_attributes.scoped(state, document, case)
    current_segments = set(scope["current_segment_ids"])
    if request.modality != "text":
        from app.services.coding_agreement_media import analyze_media

        data = await analyze_media(
            items, document, request, state, scope, case, coders, theme, excluded
        )
        return export_result(data) if export else data if full else paginated(data, request.page)
    rows, all_differences, unrecorded = [], [], 0
    totals = {"both": 0, "only_a": 0, "only_b": 0, "neither": 0}
    segments = [
        s
        for s in document.segments
        if s.material_id and s.locator.kind == "text" and theme.id in s.theme_ids
    ]
    for source in items:
        material = source["material"]
        if request.material_id and material.id != request.material_id:
            continue
        case_keys = source["cases"]
        if request.case_key:
            if request.case_key not in case_keys:
                continue
            case_keys = {request.case_key}
        if not any(
            coding_attributes.matches_source(
                document, conditions, key, material.id, material.sha256, material.version
            )
            for key in (case_keys or {None})
        ):
            continue
        valid = []
        for segment in segments:
            if segment.material_id != material.id or str(segment.id) not in current_segments:
                continue
            if (
                segment.material_sha256 != material.sha256
                or segment.material_version != material.version
            ):
                continue
            if not segment.coder_id:
                unrecorded += 1
                continue
            if segment.coder_id not in {request.coder_a, request.coder_b}:
                continue
            try:
                material_service.validate_locator(
                    material,
                    CitationRequest(
                        material_sha256=material.sha256,
                        material_version=material.version,
                        locator=segment.locator,
                        reviewed=True,
                    ),
                )
            except (StatForgeError, ValueError):
                raise text_coding.fail(
                    f"{material.filename} 的标注位置与文字不一致，请回到原文核对。"
                ) from None
            valid.append(segment)
        for page in source["pages"]:
            raw = page["text"]
            crlf = [i for i in range(len(raw) - 1) if raw[i : i + 2] == "\r\n"]
            lf_breaks = [value - i for i, value in enumerate(crlf)]
            n = len(raw) - len(crlf)
            if not n:
                continue
            intervals = {identity: [] for identity in (request.coder_a, request.coder_b)}
            for segment in valid:
                loc = segment.locator
                if loc.page == page["page"] and loc.text_sha256 == page["sha256"]:
                    intervals[segment.coder_id].append(
                        (
                            loc.start - bisect_left(crlf, loc.start),
                            loc.end - bisect_left(crlf, loc.end),
                        )
                    )
            counts, differences = compare(n, intervals[request.coder_a], intervals[request.coder_b])
            target = {
                "material_id": str(material.id),
                "material_sha256": material.sha256,
                "material_version": material.version,
                "filename": material.filename,
            }
            row = {
                **target,
                "page": page["page"],
                "start": 0,
                "end": n,
                "raw_start": 0,
                "raw_end": len(raw),
                **counts,
                **metrics(counts),
            }
            rows.append(row)
            for key in totals:
                totals[key] += counts[key]
            for start, end, kind in differences:
                # Bounded excerpts keep the existing locator contract; all coordinates remain
                # exported.
                for part in range(start, end, 1000):
                    stop = min(part + 1000, end)
                    raw_start = part + bisect_left(lf_breaks, part)
                    raw_end = stop + bisect_left(lf_breaks, stop)
                    locator = TextLocation(
                        kind="text",
                        page=page["page"],
                        start=raw_start,
                        end=raw_end,
                        text_sha256=page["sha256"],
                        quote=raw[raw_start:raw_end],
                    )
                    material_service.validate_locator(
                        material,
                        CitationRequest(
                            material_sha256=material.sha256,
                            material_version=material.version,
                            locator=locator,
                            reviewed=True,
                        ),
                    )
                    all_differences.append(
                        {
                            "kind": kind,
                            "start": part,
                            "end": stop,
                            "target": {**target, "locator": locator.model_dump()},
                            "source_index": len(rows) - 1,
                        }
                    )
    summary = {**totals, **metrics(totals)}
    scope_label = f"案例 · {case['title']}" if case else "全项目"
    scope_label += " · " + (
        rows[0]["filename"] if request.material_id and rows else "所选已读取文字"
    )
    if scope["description"]:
        scope_label += " · " + scope["description"]
    title = coding_relations.theme_names(document.themes)[str(theme.id)]
    base = {
        "revision": state["revision"],
        "scope_label": scope_label,
        "unit": UNIT,
        "modality": "text",
        "request": request.model_dump(mode="json"),
        "scope": scope,
        "coder_a": {"id": request.coder_a, "name": coders[request.coder_a]},
        "coder_b": {"id": request.coder_b, "name": coders[request.coder_b]},
        "theme": {"id": str(theme.id), "name": title},
        "summary": summary,
        "sources": rows,
        "unrecorded": unrecorded,
        "excluded": excluded,
    }
    data = {**base, "differences": all_differences}
    return export_result(data) if export else data if full else paginated(data, request.page)


def paginated(data, page=0):
    return {
        **data,
        "differences": data["differences"][page * PAGE_SIZE : (page + 1) * PAGE_SIZE],
        "total_differences": len(data["differences"]),
        "page_size": PAGE_SIZE,
    }


def export_result(data):
    if data.get("modality", "text") != "text":
        from app.services.coding_agreement_media import export_media

        return export_media(data)
    scope_label, title = data["scope_label"], data["theme"]["name"]
    rows, summary, all_differences = data["sources"], data["summary"], data["differences"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "记录类型",
            "范围",
            "编码修订",
            "编码者A_ID",
            "编码者A",
            "编码者B_ID",
            "编码者B",
            "主题ID",
            "主题",
            "单位",
            "来源",
            "材料ID",
            "材料版本",
            "材料SHA256",
            "页码",
            "范围起点_LF",
            "范围终点_LF",
            "原文起点",
            "原文终点",
            "双方编码",
            "仅A编码",
            "仅B编码",
            "双方未编码",
            "总字符数",
            "总体一致率",
            "已编码交并比",
            "Cohen_kappa",
            "分歧",
            "分歧起点_LF",
            "分歧终点_LF",
            "分歧原文起点",
            "分歧原文终点",
            "原文",
        ]
    )

    def write(kind, row, difference=None):
        d = difference or {}
        loc = d.get("target", {}).get("locator", {})
        writer.writerow(
            [
                kind,
                scope_label,
                data["revision"],
                data["coder_a"]["id"],
                data["coder_a"]["name"],
                data["coder_b"]["id"],
                data["coder_b"]["name"],
                data["theme"]["id"],
                title,
                UNIT,
                *[
                    row.get(k, "")
                    for k in (
                        "filename",
                        "material_id",
                        "material_version",
                        "material_sha256",
                        "page",
                        "start",
                        "end",
                        "raw_start",
                        "raw_end",
                    )
                ],
                *[
                    row.get(k)
                    for k in (
                        "both",
                        "only_a",
                        "only_b",
                        "neither",
                        "total",
                        "agreement",
                        "coded_overlap",
                        "kappa",
                    )
                ],
                {"only_a": "仅A编码", "only_b": "仅B编码"}.get(d.get("kind"), ""),
                d.get("start", ""),
                d.get("end", ""),
                loc.get("start", ""),
                loc.get("end", ""),
                loc.get("quote", ""),
            ]
        )

    if rows:
        write("合计", summary)
        for row in rows:
            write("来源", row)
        for d in all_differences:
            write("分歧", rows[d["source_index"]], d)
    return {"csv": "\ufeff" + output.getvalue(), "filename": "文本编码者一致性.csv"}


async def save(db, project, user, request):
    import tempfile
    from datetime import datetime
    from pathlib import Path

    import polars as pl

    from app.preview.write_fence import acquire_write_fence
    from app.services import dataset_service

    await acquire_write_fence(db, user.id)
    data = await analyze(db, project, user, request, full=True)
    if not data["sources"]:
        raise text_coding.fail("当前范围没有可保存的来源。", 422)
    title = (
        {"text": "文字", "image": "图片", "audio": "音频", "video": "视频"}[request.modality]
        + "编码比较 · "
        + data["theme"]["name"]
    )
    values = [
        {
            "材料": row["filename"],
            "来源版本": row["material_version"],
            "单位": row.get("unit", "字符"),
            **{
                key: row[key]
                for key in (
                    "both",
                    "only_a",
                    "only_b",
                    "neither",
                    "total",
                    "agreement",
                    "coded_overlap",
                    "kappa",
                )
            },
        }
        for row in data["sources"]
    ]
    with tempfile.TemporaryDirectory(prefix="sf-agreement-") as folder:
        path = Path(folder) / "agreement.parquet"
        pl.DataFrame(values).write_parquet(path)
        dataset = await dataset_service.upload_dataset(
            db, project, user.id, str(path), title + ".parquet", allow_duplicate=True
        )
    data["saved_at"] = datetime.now(UTC).isoformat()
    metadata = json.loads(dataset.preview_json or "{}")
    metadata.update(display_name=title, coding_agreement=data)
    dataset.preview_json = json.dumps(metadata, ensure_ascii=False)
    await db.commit()
    return {"id": str(dataset.id), "title": title, "saved_at": data["saved_at"]}


async def snapshot(db, project, user, identity):
    from app.services import dataset_service

    dataset = await dataset_service.get_dataset_with_access(db, identity, user.id)
    data = json.loads(dataset.preview_json or "{}").get("coding_agreement")
    if dataset.project_id != project or not data:
        raise text_coding.fail("此比较不属于当前项目或已不可读取。", 404)
    return data


async def history(db, project, user):
    from app.services import dataset_service

    await material_service.project_access(db, project, user.id)
    datasets = await dataset_service.list_project_datasets(db, project, user.id)
    result = []
    for dataset in datasets:
        meta = json.loads(dataset.preview_json or "{}")
        data = meta.get("coding_agreement")
        if data and dataset.upload_status != UploadStatus.DELETED:
            result.append(
                {
                    "id": str(dataset.id),
                    "title": meta.get("display_name") or dataset.original_filename,
                    "saved_at": data["saved_at"],
                    "scope_label": data["scope_label"],
                }
            )
    return sorted(result, key=lambda r: r["saved_at"], reverse=True)
