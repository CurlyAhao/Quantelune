"""Explicitly selected desktop folders, with sources read in place."""

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from sqlalchemy import select

from app.config import settings
from app.exceptions import StatForgeError


def registry_path():
    return Path(settings.app_data_path or settings.local_storage_path) / "local-workspaces.json"


def registry():
    path = registry_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path, value):
    checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def checked_path(path):
    for part in [path, *path.parents]:
        if part.exists() and (
            part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise StatForgeError(
                422, "WORKSPACE_PATH_INVALID", "工作区的受管路径不能通过链接指向其他目录。"
            )
    return path


def project_root(project_id):
    if not settings.desktop_mode or settings.storage_backend != "local":
        return None
    entry = registry().get(str(project_id))
    return Path(entry["root"]) if entry else None


def object_location(key):
    parts = PurePosixPath(key).parts
    if len(parts) >= 5 and parts[0] == "users" and parts[2] == "projects":
        root = project_root(parts[3])
    elif len(parts) >= 3 and parts[0] in {
        "ml-artifacts",
        "research-artifacts",
        "workflow-derived-data",
        "workflow-artifacts",
    }:
        root = project_root(parts[1])
    elif parts and parts[0] == "m" and settings.desktop_mode:
        entry = next(
            (value for value in registry().values() if key in value.get("objects", [])), None
        )
        root = Path(entry["root"]) if entry else None
    else:
        root = None
    if root is None:
        return None
    checked_path(root / ".statforge" / "storage")
    from app.services.storage_service import _safe_resolve

    manifest = root / ".statforge" / "sources.json"
    sources = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    if key in sources:
        return _safe_resolve(root, sources[key]), True
    return _safe_resolve(root / ".statforge" / "storage", key), False


def register_object(project_id, key):
    entries = registry()
    entry = entries.get(str(project_id))
    if entry and key not in entry.get("objects", []):
        entry.setdefault("objects", []).append(key)
        write_json(registry_path(), entries)


def reference_object(project_id, key, source):
    """Give a preview an owned reference to an existing file, without copying it."""
    root = project_root(project_id)
    if root is None:
        return False
    from app.services.storage_service import filesystem_path

    source = filesystem_path(Path(source).resolve(strict=True))
    root = filesystem_path(root.resolve())
    if not source.is_relative_to(root) or not source.is_file():
        return False
    checked_path(source)
    register_object(project_id, key)
    manifest = root / ".statforge" / "sources.json"
    sources = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    sources[key] = source.relative_to(root).as_posix()
    write_json(manifest, sources)
    return True


def workspace_entries(root):
    """Yield each real directory and its visible children, skipping managed storage and links."""

    def regular(path):
        return not path.is_symlink() and not getattr(path.lstat(), "st_file_attributes", 0) & 0x400

    for directory, folders, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        folders[:] = sorted(
            name for name in folders if name != ".statforge" and regular(parent / name)
        )
        yield parent, folders, [parent / name for name in sorted(names) if regular(parent / name)]


def ensure_relocated(entries, project_id, root):
    previous = entries.get(str(project_id))
    if not previous:
        return
    old = Path(previous["root"])
    if old == root:
        return
    try:
        old.stat()
    except FileNotFoundError:
        # An offline drive must not be mistaken for an intentional move.
        if Path(old.anchor).is_dir():
            return
    except OSError:
        pass
    else:
        try:
            if old.samefile(root):
                return
        except OSError:
            pass
    raise StatForgeError(
        409,
        "WORKSPACE_COPY_CONFLICT",
        f"该文件夹是已有项目的副本。请打开原工作区 {old} 继续使用。",
    )


async def file_view(db, project_id, owner):
    """A directory view is not a projection of every saved dataset record."""
    from app.models.dataset import Dataset, UploadStatus
    from app.services import dataset_service, storage_service

    await dataset_service._verify_project_access(db, project_id, owner)
    root = project_root(project_id)
    rows = list(
        (
            await db.scalars(
                select(Dataset).where(
                    Dataset.project_id == project_id, Dataset.upload_status != UploadStatus.DELETED
                )
            )
        ).all()
    )
    if root is None:
        return {"root": None, "files": [], "folders": [], "sessions": []}
    manifest = root / ".statforge" / "sources.json"
    sources = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    linked = {
        sources[row.object_key]: row
        for row in rows
        if row.local_file and row.raw_file and row.object_key in sources
    }
    files, directories, sessions = [], [], []
    for directory, folders, paths in workspace_entries(root):
        directories.extend((directory / name).relative_to(root).as_posix() for name in folders)
        for path in paths:
            relative = path.relative_to(root).as_posix()
            row = linked.get(relative)
            files.append(
                {
                    "path": relative,
                    "name": path.name,
                    "size": path.stat().st_size,
                    "dataset_id": str(row.id) if row else None,
                }
            )
    for row in rows:
        if row.signal_session and row.file_format in {"bidmc", "physiocgm", "hapt"}:
            try:
                descriptor = json.loads(
                    storage_service.get_local_object_path(row.object_key).read_text(
                        encoding="utf-8"
                    )
                )

                def leaves(value):
                    if isinstance(value, str):
                        yield value
                    elif isinstance(value, dict):
                        for child in value.values():
                            yield from leaves(child)
                    elif isinstance(value, list):
                        for child in value:
                            yield from leaves(child)

                sessions.append(
                    {
                        "id": str(row.id),
                        "title": descriptor.get("session", row.original_filename),
                        "files": list(leaves(descriptor.get("files", {}))),
                    }
                )
            except (OSError, ValueError):
                continue
    for row in rows:
        if row.media_case:
            from app.services.multimodal_cases import read_metadata, sample_rows

            try:
                samples = sample_rows(root, row.media_case)
                related = [
                    sample[key]
                    for sample in samples
                    for key in ("video_av", "audio", "text", "image_path")
                    if key in sample
                ]
            except (OSError, ValueError):
                related = []
            sessions.append(
                {
                    "id": str(row.id),
                    "title": row.display_name,
                    "files": related,
                    "kind": "media",
                    "group_id": read_metadata(row).get("media_group_id", str(row.id)),
                    "created": row.created_at.isoformat(),
                }
            )
    from app.models.workspace import WorkspaceRecord

    records = await db.scalars(
        select(WorkspaceRecord.payload_json).where(
            WorkspaceRecord.project_id == project_id,
            WorkspaceRecord.user_id == owner,
            WorkspaceRecord.kind == "visual_run",
        )
    )
    visual_outputs = []
    for raw in records:
        run = json.loads(raw)
        visual_outputs.extend(
            run[key] for key in ("summary_id", "index_id", "package_id") if run.get(key)
        )
    return {
        "root": str(root),
        "files": files,
        "folders": directories,
        "sessions": sessions,
        "generated_ids": visual_outputs,
    }


async def open_folder(db, owner, selected):
    if not settings.desktop_mode or settings.storage_backend != "local":
        raise StatForgeError(403, "DESKTOP_REQUIRED", "本地文件夹工作区只在桌面版使用。")
    if not Path(selected).is_absolute():
        raise StatForgeError(422, "WORKSPACE_FOLDER_REQUIRED", "请选择工作区的完整目录。")
    root = Path(selected).resolve(strict=True)
    if not root.is_dir():
        raise StatForgeError(422, "WORKSPACE_FOLDER_REQUIRED", "请选择工作区文件夹。")
    internal = root / ".statforge"
    if internal.is_symlink() or (
        internal.exists() and getattr(internal.lstat(), "st_file_attributes", 0) & 0x400
    ):
        raise StatForgeError(422, "WORKSPACE_FOLDER_INVALID", ".statforge 不能指向其他目录。")
    from app.models.dataset import Dataset, UploadStatus
    from app.models.project import Project
    from app.services import project_sources

    entries = registry()
    existing_id = next(
        (
            key
            for key, value in entries.items()
            if os.path.normcase(value["root"]) == os.path.normcase(str(root))
            and value["owner"] == str(owner)
        ),
        None,
    )
    if not existing_id and (internal / "workspace.json").exists():
        saved = json.loads((internal / "workspace.json").read_text(encoding="utf-8"))
        candidate = await db.get(Project, uuid.UUID(saved["project_id"]))
        if candidate and candidate.owner_id == owner and not candidate.is_deleted:
            ensure_relocated(entries, candidate.id, root)
            existing_id = str(candidate.id)
        else:
            raise StatForgeError(
                409,
                "WORKSPACE_INDEX_UNAVAILABLE",
                (
                    "此文件夹已有 StatForge 项目，但当前安装未找到其项目索引。已"
                    "保留原配置，请先恢复原工作区索引。"
                ),
            )
    elif not existing_id and (internal / "project.json").exists():
        raise StatForgeError(
            409,
            "WORKSPACE_INDEX_UNAVAILABLE",
            "此文件夹已有项目配置，已保留，未覆盖。请恢复原工作区索引后打开。",
        )
    project = await db.get(Project, uuid.UUID(existing_id)) if existing_id else None
    if project is None or project.is_deleted:
        project = Project(id=uuid.uuid4(), owner_id=owner, title=root.name or str(root))
        db.add(project)
        await db.flush()
    # Recheck after DB awaits so a concurrent open cannot replace a newly bound root.
    entries = registry()
    ensure_relocated(entries, project.id, root)
    entries[str(project.id)] = {
        **entries.get(str(project.id), {}),
        "root": str(root),
        "owner": str(owner),
    }
    write_json(registry_path(), entries)
    manifest = internal / "sources.json"
    sources = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    rows = list(
        (
            await db.execute(
                select(Dataset).where(
                    Dataset.project_id == project.id, Dataset.upload_status != UploadStatus.DELETED
                )
            )
        ).scalars()
    )
    # A name inside the project is not a storage identity. Uploaded copies live
    # in managed storage even when their display path matches a workspace file.
    # Only an explicit in-place reference with its existing key/path binding can
    # be refreshed or marked missing by this directory scan.
    known = {
        row.source_path: row
        for row in rows
        if row.raw_file and row.local_file and sources.get(row.object_key) == row.source_path
    }
    # Older scans could have inserted aliases for managed uploads. Remove only
    # those proven-invalid aliases; do not move files or rewrite their hashes.
    for row in rows:
        if row.raw_file and not row.local_file:
            sources.pop(row.object_key, None)
            if row.error_code == "WORKSPACE_SOURCE_MISSING":
                from app.services.storage_service import _get_local_root, _safe_resolve

                copies = (
                    _safe_resolve(internal / "storage", row.object_key),
                    _safe_resolve(_get_local_root(), row.object_key),
                )
                if any(copy.is_file() for copy in copies):
                    row.upload_status = UploadStatus.UPLOADED
                    row.error_code = row.error_message = None
    seen = set()
    for _, _, paths in workspace_entries(root):
        for path in paths:
            relative = path.relative_to(root).as_posix()
            seen.add(relative)
            row = known.get(relative)
            if row is None:
                row = await project_sources.store(
                    db, project.id, owner, str(path), path.name, relative, linked=True
                )
            else:
                row.sha256 = await asyncio.to_thread(project_sources.file_digest, path)
                row.content_hash, row.file_size = row.sha256, path.stat().st_size
                row.upload_status, row.error_code, row.error_message = (
                    (UploadStatus.READY if row.signal_session else UploadStatus.UPLOADED),
                    None,
                    None,
                )
            sources[row.object_key] = relative
    for relative, row in known.items():
        if relative not in seen:
            row.upload_status = UploadStatus.FAILED
            row.error_code, row.error_message = (
                "WORKSPACE_SOURCE_MISSING",
                "原文件已从工作区移走，请恢复文件或刷新工作区。",
            )
    write_json(manifest, sources)
    from app.services.physiocgm import discover

    await discover(db, project, owner, root, rows)
    from app.services.bidmc import discover as discover_bidmc

    await discover_bidmc(db, project, owner, root, rows)
    from app.services import hapt, multimodal_cases

    await hapt.discover(db, project, owner, root, rows)
    await multimodal_cases.discover(db, project, owner, root, rows)
    await db.flush()
    write_json(
        internal / "workspace.json",
        {
            "schema": "statforge-local-workspace-v1",
            "project_id": str(project.id),
            "title": project.title,
            "root": str(root),
            "source_files": len(sources),
        },
    )
    return project


async def save_project(db, project, user):
    root = project_root(project.id)
    if root is None:
        raise StatForgeError(409, "LOCAL_WORKSPACE_REQUIRED", "该项目尚未关联本地工作区文件夹。")
    from app.services.project_files import capture_records

    records = await capture_records(db, project, user)
    from app.services import storage_service

    keys = {
        value
        for rows in records.values()
        for row in rows
        for name, value in row.items()
        if name in {"object_key", "artifact_key"} and isinstance(value, str) and value
    }
    for key in keys:
        if key.startswith("m/"):
            register_object(project.id, key)
        location = object_location(key)
        if location and not location[1]:
            source = storage_service.get_local_object_path(key)
            destination = location[0]
            if source != destination and source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    destination = root / ".statforge" / "project.json"
    write_json(destination, {"schema": "statforge-local-project-v1", "records": records})
    return {"path": str(destination)}
