"""Immutable text material versions keep old citations attached to old bytes."""

import hashlib
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.material import Material
from app.services import material_service as materials
from app.services import storage_service


def fail(message="文件已有变化，请重新核对。", status=409):
    return StatForgeError(status, "MATERIAL_SOURCE_CHANGED", message)


def lineage(material):
    return json.loads(material.metadata_json).get("file_version", {})


async def family(db, material, owner):
    root = lineage(material).get("root_id", str(material.id))
    try:
        root_material = await materials.load(db, uuid.UUID(root), owner, material.project_id)
    except ValueError:
        raise fail("材料版本无法读取，请保留原项目。") from None
    if lineage(root_material) or root_material.version != 1:
        raise fail("材料版本无法读取，请保留原项目。")
    rows = (
        await db.scalars(
            select(Material)
            .where(Material.project_id == material.project_id, Material.user_id == owner)
            .order_by(Material.version, Material.created_at, Material.id)
        )
    ).all()
    return root, [row for row in rows if lineage(row).get("root_id", str(row.id)) == root]


async def available(material):
    try:
        await materials.verify_version(material)
        return True
    except StatForgeError:
        return False


async def versions(db, identity, owner):
    material = await materials.load(db, identity, owner)
    _, rows = await family(db, material, owner)
    return {
        "versions": [materials.public(row) for row in rows],
        "stored_available": await available(material),
    }


def summary(extraction):
    pages = extraction.get("pages", [])
    return {
        "pages": len(pages),
        "characters": sum(len(page["text"]) for page in pages),
        "extraction_status": extraction.get("status"),
    }


def token(material, incoming):
    return hashlib.sha256(
        materials.canonical(
            {
                "material_id": str(material.id),
                "material_sha256": material.sha256,
                "material_version": material.version,
                "incoming_sha256": incoming,
            }
        ).encode()
    ).hexdigest()


async def compare(db, identity, owner, filename, data, body, snapshot):
    material = await materials.load(db, identity, owner)
    if material.sha256 != body.source_sha256:
        raise fail()
    if material.mime_type not in {"text/plain", "application/pdf"}:
        raise fail("请在文本材料中核对文件。", 422)
    materials.reject_secret(
        {"filename": filename, "text": data.decode("utf-8", errors="replace")}, snapshot.secret
    )
    with tempfile.TemporaryDirectory(prefix="check-", dir=materials.temp_parent()) as directory:
        path = Path(directory) / "source"
        await anyio.to_thread.run_sync(path.write_bytes, data)
        mime, metadata, extraction = await anyio.to_thread.run_sync(
            materials.inspect, path, filename
        )
    if mime != material.mime_type:
        raise fail("格式不同，请作为独立材料导入。", 422)
    materials.reject_secret({"metadata": metadata, "extraction": extraction}, snapshot.secret)
    sha = hashlib.sha256(data).hexdigest()
    return {
        "source": materials.public(material),
        "same_content": sha == material.sha256,
        "comparison_sha256": token(material, sha),
        "stored_available": await available(material),
        "before": summary(json.loads(material.extraction_json)),
        "after": {
            "filename": filename.replace("\\", "/").split("/")[-1][:255],
            "sha256": sha,
            **summary(extraction),
        },
    }


