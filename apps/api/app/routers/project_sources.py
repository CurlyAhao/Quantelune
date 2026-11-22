"""Store whole folders first; prepare selected files from the project data page."""

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path, PurePosixPath

import anyio
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.dataset import Dataset, UploadStatus, Variable, VariableRole
from app.models.user import User
from app.routers.datasets import _stream_to_temp
from app.schemas.dataset import DatasetResponse
from app.services import dataset_service, json_records, project_sources, storage_service
from app.services import multimodal_import as mm

router = APIRouter(tags=["project source files"])
MATERIAL_LOCK = asyncio.Lock()


@router.post("/api/v1/datasets/{dataset_id}/source-material")
async def source_material(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    """Reuse a registered source's bytes in the existing material preview service."""
    from app.preview.write_fence import acquire_write_fence
    from app.services import material_service

    async with MATERIAL_LOCK:
        await acquire_write_fence(db, user.id)
        source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
        await db.refresh(source)
        if not source.raw_file or source.upload_status != UploadStatus.UPLOADED:
            raise mm.invalid("原文件当前不可用，请刷新工作区。")
        metadata = json.loads(source.preview_json or "{}")
        linked = metadata.get("material_id")
        previous_material_ids = []
        material_version = 1
        if linked:
            material = await material_service.load(
                db, uuid.UUID(linked), user.id, source.project_id
            )
            if material.sha256 == source.sha256:
                await material_service.verify_version(material)
                return material_service.public(material)
            previous_material_ids = [
                *json.loads(material.metadata_json or "{}").get("annotation_material_ids", []),
                str(material.id),
            ]
            material_version = material.version + 1
        with tempfile.TemporaryDirectory(prefix="sf-source-material-") as directory:
            from app.config import settings

            if settings.storage_backend == "local":
                path = storage_service.get_local_object_path(source.object_key)
            else:
                path = Path(directory) / "source"
                await anyio.to_thread.run_sync(
                    storage_service.download_to_path, source.object_key, str(path)
                )

            def file_hash():
                with path.open("rb") as handle:
                    return hashlib.file_digest(handle, "sha256").hexdigest()

            if await anyio.to_thread.run_sync(file_hash) != source.sha256:
                raise mm.invalid("原文件已变化，请刷新工作区。")
            from app.models.material import Material
            from app.services.local_workspaces import reference_object

            identity = uuid.uuid4()
            key = f"m/{identity.hex}/source"
            mime, details, extraction = await anyio.to_thread.run_sync(
                material_service.inspect, path, source.original_filename
            )
            if reference_object(source.project_id, key, path):
                details.update(
                    source_dataset_id=str(source.id),
                    source_path=source.source_path,
                    local_file=source.local_file,
                    annotation_material_ids=list(dict.fromkeys(previous_material_ids)),
                )
                item = Material(
                    id=identity,
                    user_id=user.id,
                    project_id=source.project_id,
                    filename=source.original_filename,
                    mime_type=mime,
                    file_size=source.file_size,
                    sha256=source.sha256,
                    version=material_version,
                    object_key=key,
                    metadata_json=material_service.canonical(details),
                    extraction_json=material_service.canonical(extraction),
                )
                db.add(item)
                await db.flush()
                material = material_service.public(item)
            else:
                data = await anyio.to_thread.run_sync(path.read_bytes)
                material = await material_service.upload(
                    db,
                    source.project_id,
                    user.id,
                    source.original_filename,
                    data,
                    source_path=source.source_path,
                )
        # Changed source bytes produce a new material; old citations stay valid.
        metadata["material_id"] = str(material["id"])
        source.preview_json = json.dumps(metadata, ensure_ascii=False)
        await db.commit()
        return material


class OpenWorkspace(BaseModel):
    path: str


@router.get("/api/v1/projects/{project_id}/workspace-files")
async def workspace_files(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services.local_workspaces import file_view

    return await file_view(db, project_id, user.id)


@router.post("/api/v1/local-workspaces/open")
async def open_workspace(
    body: OpenWorkspace,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.schemas.project import ProjectResponse
    from app.services.local_workspaces import open_folder, save_project

    owner = user.id
    project = await open_folder(db, owner, body.path)
    await save_project(db, project, user)
    return ProjectResponse.model_validate(project)


@router.post("/api/v1/projects/{project_id}/save-local-workspace")
async def save_workspace(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services.local_workspaces import save_project

    project = await dataset_service._verify_project_access(db, project_id, user.id)
    return await save_project(db, project, user)


@router.post(
    "/api/v1/projects/{project_id}/source-files", response_model=DatasetResponse, status_code=201
)
async def receive_source(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    source_path: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    owner = user.id
    await dataset_service._verify_project_access(db, project_id, owner)
    project_sources.source_path(source_path)
    path = None
    try:
        path, _ = await _stream_to_temp(file)
        return await project_sources.store(
            db, project_id, owner, path, file.filename or "file", source_path
        )
    finally:
        if path:
            os.unlink(path)


@router.get("/api/v1/datasets/{dataset_id}/source-structure")
async def inspect_source(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    if not source.raw_file or source.file_format not in {"pkl", "pickle"}:
        raise mm.invalid("请选择项目中的原始 PKL 文件。")
    from app.config import settings

    if settings.storage_backend == "local":
        return await anyio.to_thread.run_sync(
            mm.inspect, str(storage_service.get_local_object_path(source.object_key))
        )
    with tempfile.TemporaryDirectory(prefix="sf-source-inspect-") as directory:
        path = str(Path(directory) / "source.pkl")
        await anyio.to_thread.run_sync(storage_service.download_to_path, source.object_key, path)
        return await anyio.to_thread.run_sync(mm.inspect, path)


class PrepareSource(BaseModel):
    mapping: mm.Mapping | None = None
    sheet_name: str | None = None
    csv_encoding: str = "auto"


def _file_sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def _matching_table(db, source, body, directory, source_path):
    from app.config import settings

    query = (
        select(Dataset)
        .where(
            Dataset.project_id == source.project_id,
            Dataset.upload_status == UploadStatus.READY,
            Dataset.file_format
            == ("parquet" if source.file_format == "json" else source.file_format),
            Dataset.preview_json.contains(str(source.id)),
        )
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
    )
    if source.file_format != "json":
        query = query.where(Dataset.sha256 == source.sha256)
    sheet = body.sheet_name
    restored_digest = None
    for table in (await db.scalars(query)).all():
        if (
            table.source_dataset_id != str(source.id)
            or table.prepared_source_sha256 != source.sha256
        ):
            continue
        if source.file_format == "xlsx":
            if sheet is None:
                from app.services.file_validation_service import excel_sheet_names

                sheet = (await anyio.to_thread.run_sync(excel_sheet_names, source_path))[0]
            if sheet != table.sheet_name:
                continue
        if source.file_format == "csv" and table.csv_encoding != (
            "utf-8" if body.csv_encoding == "auto" else body.csv_encoding
        ):
            continue
        if settings.storage_backend == "local":
            path = storage_service.get_local_object_path(table.object_key)
        else:
            if not await anyio.to_thread.run_sync(storage_service.object_exists, table.object_key):
                continue
            path = Path(directory) / "existing-table"
            await anyio.to_thread.run_sync(
                storage_service.download_to_path, table.object_key, str(path)
            )
        try:
            if await anyio.to_thread.run_sync(_file_sha, path) != table.sha256:
                continue
        except FileNotFoundError:
            continue
        meta = json.loads(table.preview_json)
        if meta.get("verify_prepared_json"):
            if restored_digest is None:

                def digest_conversion():
                    converted = Path(directory) / "verify.parquet"
                    json_records.read_table(source_path, source.sha256).write_parquet(converted)
                    return _file_sha(converted)

                restored_digest = await anyio.to_thread.run_sync(digest_conversion)
            if restored_digest != table.sha256:
                continue
            meta.pop("verify_prepared_json")
            table.preview_json = json.dumps(meta, ensure_ascii=False)
        return table
    return None


@router.get("/api/v1/datasets/{dataset_id}/source-json")
async def preview_json_source(
    dataset_id: uuid.UUID,
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.config import settings
    from app.exceptions import FileValidationError

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    if not source.raw_file or source.file_format != "json":
        raise FileValidationError("JSON_SOURCE_EXPECTED", "请选择一个 JSON 原文件。")
    key, sha = source.object_key, source.sha256

    def page():
        if settings.storage_backend == "local":
            frame = json_records.read_table(storage_service.get_local_object_path(key), sha)
        else:
            with tempfile.TemporaryDirectory(prefix="sf-json-preview-") as directory:
                path = Path(directory) / "source.json"
                storage_service.download_to_path(key, str(path))
                frame = json_records.read_table(path, sha)
        return {
            "columns": frame.columns,
            "total": frame.height,
            "rows": [
                [json_records.display_value(v) for v in row]
                for row in frame.slice(offset, 20).iter_rows()
            ],
        }

    return await anyio.to_thread.run_sync(page)


@router.get("/api/v1/datasets/{dataset_id}/source-sheets")
async def source_sheets(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    if not source.raw_file or source.file_format != "xlsx":
        raise mm.invalid("请选择项目中的 Excel 原文件。")
    from app.config import settings
    from app.services.file_validation_service import excel_sheet_names

    if settings.storage_backend == "local":
        return {
            "sheet_names": await anyio.to_thread.run_sync(
                excel_sheet_names, str(storage_service.get_local_object_path(source.object_key))
            )
        }
    with tempfile.TemporaryDirectory(prefix="sf-source-sheets-") as directory:
        path = str(Path(directory) / "source.xlsx")
        await anyio.to_thread.run_sync(storage_service.download_to_path, source.object_key, path)
        return {"sheet_names": await anyio.to_thread.run_sync(excel_sheet_names, path)}


@router.post(
    "/api/v1/datasets/{dataset_id}/prepare-source", response_model=DatasetResponse, status_code=201
)
async def prepare_source(
    dataset_id: uuid.UUID,
    body: PrepareSource,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    response: Response = None,
):
    from app.preview.write_fence import acquire_write_fence

    owner = user.id
    source = await dataset_service.get_dataset_with_access(db, dataset_id, owner)
    if source.file_format in {"csv", "xlsx", "parquet", "json"}:
        await acquire_write_fence(db, owner)
        await db.refresh(source)
    if not source.raw_file or source.upload_status != UploadStatus.UPLOADED:
        raise mm.invalid("请选择尚未覆盖的项目原文件。")
    project, source_id, original_key = source.project_id, str(source.id), source.object_key
    original_sha, original_bytes, original_name = (
        source.sha256,
        source.file_size,
        source.original_filename,
    )
    parent = PurePosixPath(source.source_path or original_name).parent
    created_key = None
    try:
        with tempfile.TemporaryDirectory(prefix="sf-source-prepare-") as directory:
            from app.config import settings

            if settings.storage_backend == "local":
                path = str(storage_service.get_local_object_path(original_key))
            else:
                path = str(Path(directory) / ("source." + source.file_format))
                await anyio.to_thread.run_sync(storage_service.download_to_path, original_key, path)
            if source.file_format in {"pkl", "pickle"}:
                if body.mapping is None:
                    raise mm.invalid("请在项目数据中选择标签和模态。")
                normalized = str(Path(directory) / "features.parquet")
                metadata = await anyio.to_thread.run_sync(
                    mm.convert, path, body.mapping, normalized
                )
                node = "_".join(body.mapping.node) or "features"
                node = "".join(c if c.isalnum() or c in "_-" else "_" for c in node)[:80]
                name = f"{Path(original_name).stem[:120]}.{node}.{body.mapping.task}.parquet"
                dataset = await dataset_service.upload_dataset(
                    db,
                    project,
                    owner,
                    normalized,
                    name,
                    source_path=str(parent / name),
                    allow_duplicate=True,
                )
                created_key = dataset.object_key
                metadata.update(
                    source_filename=original_name,
                    source_object_key=original_key,
                    source_dataset_id=source_id,
                    source_bytes=original_bytes,
                )
                preview = json.loads(dataset.preview_json or "{}")
                preview.update(multimodal=metadata, source_dataset_id=source_id)
                dataset.preview_json = json.dumps(preview, ensure_ascii=False)
                for column in json.loads(dataset.schema_json or "[]"):
                    role = (
                        VariableRole.TARGET
                        if column["name"] == "label"
                        else VariableRole.IDENTIFIER
                        if column["name"] == "sample_id"
                        else VariableRole.FEATURE
                    )
                    db.add(
                        Variable(
                            dataset_id=dataset.id,
                            name=column["name"],
                            data_type=column["inferred_type"],
                            role=role,
                        )
                    )
            elif source.file_format in {"csv", "xlsx", "parquet", "json"}:
                if await anyio.to_thread.run_sync(_file_sha, path) != original_sha:
                    raise mm.invalid("原文件已变化，请重新导入。")
                existing = await _matching_table(db, source, body, directory, path)
                if existing is not None:
                    if response is not None:
                        response.status_code = 200
                    return existing
                output_format = source.file_format
                if source.file_format == "json":
                    normalized = str(Path(directory) / "records.parquet")
                    frame = await anyio.to_thread.run_sync(
                        json_records.read_table, path, original_sha
                    )
                    await anyio.to_thread.run_sync(frame.write_parquet, normalized)
                    path, output_format = normalized, "parquet"
                name = f"{Path(original_name).stem[:160]}.analysis.{output_format}"
                dataset = await dataset_service.upload_dataset(
                    db,
                    project,
                    owner,
                    path,
                    name,
                    sheet_name=body.sheet_name,
                    csv_encoding=body.csv_encoding,
                    source_path=str(parent / name),
                    allow_duplicate=True,
                )
                created_key = dataset.object_key
                preview = json.loads(dataset.preview_json or "{}")
                if source.file_format == "json":
                    preview["rows"] = [
                        {k: json_records.display_value(v) for k, v in row.items()}
                        for row in preview["rows"]
                    ]
                preview["prepared_source"] = {
                    "sha256": original_sha,
                    "sheet_name": body.sheet_name,
                    "csv_encoding": body.csv_encoding,
                }
                preview["source_dataset_id"] = source_id
                dataset.preview_json = json.dumps(preview, ensure_ascii=False)
            else:
                raise mm.invalid(
                    "这个原文件已保存。当前可将 CSV、Excel、JSON 记录表、Parquet 和预处"
                    "理特征 PKL 配置为分析数据。"
                )
            await db.flush()
            await db.refresh(dataset)
            from app.services.local_workspaces import project_root, save_project

            if project_root(project):
                local_project = await dataset_service._verify_project_access(db, project, owner)
                await save_project(db, local_project, user)
            return dataset
    except Exception:
        if created_key:
            storage_service.delete_object(created_key)
        raise
