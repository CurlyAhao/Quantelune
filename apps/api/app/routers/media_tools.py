"""Local desktop tool choice; never exposed as an Agent execution argument."""

import asyncio
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.dependencies import get_current_user
from app.models.user import User
from app.services.multimodal_cases import invalid
from app.services.praat_pitch import config_path, configured_path, praat_identity

router = APIRouter(prefix="/api/v1/media-tools", tags=["local media tools"])


@router.get("/praat")
async def read_praat(user: User = Depends(get_current_user)):
    try:
        identity = await asyncio.to_thread(praat_identity, configured_path())
        return {"available": True, "version": identity["version"], "path": identity["path"]}
    except ValueError as exc:
        return {"available": False, "message": str(exc)}


class PraatChoice(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


@router.put("/praat")
async def choose_praat(body: PraatChoice, user: User = Depends(get_current_user)):
    destination = config_path()
    try:
        identity = await asyncio.to_thread(praat_identity, body.path)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"path": identity["path"]}, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(destination)
    return {"available": True, "version": identity["version"], "path": identity["path"]}