async def apply(db, identity, owner, filename, data, body, snapshot):
    comparison = await compare(db, identity, owner, filename, data, body, snapshot)
    if body.comparison_sha256 != comparison["comparison_sha256"]:
        raise fail("所选文件已变化，请重新核对。")
    fresh = await materials.fence_snapshot(db, owner, snapshot)
    material = await materials.load(db, identity, owner)
    if (
        material.sha256 != body.source_sha256
        or token(material, comparison["after"]["sha256"]) != body.comparison_sha256
    ):
        raise fail()
    if body.mode == "restore":
        if not comparison["same_content"]:
            raise fail("内容不是原版本，请选择原文件，或采用新版。")
        if not await available(material):
            key = f"m/{uuid.uuid4().hex}/source"
            from app.services.local_workspaces import register_object

            register_object(material.project_id, key)
            with tempfile.TemporaryDirectory(
                prefix="restore-", dir=materials.temp_parent()
            ) as directory:
                path = Path(directory) / "source"
                await anyio.to_thread.run_sync(path.write_bytes, data)
                await anyio.to_thread.run_sync(
                    storage_service.upload_file, key, str(path), material.mime_type
                )
            material.object_key = key
        meta = json.loads(material.metadata_json)
        meta["source_check"] = {
            "sha256": material.sha256,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        material.metadata_json = materials.canonical(meta)
        await db.flush()
        return {"material": materials.public(material), "outcome": "verified"}
    if comparison["same_content"]:
        raise fail("内容一致，无需创建新版本。")
    root, rows = await family(db, material, owner)
    candidates = (
        rows
        if body.mode == "version"
        else (
            await db.scalars(
                select(Material).where(
                    Material.project_id == material.project_id, Material.user_id == owner
                )
            )
        ).all()
    )
    for row in candidates:
        if row.sha256 == comparison["after"]["sha256"] and (
            lineage(row).get("parent_id") == str(material.id)
            if body.mode == "version"
            else not lineage(row)
        ):
            return {"material": materials.public(row), "outcome": "existing"}
    created = await materials.upload(db, material.project_id, owner, filename, data, fresh)
    new = await materials.load(db, uuid.UUID(created["id"]), owner, material.project_id)
    if body.mode == "version":
        new.version = max(row.version for row in rows) + 1
        meta = json.loads(new.metadata_json)
        meta["file_version"] = {
            "root_id": root,
            "parent_id": str(material.id),
            "parent_sha256": material.sha256,
        }
        new.metadata_json = materials.canonical(meta)
        await db.flush()
    return {"material": materials.public(new), "outcome": "created"}


async def restore_lineage(db, records, mapping, owner, project):
    """Restore exact version numbers before restoring version-bound citations."""
    for record in records:
        value = record.get("version", 1)
        if type(value) is not int or value < 1:
            raise fail("材料版本无效，请保留原项目。", 422)
        new = await materials.load(db, uuid.UUID(mapping[record["id"]]), owner, project)
        old_meta = json.loads(record.get("metadata_json") or "{}")
        previous = old_meta.get("file_version")
        meta = json.loads(new.metadata_json)
        checked = old_meta.get("source_check")
        if checked:
            try:
                if (
                    not isinstance(checked, dict)
                    or set(checked) != {"sha256", "verified_at"}
                    or checked["sha256"] != new.sha256
                    or not isinstance(checked["verified_at"], str)
                    or len(checked["verified_at"]) > 64
                    or datetime.fromisoformat(checked["verified_at"]).tzinfo is None
                ):
                    raise ValueError()
            except (ValueError, TypeError):
                raise fail("材料核对记录无法恢复，请保留原项目。", 422) from None
            meta["source_check"] = checked
        if previous:
            if (
                not isinstance(previous, dict)
                or previous.get("root_id") not in mapping
                or previous.get("parent_id") not in mapping
            ):
                raise fail("材料版本关系无法恢复，请保留原项目。", 422)
            meta["file_version"] = {
                "root_id": mapping[previous["root_id"]],
                "parent_id": mapping[previous["parent_id"]],
                "parent_sha256": previous.get("parent_sha256"),
            }
        elif value != 1:
            raise fail("材料缺少版本来源，请保留原项目。", 422)
        new.version = value
        new.metadata_json = materials.canonical(meta)
    await db.flush()
    for record in records:
        new = await materials.load(db, uuid.UUID(mapping[record["id"]]), owner, project)
        previous = lineage(new)
        if not previous:
            continue
        parent = await materials.load(db, uuid.UUID(previous["parent_id"]), owner, project)
        root = await materials.load(db, uuid.UUID(previous["root_id"]), owner, project)
        if (
            root.version != 1
            or lineage(root)
            or parent.version >= new.version
            or parent.sha256 != previous["parent_sha256"]
            or lineage(parent).get("root_id", str(parent.id)) != str(root.id)
        ):
            raise fail("材料版本关系不一致，请保留原项目。", 422)
