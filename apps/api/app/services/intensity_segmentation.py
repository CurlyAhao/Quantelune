"""Append an editable intensity suggestion tier to an existing time annotation."""

import asyncio
import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

from app.preview.workspace import read_record, save_record
from app.schemas.textgrid import GridItem, GridTier, TextGridDocument
from app.schemas.workspace import SaveRecord
from app.services import (
    dataset_service,
    local_workspaces,
    material_service,
    storage_service,
    textgrid,
)
from app.services.audio_spectrum import source_context
from app.services.multimodal_cases import invalid, read_json, runtime_config


async def generate(db, user, material_id, body):
    material = await material_service.load(db, material_id, user.id)
    dataset = await dataset_service.get_dataset_with_access(db, body.dataset_id, user.id)
    original, clock = await source_context(db, user, dataset, body.package_id, body.sample_id)
    if original.project_id != material.project_id or original.sha256 != material.sha256:
        raise invalid("所选声源与时间标注不对应，请回到原录音重新打开。")
    await material_service.verify_version(material)
    record = await read_record(db, body.record_id, user) if body.record_id else None
    if record:
        if record["payload"]["kind"] != "textgrid" or record["revision"] != body.revision:
            raise invalid("时间标注已更新，请重新打开后分段。", 409)
        document = TextGridDocument.model_validate(record["payload"])
        if document.material_id != material_id:
            raise invalid("所选时间标注属于另一份录音。")
        await textgrid.validate_binding(db, document, material.project_id, user)
    else:
        duration = await textgrid.material_duration(material)
        document = TextGridDocument(
            title=material.filename.rsplit(".", 1)[0] + ".TextGrid",
            material_id=material.id,
            material_sha256=material.sha256,
            material_version=material.version,
            duration=duration,
            start="0",
            end=duration,
            offset="0",
            tiers=[],
            imported_text="",
        )
    input_origin = clock.get("input_origin", 0.0)
    request_start = None if body.start is None else body.start + clock["offset"] - input_origin
    request_end = None if body.end is None else body.end + clock["offset"] - input_origin
    root = local_workspaces.project_root(material.project_id)
    if root is None:
        raise invalid("请先打开材料所在的工作区文件夹。")
    output = root / ".statforge" / "storage" / "intensity-segments" / uuid.uuid4().hex
    local_workspaces.checked_path(output)
    output.mkdir(parents=True, exist_ok=False)
    config = runtime_config()
    request = {
        **body.model_dump(mode="json"),
        "source": str(storage_service.get_local_object_path(dataset.object_key)),
        "source_sha256": dataset.sha256,
        "output": str(output),
        "input_start": request_start,
        "input_end": request_end,
        "input_container_origin": clock.get("audio_origin") if dataset.id == original.id else 0,
        "ffmpeg": config["options"]["ffmpeg_path"],
        "ffprobe": config["options"]["ffprobe_path"],
    }
    request_path = output / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            config["python"],
            "-B",
            str(Path(__file__).with_name("audio_preprocessing.py")),
            str(request_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "TEMP": config["temp"],
                "TMP": config["temp"],
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
            },
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        _, stderr = await process.communicate()
        (output / "process.log").write_bytes(stderr)
        if process.returncode:
            failure = (
                read_json(output / "failure.json") if (output / "failure.json").exists() else {}
            )
            raise invalid(failure.get("message") or "强度分段失败，请检查本地 Praat 运行环境。")
        result = read_json(output / "regions.json")
        # Input WAV zero can represent an original selection; grid offset is separate.
        shift = Decimal(str(input_origin)) - Decimal(document.offset)
        left, right = Decimal(document.start), Decimal(document.end)
        items = []
        cursor = left
        for interval in result["intervals"]:
            a, b = Decimal(str(interval["start"])) + shift, Decimal(str(interval["end"])) + shift
            if a < left - Decimal("0.000000001") or b > right + Decimal("0.000000001"):
                raise invalid("分段范围超出此时间标注，请使用原录音的完整时间标注。")
            a, b = max(a, left), min(b, right)
            if b <= a:
                continue
            if a > cursor:
                items.append(GridItem(start=str(cursor), end=str(a), text=""))
            items.append(GridItem(start=str(a), end=str(b), text=interval["text"]))
            cursor = b
        if cursor < right:
            items.append(GridItem(start=str(cursor), end=str(right), text=""))
        names = {tier.name for tier in document.tiers}
        name, counter = "强度分段", 2
        while name in names:
            name, counter = f"强度分段 {counter}", counter + 1
        method = {
            **result["method"],
            "input_dataset_id": str(dataset.id),
            "input_name": dataset.original_filename,
            "original_dataset_id": str(original.id),
            "original_sha256": original.sha256,
            "input_origin_seconds": input_origin,
            "display_offset_seconds": clock["offset"],
            "preprocessing": clock.get("preprocessing"),
            "artifact_directory": str(output),
            "original_start": result["method"]["input_start"] + input_origin,
            "original_end": result["method"]["input_end"] + input_origin,
        }
        document.tiers.append(
            GridTier(
                type="IntervalTier",
                name=name,
                start=document.start,
                end=document.end,
                items=items,
                generation=method,
            )
        )
        document = TextGridDocument.model_validate(document.model_dump())
        await textgrid.validate_binding(db, document, material.project_id, user)
        saved = await save_record(
            db,
            user,
            SaveRecord(
                project_id=material.project_id,
                expected_revision=body.revision if body.record_id else 0,
                payload=document,
            ),
            body.record_id,
            textgrid_write=True,
        )
        saved["selection"] = {"tier": len(document.tiers) - 1, "item": None}
        return saved
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
