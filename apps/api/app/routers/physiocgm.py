"""Task-local manual and conversational access share the same signal service."""

import json
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.services import dataset_service
from app.services import physiocgm as service

router = APIRouter(prefix="/api/v1/datasets", tags=["signals"])


async def source(db, user, identity):
    row = await dataset_service.get_dataset_with_access(db, identity, user.id)
    if not row.signal_session:
        raise service.invalid("请选择已识别的原始信号会话或分段 PKL。")
    return row


@router.post("/{dataset_id}/signals/preview")
async def preview(
    dataset_id: uuid.UUID,
    options: service.Options,
    persist: bool = True,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    row = await source(db, user, dataset_id)
    try:
        if persist:
            result, _ = await service.apply_view(db, user, row, options)
            return result
        return await service.read_view(db, user, row, options)
    except (ValueError, KeyError, OSError) as error:
        raise service.invalid(
            f"读取会话失败：{error}。请核对文件列名、时间格式和文件是否完整。"
        ) from error


@router.post("/{dataset_id}/signals/package")
async def package(
    dataset_id: uuid.UUID,
    options: service.Options,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    row = await source(db, user, dataset_id)
    try:
        result = await service.package(db, user, row, options)
        await db.commit()
        return result
    except (ValueError, KeyError, OSError) as error:
        raise service.invalid(
            f"分段未完成：{error}。原文件仍保留，请核对文件和时间设置后重试。"
        ) from error


@router.get("/{dataset_id}/signals/settings")
async def settings(
    dataset_id: uuid.UUID,
    task_id: str = "project",
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    row = await source(db, user, dataset_id)
    return json.loads(row.preview_json or "{}").get("signal_options", {}).get(task_id, {})
