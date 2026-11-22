"""Merge QDPX objects into the existing project, retaining source and coding versions."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.dataset import Dataset, UploadStatus
from app.models.material import Material
from app.models.workspace import WorkspaceRecord
from app.preview.workspace import read_record, save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.coding import CodingDocument, LocalCoder, Theme
from app.schemas.material import CitationRequest
from app.schemas.workspace import SaveRecord
from app.services import local_workspaces as local
from app.services import material_service, project_sources, storage_service, text_coding
from app.services import refi_project as exchange
from app.services import refi_project_format as refi
from app.services.archive_staging import ExpandedBudget, require_disk_space


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_digest(archive, source, budget):
    if source.get("member_path"):
        with archive.open(source["member_path"]) as stream:
            digest = hashlib.sha256()
            for chunk in budget.chunks(stream):
                digest.update(chunk)
            return digest.hexdigest()
    if "plain_text_content" in source:
        return hashlib.sha256(source["plain_text_content"].encode("utf-8")).hexdigest()
    return None


def coding_key(source_guid, sha, selection, coding):
    return digest(
        [
            source_guid,
            sha,
            selection["guid"],
            selection["kind"],
            selection["coordinates"],
            selection.get("description", ""),
            coding,
        ]
    )


def latest_groups(rows):
    groups = {}
    for row in rows:
        if row.media_case:
            meta = json.loads(row.preview_json)
            groups[meta.get("media_group_id", str(row.id))] = row
    return groups


async def prepare(db, user, project, archive_path):
    root = local.project_root(project.id)
    if not root:
        raise exchange.invalid("请先在本地工作区打开目标项目，再合并 QDPX。")
    local.checked_path(root)
    budget = ExpandedBudget()
    try:
        package = refi.read_package(archive_path, budget)
    except (ValueError, OSError) as exc:
        raise exchange.invalid(f"无法读取此 QDPX 项目：{exc}") from None
    state = exchange.state_for(project)
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
    material_by_dataset = {
        json.loads(m.metadata_json).get("source_dataset_id"): m for m in materials
    }
    by_material = {str(m.id): m for m in materials}
    issues = [exchange.issue_text(i) for i in package["issues"]]
    conflicts, source_plans = [], []
    raw_rows = [r for r in rows if r.raw_file and not r.media_case]
    identities = {
        json.loads(r.preview_json).get("refi_source", {}).get("guid"): r for r in raw_rows
    }
    reserved = {str(r.source_path).casefold() for r in rows}
    with ZipFile(archive_path) as archive:
        for source in package["sources"]:
            try:
                sha = source_digest(archive, source, budget)
            except (ValueError, OSError) as exc:
                raise exchange.invalid(f"无法读取此 QDPX 项目：{exc}") from None
            if sha is None:
                continue
            name = exchange.safe_name(source["name"])
            if not Path(name).suffix:
                name += Path(source["member_path"]).suffix if source.get("member_path") else ".txt"
            # Exact bytes of the same media kind imply the same embedded clock.
            # A filename alone is never a source identity.
            candidates = [
                r
                for r in raw_rows
                if r.sha256 == sha
                and json.loads(r.preview_json).get("refi_source", {}).get("kind", "")
                == source["kind"]
            ]
            preferred = identities.get(source["guid"])
            if preferred in candidates:
                candidates.remove(preferred)
                candidates.insert(0, preferred)
            row = None
            for candidate in candidates:
                path = storage_service.get_local_object_path(candidate.object_key)
                if not path.is_file() or file_digest(path) != sha:
                    raise exchange.invalid(
                        f"「{candidate.original_filename}」原文件已变化，请刷新后重新预览合并。"
                    )
                row = candidate
                break
            same_name = [
                r
                for r in raw_rows
                if (
                    json.loads(r.preview_json).get("refi_source", {}).get("name")
                    or r.original_filename
                ).casefold()
                == source["name"].casefold()
            ]
            conflict = row is None and (same_name or preferred)
            if conflict:
                name = (
                    f"{Path(name).stem}（{exchange.safe_name(package['name'])[:45]}）"
                    f"{Path(name).suffix}"
                )
                conflicts.append(
                    {
                        "kind": "source",
                        "name": source["name"],
                        "detail": f"原文不同，另存为「{name}」；已有编码仍使用原版本。",
                    }
                )
            relative = "材料/" + name
            counter = 2
            while row is None and (relative.casefold() in reserved or (root / relative).exists()):
                relative = f"材料/{Path(name).stem} ({counter}){Path(name).suffix}"
                counter += 1
            reserved.add(relative.casefold())
            export_guid = (
                json.loads(row.preview_json).get("refi_source", {}).get("guid", str(row.id))
                if row
                else source["guid"]
            )
            if not row and source["guid"] in identities:
                export_guid = exchange.stable_guid(f"source:{source['guid']}:{sha}")
            source_plans.append(
                {
                    "source": source,
                    "sha": sha,
                    "row": row,
                    "relative": row.source_path if row else relative,
                    "export_guid": export_guid,
                }
            )
    themes = list(document.themes)
    code_map = {}
    for code in package["codes"]:
        old = next((t for t in themes if str(t.id) == code["guid"]), None)
        parent = code_map.get(code["parent_guid"], code["parent_guid"])
        proposed = Theme(
            id=code["guid"],
            name=code["name"],
            parent_id=parent,
            is_codable=code["is_codable"],
            color_rgb=code.get("color"),
            memo=code["description"],
        )
        if old and any(
            getattr(old, k) != getattr(proposed, k)
            for k in ("name", "parent_id", "is_codable", "memo", "color_rgb")
        ):
            proposed.id = uuid.UUID(exchange.stable_guid("theme:" + digest(code)))
            proposed.name = code["name"][:53] + "（导入版本）"
            conflicts.append(
                {"kind": "code", "name": code["name"], "detail": "主题设置不同，保留两个版本。"}
            )
        code_map[code["guid"]] = str(proposed.id)
        if not any(t.id == proposed.id for t in themes):
            themes.append(proposed)
    coders = list(document.local_coders)
    coder_map = {}
    for author in package["users"]:
        old = next((c for c in coders if c.id == author["guid"]), None)
        key = author["guid"]
        if old and old.name != author["name"]:
            key = exchange.stable_guid("user:" + digest(author))
            conflicts.append(
                {
                    "kind": "coder",
                    "name": author["name"],
                    "detail": "同一作者标识的名称不同，保留双方记录。",
                }
            )
        coder_map[author["guid"]] = key
        if not any(c.id == key for c in coders):
            coders.append(LocalCoder(id=key, name=author["name"]))
    seen = dict(state.get("coding_fingerprints", {}))
    for segment in document.segments:
        original = state.get("segments", {}).get(str(segment.id))
        material = by_material.get(str(segment.material_id))
        if original and material:
            seen.setdefault(
                coding_key(
                    original["source_guid"],
                    original.get("source_sha", material.sha256),
                    original["selection"],
                    original["coding"],
                ),
                str(segment.id),
            )
    coding_plans, skipped = [], 0
    segment_ids = {str(s.id) for s in document.segments}
    for plan in source_plans:
        source = plan["source"]
        for selected in source["selections"]:
            for coding in selected["codings"]:
                key = coding_key(source["guid"], plan["sha"], selected, coding)
                if key in seen:
                    skipped += 1
                    continue
                identity = coding["guid"]
                if identity in segment_ids:
                    identity = exchange.stable_guid("coding:" + key)
                segment_ids.add(identity)
                coding_plans.append(
                    {
                        "source": plan,
                        "selection": selected,
                        "coding": coding,
                        "key": key,
                        "id": identity,
                    }
                )
    groups = latest_groups(rows)
    existing_cases = {
        sample.get("refi_case_guid"): (group, sample)
        for group, row in groups.items()
        for sample in row.media_case.get("samples", [])
        if sample.get("refi_case_guid")
    }
    for case in package["cases"]:
        old = existing_cases.get(case["guid"])
        if old and old[1].get("case_title") != case["name"]:
            conflicts.append(
                {
                    "kind": "case",
                    "name": case["name"],
                    "detail": "同一案例的名称不同，保留目标项目名称并合入材料关联。",
                }
            )
    from app.services import project_memo

    memo_record = await project_memo.current(db, project.id, user)
    if (
        memo_record
        and package.get("description")
        and json.loads(memo_record.payload_json).get("body") != package["description"]
    ):
        conflicts.append(
            {
                "kind": "memo",
                "name": "项目备忘",
                "detail": "保留本项目备忘，导入方备忘另存为研究日志。",
            }
        )
    # Bind the preview to current target state and the selected archive.
    record_versions = sorted(
        (str(r.id), r.revision)
        for r in (
            await db.scalars(
                select(WorkspaceRecord).where(WorkspaceRecord.project_id == project.id)
            )
        ).all()
    )
    token = digest(
        [
            file_digest(archive_path),
            record_versions,
            [(str(r.id), r.sha256, r.preview_json) for r in rows],
            state,
        ]
    )
    added_journals = 0
    for note in package.get("notes", []):
        if not (note.get("body") or "").lstrip("\ufeff").strip():
            continue
        existing_notes = [
            v
            for v in state.get("notes", {}).values()
            if v.get("metadata", {}).get("guid") == note["guid"]
        ]
        if any(v.get("metadata") == note for v in existing_notes):
            continue
        added_journals += 1
        if existing_notes:
            conflicts.append(
                {
                    "kind": "journal",
                    "name": note.get("name") or "未命名日志",
                    "detail": "日志内容或署名时间不同，另存导入版本。",
                }
            )
    preview = {
        "token": token,
        "project_name": package["name"],
        "added_journals": added_journals,
        "sources": [
            {
                "name": p["source"]["name"],
                "saved_name": Path(p["relative"]).name,
                "action": "reuse" if p["row"] else "add",
            }
            for p in source_plans
        ],
        "added_sources": sum(p["row"] is None for p in source_plans),
        "reused_sources": sum(p["row"] is not None for p in source_plans),
        "added_codings": len(coding_plans),
        "reused_codings": skipped,
        "conflicts": conflicts,
        "items": issues,
    }
    try:
        CodingDocument.model_validate(
            {**document.model_dump(mode="json"), "themes": themes, "local_coders": coders}
        )
        if len(document.segments) + len(coding_plans) > 2000:
            raise ValueError("合并后编码数量超过当前项目支持的范围。")
    except ValueError as exc:
        raise exchange.invalid(f"此项目不能合并：{exc}") from None
    return dict(
        root=root,
        budget=budget,
        package=package,
        state=state,
        record=record,
        document=document,
        rows=rows,
        materials=material_by_dataset,
        source_plans=source_plans,
        coding_plans=coding_plans,
        fingerprints=seen,
        themes=themes,
        coders=coders,
        code_map=code_map,
        coder_map=coder_map,
        groups=groups,
        existing_cases=existing_cases,
        preview=preview,
        issues=issues,
    )


async def merge_cases(db, user, project, prepared, source_rows):
    """Append to the existing immutable group version; preserve its case metadata."""
    descriptors, case_groups = {}, {}
    new_cases = []
    for case in prepared["package"]["cases"]:
        match = prepared["existing_cases"].get(case["guid"])
        if not match:
            new_cases.append(case)
            continue
        group, old_sample = match
        row = prepared["groups"][group]
        descriptor = descriptors.setdefault(group, copy.deepcopy(row.media_case))
        sample = next(s for s in descriptor["samples"] if s.get("refi_case_guid") == case["guid"])
        members = sample.setdefault("members", list(sample.get("files", {}).values()))
        for key in case["source_guids"]:
            source = source_rows.get(key)
            if source and not any(m.get("id") == str(source.id) for m in members):
                members.append({"id": str(source.id), "sha256": source.sha256})
        case_groups[case["guid"]] = (row, sample)
    for group, descriptor in descriptors.items():
        old = prepared["groups"][group]
        if descriptor == old.media_case:
            continue
        with tempfile.TemporaryDirectory(prefix="sf-qdpx-cases-") as directory:
            path = Path(directory) / "cases.sfmedia"
            path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
            row = await project_sources.store(
                db, project.id, user.id, str(path), path.name, "联合查看/cases.sfmedia"
            )
        row.preview_json = json.dumps(
            {
                **json.loads(old.preview_json),
                **json.loads(row.preview_json),
                "media_case": descriptor,
                "media_group_id": group,
                "display_name": descriptor["title"],
            },
            ensure_ascii=False,
        )
        row.upload_status = UploadStatus.READY
        for case in prepared["package"]["cases"]:
            if case["guid"] in case_groups and str(case_groups[case["guid"]][0].id) == str(old.id):
                sample = next(
                    s for s in descriptor["samples"] if s.get("refi_case_guid") == case["guid"]
                )
                case_groups[case["guid"]] = (row, sample)
    if new_cases:
        row = await exchange.case_collection(db, user, project, new_cases, source_rows)
        for case in new_cases:
            sample = next(s for s in row.media_case["samples"] if s["sample_id"] == case["guid"])
            case_groups[case["guid"]] = (row, sample)
    return case_groups


async def apply(db, user, project, archive_path, expected_token):
    await acquire_write_fence(db, user.id)
    p = await prepare(db, user, project, archive_path)
    if p["preview"]["token"] != expected_token:
        raise StatForgeError(409, "QDPX_MERGE_CHANGED", "项目或交换包已变化，请重新打开合并预览。")
    created_paths = []
    try:
        return await _apply_prepared(db, user, project, archive_path, p, created_paths)
    except Exception as exc:
        for path in reversed(created_paths):
            path.unlink(missing_ok=True)
        if isinstance(exc, (ValueError, OSError)):
            raise exchange.invalid(f"无法读取此 QDPX 项目：{exc}") from None
        raise


async def _apply_prepared(db, user, project, archive_path, p, created_paths):
    root, state, package = p["root"], p["state"], p["package"]
    source_rows, source_materials = {}, {}
    with ZipFile(archive_path) as archive:
        for plan in p["source_plans"]:
            source, row = plan["source"], plan["row"]
            if row is None:
                path = local.checked_path(root / plan["relative"])
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as dst:
                    created_paths.append(path)
                    if source.get("member_path"):
                        with archive.open(source["member_path"]) as src:
                            p["budget"].copy(src, dst, path.parent)
                    else:
                        data = source["plain_text_content"].encode("utf-8")
                        p["budget"].consume(len(data))
                        require_disk_space(path.parent, len(data))
                        dst.write(data)
                row = await project_sources.store(
                    db, project.id, user.id, str(path), path.name, plan["relative"], linked=True
                )
                local.reference_object(project.id, row.object_key, path)
                meta = json.loads(row.preview_json)
                meta["refi_source"] = {
                    "guid": plan["export_guid"],
                    "original_guid": source["guid"],
                    "name": source["name"],
                    "kind": source["kind"],
                    "version_label": path.name,
                }
                row.preview_json = json.dumps(meta, ensure_ascii=False)
            source_rows[source["guid"]] = row
            material = p["materials"].get(str(row.id))
            if not material:
                try:
                    material = await exchange.make_material(
                        db,
                        user,
                        project,
                        row,
                        storage_service.get_local_object_path(row.object_key),
                    )
                except StatForgeError as exc:
                    if exc.error_code != "MATERIAL_FORMAT_UNSUPPORTED":
                        raise
                    p["issues"].append(
                        f"「{row.original_filename}」原文件已保存，当前格式不能在内置预览中打开。"
                    )
            if material:
                source_materials[source["guid"]] = material
            state.setdefault("sources", {})[plan["export_guid"]] = {
                "dataset_id": str(row.id),
                "sha256": row.sha256,
                "relative_path": row.source_path,
            }
    case_groups = await merge_cases(db, user, project, p, source_rows)
    segments = list(p["document"].segments)
    for item in p["coding_plans"]:
        source, selected, coding = item["source"]["source"], item["selection"], item["coding"]
        material = source_materials.get(source["guid"])
        if not material:
            continue
        try:
            locator = exchange.locator_for(material, selected)
            request = CitationRequest(
                material_sha256=material.sha256,
                material_version=material.version,
                reviewed=True,
                locator=locator,
            )
            material_service.validate_locator(material, request)
        except (ValueError, StatForgeError) as exc:
            p["issues"].append(
                f"「{source['name']}」一处编码位置无法读取（{str(exc)[:100]}），原记录保留。"
            )
            continue
        citation = await material_service.save_citation(db, material.id, user.id, request)
        segment = text_coding.segment_from_citation(
            citation,
            [p["code_map"][coding["code_guid"]]],
            selected.get("description", ""),
            identity=uuid.UUID(item["id"]),
            origin="manual",
        )
        segment.coder_id = p["coder_map"][coding["user_guid"]]
        segment.coder_name = next(c.name for c in p["coders"] if c.id == segment.coder_id)
        case = next((c for c in package["cases"] if source["guid"] in c["source_guids"]), None)
        if case and case["guid"] in case_groups:
            collection, sample = case_groups[case["guid"]]
            segment.sample_id, segment.sample_title = (
                sample["sample_id"],
                sample.get("case_title", case["name"]),
            )
            segment.sample_collection_id = collection.id
        segments.append(segment)
        p["fingerprints"][item["key"]] = str(segment.id)
        state.setdefault("segments", {})[str(segment.id)] = {
            "source_guid": source["guid"],
            "source_name": source["name"],
            "source_sha": material.sha256,
            "selection": selected,
            "coding": coding,
            "locator": segment.locator.model_dump(mode="json"),
        }
    document = CodingDocument.model_validate(
        {
            **p["document"].model_dump(mode="json"),
            "themes": p["themes"],
            "local_coders": p["coders"],
            "segments": segments,
        }
    )
    record = p["record"]
    if record and document == p["document"]:
        saved = await read_record(db, record.id, user)
    else:
        saved = await save_record(
            db,
            user,
            SaveRecord(
                project_id=project.id,
                expected_revision=record.revision if record else 0,
                payload=document,
            ),
            record.id if record else None,
            commit=False,
            coding_write=True,
        )
    from app.services.refi_project_notes import restore_notes

    counts = await restore_notes(
        db, user, project, package, saved, document, state, p["issues"], merge=True
    )
    authors = {u["guid"]: u for u in state.get("users", [])}
    for author in package["users"]:
        key = p["coder_map"][author["guid"]]
        authors.setdefault(key, {**author, "guid": key})
    state["users"] = list(authors.values())
    state["coding_fingerprints"] = p["fingerprints"]
    archive_sha = file_digest(archive_path)
    archive_copy = root / ".statforge" / "qdpx-merges" / (archive_sha + ".qdpx")
    if not archive_copy.exists():
        archive_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive_path, archive_copy)
    state.setdefault("merges", {})[archive_sha] = {
        "name": package["name"],
        "preview": p["preview"],
        "notes": counts,
    }
    state["summary"] = {
        **state.get("summary", {}),
        "sources": len({v["dataset_id"] for v in state["sources"].values()}),
        "codes": len(document.themes),
        "codings": len(document.segments),
        "cases": len(set(p["existing_cases"]) | {c["guid"] for c in package["cases"]}),
        "items": list(dict.fromkeys(state.get("summary", {}).get("items", []) + p["issues"])),
    }
    state["summary"]["journals"] = len(
        {v["record_id"] for v in state.get("notes", {}).values() if v.get("record_id")}
    )
    all_rows = list(
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
    state["summary"]["links"] = sum(
        len(s.get("members", list(s.get("files", {}).values())))
        for group in latest_groups(all_rows).values()
        for s in group.media_case.get("samples", [])
    )
    # Source identities and deduplication receipts commit with the imported records.
    # The local JSON remains a readable mirror, not the sole recovery authority.
    exchange.retain_state(project, state)
    await db.commit()
    created_paths.clear()  # Committed files now belong to the project.
    local.write_json(exchange.exchange_path(project.id), state)
    await local.save_project(db, project, user)
    return {
        "summary": state["summary"],
        "added_sources": p["preview"]["added_sources"],
        "added_codings": len(document.segments) - len(p["document"].segments),
        "notes": counts,
    }
