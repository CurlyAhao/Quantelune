"""Pending preparation belongs to its dataset; saving never applies a rule."""

import json

from pydantic import BaseModel, ConfigDict, Field

from app.exceptions import StatForgeError
from app.preview.workspace import credential_free
from app.preview.write_fence import acquire_write_fence
from app.schemas.preparation import Preparation
from app.services import dataset_service


class DraftWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    request: Preparation | None = None


async def read(db, identity, user):
    dataset = await dataset_service.get_dataset_with_access(db, identity, user.id)
    return json.loads(dataset.preview_json or "{}").get(
        "preparation_draft", {"revision": 0, "request": None}
    )


async def save(db, identity, user, body: DraftWrite):
    await acquire_write_fence(db, user.id)
    await db.refresh(user)
    if not user.is_active:
        raise StatForgeError(403, "ACCESS_DENIED", "当前身份不可用。")
    dataset = await dataset_service.get_dataset_with_access(db, identity, user.id)
    await db.refresh(dataset)
    await dataset_service._verify_project_access(db, dataset.project_id, user.id)
    data = json.loads(dataset.preview_json or "{}")
    old = data.get("preparation_draft", {"revision": 0})
    if old["revision"] != body.revision:
        raise StatForgeError(
            409, "PREPARATION_DRAFT_CONFLICT", "整理草稿已有变化，请重新打开后继续。"
        )
    request = body.request
    if request is not None:
        if request.source_sha256 != dataset.sha256:
            raise StatForgeError(409, "PREPARATION_SOURCE_CHANGED", "数据版本已变化，请重新选择。")
        if request.right_dataset_id:
            right = await dataset_service.get_dataset_with_access(
                db, request.right_dataset_id, user.id
            )
            if right.project_id != dataset.project_id or right.sha256 != request.right_sha256:
                raise StatForgeError(
                    409, "PREPARATION_SOURCE_CHANGED", "关联表已有变化，请重新选择。"
                )
        await credential_free(db, user, request.model_dump(mode="json"))
    saved = {
        "revision": body.revision + 1,
        "request": request.model_dump(mode="json", exclude={"result_sha256"}) if request else None,
    }
    data["preparation_draft"] = saved
    dataset.preview_json = json.dumps(data, ensure_ascii=False)
    await db.flush()
    await db.commit()
    return saved
