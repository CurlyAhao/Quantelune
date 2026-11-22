"""Praat time/label roundtrip with shared validation for text and binary files."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.exceptions import StatForgeError
from app.models.workspace import WorkspaceRecord
from app.schemas.textgrid import GridItem, GridTier, TextGridDocument
from app.services import material_service


def fail(message, status=422):
    return StatForgeError(status, "TEXTGRID_INPUT", message)


# Praat treats free-standing numbers, quoted strings and flags as data;
# bracketed item indexes and everything after ! outside a string are comments.
TOKENS = re.compile(
    r'(?<!\S)(?:"(?:[^"]|"")*"|<[^>]+>|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?!\S)|![^\r\n]*',
    re.S,
)


def decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        raise fail("请在 Praat 中另存为 Unicode 文本 TextGrid 后导入。") from None


async def convert_format(data: bytes, format_name: str) -> bytes:
    from app.services.multimodal_cases import runtime_config

    config = runtime_config()
    with tempfile.TemporaryDirectory(
        prefix="textgrid-", dir=material_service.temp_parent()
    ) as directory:
        source, destination = (
            Path(directory) / "input.TextGrid",
            Path(directory) / "output.TextGrid",
        )
        source.write_bytes(data)
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                config["python"],
                "-B",
                "-I",
                str(Path(__file__).with_name("textgrid_format_worker.py")),
                str(source),
                str(destination),
                format_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,
                    "TEMP": directory,
                    "TMP": directory,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONIOENCODING": "utf-8",
                },
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            await asyncio.wait_for(process.communicate(), timeout=60)
            if process.returncode:
                message = (
                    "Praat 格式转换组件不可用，请检查现有媒体运行环境。"
                    if process.returncode == 2
                    else "TextGrid 无法转换，请在 Praat 中检查文件内容。"
                )
                raise fail(message)
            return destination.read_bytes()
        except (TimeoutError, OSError):
            raise fail("TextGrid 转换未完成，请检查本机媒体运行环境后重试。") from None
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()


def parse(text: str):
    tokens = [match.group() for match in TOKENS.finditer(text) if not match.group().startswith("!")]
    index = 0

    def take():
        nonlocal index
        if index >= len(tokens):
            raise ValueError("missing token")
        value = tokens[index]
        index += 1
        return value

    def string():
        value = take()
        if not value.startswith('"'):
            raise ValueError("expected text")
        return value[1:-1].replace('""', '"')

    def number():
        value = take()
        if not Decimal(value).is_finite():
            raise ValueError("nonfinite")
        return value

    def count():
        value = Decimal(number())
        if value < 0 or value != int(value):
            raise ValueError("bad count")
        return int(value)

    try:
        if string() not in ("ooTextFile", "ooTextFile short") or string() != "TextGrid":
            raise ValueError("not a TextGrid")
        start, end, exists = number(), number(), take()
        if Decimal(end) <= Decimal(start) or exists not in ("<exists>", "<absent>"):
            raise ValueError("domain")
        tiers = []
        for _ in range(count() if exists == "<exists>" else 0):
            kind, name, a, b, size = string(), string(), number(), number(), count()
            if kind not in ("IntervalTier", "TextTier"):
                raise ValueError("unsupported tier")
            items = [
                GridItem(
                    start=number(), end=number() if kind == "IntervalTier" else None, text=string()
                )
                for _ in range(size)
            ]
            tiers.append(GridTier(type=kind, name=name, start=a, end=b, items=items))
        if index != len(tokens):
            raise ValueError("extra data")
        return start, end, tiers
    except (ValueError, ArithmeticError):
        raise fail(
            "TextGrid 文本不完整或时间边界不连续；请在 Praat 中打开并另存为文本格式。"
        ) from None


def quote(value: str):
    return '"' + value.replace('"', '""') + '"'


def export(document: TextGridDocument) -> str:
    offset = Decimal(document.offset)

    def time(value):
        return value if not offset else str(Decimal(value) + offset)

    lines = [
        'File type = "ooTextFile"',
        'Object class = "TextGrid"',
        "",
        "xmin = " + time(document.start),
        "xmax = " + time(document.end),
        "tiers? <exists>",
        f"size = {len(document.tiers)}",
        "item []:",
    ]
    for index, tier in enumerate(document.tiers, 1):
        lines += [
            f"    item [{index}]:",
            "        class = " + quote(tier.type),
            "        name = " + quote(tier.name),
            "        xmin = " + time(tier.start),
            "        xmax = " + time(tier.end),
        ]
        kind = "intervals" if tier.type == "IntervalTier" else "points"
        lines.append(f"        {kind}: size = {len(tier.items)}")
        for j, item in enumerate(tier.items, 1):
            lines.append(f"        {kind} [{j}]:")
            if item.end is not None:
                lines += [
                    "            xmin = " + time(item.start),
                    "            xmax = " + time(item.end),
                    "            text = " + quote(item.text),
                ]
            else:
                lines += [
                    "            number = " + time(item.start),
                    "            mark = " + quote(item.text),
                ]
    return "\n".join(lines) + "\n"


def check_bounds(document):
    offset = Decimal(document.offset)
    duration = Decimal(document.duration)
    bounds = [Decimal(document.start), Decimal(document.end)]
    for tier in document.tiers:
        bounds += [Decimal(tier.start), Decimal(tier.end)]
        for item in tier.items:
            bounds += [Decimal(item.start)] + ([Decimal(item.end)] if item.end is not None else [])
    a, b = min(bounds) + offset, max(bounds) + offset
    # Only representation precision is tolerated, never clamp stored annotation times.
    if a < Decimal("-0.000000001") or b > duration + Decimal("0.000000001"):
        raise fail(f"标注范围 {a}–{b} 秒超出录音 0–{duration} 秒；请调整导入偏移，或选择对应录音。")


async def validate_binding(db, document, project, user):
    material = await material_service.load(db, document.material_id, user.id, project)
    if material.sha256 != document.material_sha256 or material.version != document.material_version:
        raise fail("录音版本已变化；请打开原版本，或为当前录音重新导入 TextGrid。", 409)
    await material_service.verify_version(material)
    check_bounds(document)


async def list_grids(db, material_id, user):
    from app.preview.workspace import read_record

    material = await material_service.load(db, material_id, user.id)
    rows = (
        await db.scalars(
            select(WorkspaceRecord)
            .where(
                WorkspaceRecord.project_id == material.project_id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "textgrid",
            )
            .order_by(WorkspaceRecord.created_at.desc())
        )
    ).all()
    result = []
    for row in rows:
        value = TextGridDocument.model_validate_json(row.payload_json)
        if (
            value.material_id == material.id
            and value.material_sha256 == material.sha256
            and value.material_version == material.version
        ):
            result.append(await read_record(db, row.id, user))
    return result


async def material_duration(material):
    meta = json.loads(material.metadata_json)
    duration = meta.get("duration_seconds")
    if not duration and material.mime_type.startswith("video/"):
        import anyio

        from app.services import storage_service
        from app.services.media_features import _run
        from app.services.multimodal_cases import runtime_config

        config = runtime_config()
        path = storage_service.get_local_object_path(material.object_key)
        try:
            probe = json.loads(
                await anyio.to_thread.run_sync(
                    _run,
                    [
                        str(config["options"]["ffprobe_path"]),
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(path),
                    ],
                )
            )
            duration = probe["format"]["duration"]
        except (KeyError, ValueError, OSError, RuntimeError):
            raise fail("无法读取视频时长；请检查本机 FFmpeg 配置，或打开对应 WAV 录音。") from None
    if not duration or not material.mime_type.startswith(("audio/", "video/")):
        raise fail("无法确认此录音的时长；请先打开对应 WAV 录音。")
    return str(duration)


def new_tier(kind, name, start, end):
    return GridTier(
        type=kind,
        name=name,
        start=start,
        end=end,
        items=[GridItem(start=start, end=end, text="")] if kind == "IntervalTier" else [],
    )


async def create_grid(db, material_id, user, title=None):
    from app.preview.workspace import save_record
    from app.schemas.workspace import SaveRecord

    material = await material_service.load(db, material_id, user.id)
    duration = await material_duration(material)
    document = TextGridDocument(
        title=title or material.filename.rsplit(".", 1)[0] + ".TextGrid",
        material_id=material.id,
        material_sha256=material.sha256,
        material_version=material.version,
        duration=duration,
        start="0",
        end=duration,
        offset="0",
        imported_text="",
        tiers=[
            new_tier("IntervalTier", "区间", "0", duration),
            new_tier("TextTier", "事件", "0", duration),
        ],
    )
    return await save_record(
        db, user, SaveRecord(project_id=material.project_id, payload=document), textgrid_write=True
    )


async def import_grid(db, material_id, user, filename, data, offset):
    from app.preview.workspace import save_record
    from app.schemas.workspace import SaveRecord

    material = await material_service.load(db, material_id, user.id)
    duration = await material_duration(material)
    binary = data.startswith(b"ooBinaryFile")
    text = decode(await convert_format(data, "long_text") if binary else data)
    start, end, tiers = parse(text)
    try:
        document = TextGridDocument(
            title=filename,
            material_id=material.id,
            material_sha256=material.sha256,
            material_version=material.version,
            duration=str(duration),
            start=start,
            end=end,
            offset=offset,
            tiers=tiers,
            imported_text=text,
            imported_binary_base64=base64.b64encode(data).decode("ascii") if binary else None,
        )
    except (ValueError, ArithmeticError):
        raise fail("偏移须填写秒数。") from None
    return await save_record(
        db, user, SaveRecord(project_id=material.project_id, payload=document), textgrid_write=True
    )


def merge_text(left, right):
    if not left or left == right:
        return right
    return left if not right else left + "\n" + right


def split_at(tier, time, text_split=None, text=None, expected_item=None):
    for index, item in enumerate(tier.items):
        if Decimal(item.start) < Decimal(time) < Decimal(item.end):
            if expected_item is not None and index != expected_item:
                raise fail("光标不在所选区间内；请重新选择区间或光标位置。")
            label = item.text if text is None else text
            if text_split is not None and text_split > len(label):
                raise fail("文字光标已变化；请重新选择拆分位置。")
            left, right = (
                (label, "") if text_split is None else (label[:text_split], label[text_split:])
            )
            tier.items[index : index + 1] = [
                GridItem(start=item.start, end=time, text=left),
                GridItem(start=time, end=item.end, text=right),
            ]
            return index
    raise fail("请把光标放在区间内部；此处已经是边界或不在该层范围内。")


async def edit_grid(db, record_id, user, change):
    from app.preview.workspace import read_record, save_record
    from app.schemas.workspace import SaveRecord

    record = await read_record(db, record_id, user)
    if record["payload"]["kind"] != "textgrid":
        raise fail("找不到此时间标注。", 404)
    document = TextGridDocument.model_validate(record["payload"])
    action = change.action
    focus = None
    if action == "restore":
        if change.restore_revision is None or change.restore_revision >= record["revision"]:
            raise fail("请选择此标注的较早版本。")
        previous = await read_record(db, record_id, user, change.restore_revision)
        document = TextGridDocument.model_validate(previous["payload"])
    elif action == "add_tier":
        if change.tier_type is None:
            raise fail("请选择区间层或点层。")
        prefix = "区间" if change.tier_type == "IntervalTier" else "事件"
        names = {tier.name for tier in document.tiers}
        name = prefix
        count = 2
        while name in names:
            name, count = f"{prefix} {count}", count + 1
        position = len(document.tiers) if change.position is None else change.position
        if position > len(document.tiers):
            raise fail("层顺序已变化，请重新打开。", 409)
        document.tiers.insert(
            position, new_tier(change.tier_type, name, document.start, document.end)
        )
        focus = {"tier": position, "item": None}
    elif change.tier >= len(document.tiers):
        raise fail("所选层已变化，请重新打开。", 409)
    else:
        tier = document.tiers[change.tier]
        focus = {"tier": change.tier, "item": None}
        if action == "rename_tier":
            if not change.name or not change.name.strip():
                raise fail("请填写层名。")
            tier.name = change.name.strip()
        elif action == "move_tier":
            if change.position is None or change.position >= len(document.tiers):
                raise fail("层顺序已变化，请重新打开。", 409)
            document.tiers.insert(change.position, document.tiers.pop(change.tier))
            focus["tier"] = change.position
        elif action == "delete_tier":
            document.tiers.pop(change.tier)
            focus["tier"] = max(0, min(change.tier, len(document.tiers) - 1))
        elif action in ("split_interval", "range_interval", "add_point"):
            # Incoming cursor/selection uses the displayed clock. Convert once,
            # with decimal seconds, back into this TextGrid's stored clock.
            if change.start is None:
                raise fail("请先在波形上选择时间位置。")
            shift = Decimal(document.offset) + Decimal(change.clock_shift)
            start = str(Decimal(change.start) - shift)
            if not Decimal(tier.start) <= Decimal(start) <= Decimal(tier.end):
                raise fail("所选时间不在此层范围内。")
            if action == "add_point":
                if tier.type != "TextTier":
                    raise fail("请在点层添加事件。")
                if any(Decimal(item.start) == Decimal(start) for item in tier.items):
                    raise fail("此时刻已有一个点；请直接编辑该点。")
                tier.items.append(GridItem(start=start, text=change.text or ""))
                tier.items.sort(key=lambda item: Decimal(item.start))
                focus["item"] = next(i for i, item in enumerate(tier.items) if item.start == start)
            else:
                if tier.type != "IntervalTier":
                    raise fail("请在区间层拆分或建立区间。")
                if action == "split_interval":
                    focus["item"] = split_at(
                        tier, start, change.text_split, change.text, change.item
                    )
                else:
                    if change.end is None:
                        raise fail("请先选择一段时间。")
                    end = str(Decimal(change.end) - shift)
                    if not Decimal(start) < Decimal(end) <= Decimal(tier.end):
                        raise fail("选段须在此层范围内，且结束时间晚于开始时间。")
                    for time in (start, end):
                        if not any(
                            Decimal(time) == Decimal(edge)
                            for item in tier.items
                            for edge in (item.start, item.end)
                        ):
                            split_at(tier, time)
                    indices = [
                        i
                        for i, item in enumerate(tier.items)
                        if Decimal(start) <= Decimal(item.start)
                        and Decimal(item.end) <= Decimal(end)
                    ]
                    if not indices:
                        raise fail("此层没有可用区间，请先添加区间层。")
                    label = ""
                    for i in indices:
                        label = merge_text(label, tier.items[i].text)
                    tier.items[indices[0] : indices[-1] + 1] = [
                        GridItem(start=start, end=end, text=label)
                    ]
                    focus["item"] = indices[0]
        elif action in ("merge_previous", "merge_next", "delete_point"):
            if change.item is None or change.item >= len(tier.items):
                raise fail("请先选择要修改的区间或点。")
            if action == "delete_point":
                if tier.type != "TextTier":
                    raise fail("区间请通过合并来移除内部边界。")
                tier.items.pop(change.item)
            else:
                i = change.item - 1 if action == "merge_previous" else change.item
                if tier.type != "IntervalTier" or i < 0 or i + 1 >= len(tier.items):
                    raise fail("此处没有可以合并的相邻区间。")
                left, right = tier.items[i : i + 2]
                tier.items[i : i + 2] = [
                    GridItem(
                        start=left.start, end=right.end, text=merge_text(left.text, right.text)
                    )
                ]
                focus["item"] = i
        elif action == "edit":
            edit_existing(tier, change)
            if change.item is None and change.theme_id:
                from app.services.text_coding import read

                coding = await read(db, uuid.UUID(record["project_id"]), user)
                theme = next(
                    (
                        t
                        for t in coding["payload"]["themes"]
                        if t.get("is_codable", True)
                        and str(change.theme_id) in [t["id"], *t.get("merged_ids", [])]
                    ),
                    None,
                )
                if theme is None:
                    raise fail("所选主题已变化，请重新选择。", 409)
                tier.theme_id = uuid.UUID(theme["id"])
            if change.item is not None:
                focus["item"] = change.item
                if tier.type == "TextTier" and change.start is not None:
                    tier.items.sort(key=lambda item: Decimal(item.start))
                    focus["item"] = next(
                        i for i, item in enumerate(tier.items) if item.start == change.start
                    )
    try:
        document = TextGridDocument.model_validate(document.model_dump())
    except ValueError:
        raise fail("时间须保持层内顺序，且相邻区间不能重叠。") from None
    result = await save_record(
        db,
        user,
        SaveRecord(
            project_id=record["project_id"], expected_revision=change.revision, payload=document
        ),
        record_id,
        textgrid_write=True,
    )
    result["selection"] = focus
    return result


def edit_existing(tier, change):
    if change.item is None:
        tier.theme_id = change.theme_id
    else:
        if change.item >= len(tier.items):
            raise fail("所选标注已变化，请重新打开。", 409)
        i = change.item
        item = tier.items[i]
        if change.text is not None:
            item.text = change.text
        if change.start is not None and not Decimal(tier.start) <= Decimal(change.start) <= Decimal(
            tier.end
        ):
            raise fail("标注时间须在此层范围内。")
        if change.end is not None and not Decimal(tier.start) <= Decimal(change.end) <= Decimal(
            tier.end
        ):
            raise fail("标注时间须在此层范围内。")
        if change.start is not None:
            if (
                tier.type == "IntervalTier"
                and i == 0
                and Decimal(change.start) != Decimal(item.start)
            ):
                raise fail("第一段起点是层边界；请选择内部边界调整。")
            item.start = change.start
            if tier.type == "IntervalTier" and i:
                tier.items[i - 1].end = change.start
        if change.end is not None:
            if tier.type != "IntervalTier":
                raise fail("点标注只需一个时间。")
            if i == len(tier.items) - 1 and Decimal(change.end) != Decimal(item.end):
                raise fail("最后一段终点是层边界；请选择内部边界调整。")
            item.end = change.end
            if i + 1 < len(tier.items):
                tier.items[i + 1].start = change.end
