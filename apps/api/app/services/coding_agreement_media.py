"""Area and original-clock agreement; source identity and units never pooled."""

import csv
import io
import json
import math
from decimal import ROUND_HALF_UP, Decimal

from app.exceptions import StatForgeError
from app.schemas.material import CitationRequest
from app.services import (
    coding_attributes,
    coding_relations,
    material_service,
    storage_service,
    text_coding,
)
from app.services.coding_agreement import compare, metrics

IMAGE_METHOD = (
    "原图像素；归一化边界乘原图尺寸后四舍五入；含左上边界、不含右下边界；各编码者区域先求并集"
)
TIME_METHOD = "原材料秒；边界与完整时长四舍五入到 1 微秒；含起点、不含终点；各编码者区间先求并集"


def pixel(value, size):
    return max(0, min(size, math.floor(value * size + 0.5)))


def rectangles(width, height, a, b):
    """Vertical slabs + interval unions: exact area, without a pixel bitmap."""
    boundaries = sorted({0, width, *[v for rect in a + b for v in (rect[0], rect[2])]})
    counts = dict.fromkeys(("both", "only_a", "only_b", "neither"), 0)
    differences, previous = [], {}
    for left, right in zip(boundaries, boundaries[1:]):
        spans = [
            [(y0, y1) for x0, y0, x1, y1 in group if x0 <= left and x1 >= right] for group in (a, b)
        ]
        column, gaps = compare(height, *spans)
        for key in counts:
            counts[key] += column[key] * (right - left)
        current = {}
        for top, bottom, kind in gaps:
            key = (top, bottom, kind)
            if key in previous:
                index = previous[key]
                differences[index][2] = right
            else:
                index = len(differences)
                differences.append([left, top, right, bottom, kind])
            current[key] = index
        previous = current
    return counts, differences


def micros(value):
    return int((Decimal(str(value)) * 1000000).to_integral_value(rounding=ROUND_HALF_UP))


async def dimensions(material):
    import anyio

    # Read the actual original, including orientation used by the image viewer.
    def read():
        from PIL import Image

        with Image.open(storage_service.get_local_object_path(material.object_key)) as image:
            width, height = image.size
            if image.getexif().get(274, 1) in (5, 6, 7, 8):
                width, height = height, width
            return width, height

    try:
        return await anyio.to_thread.run_sync(read)
    except (ImportError, OSError, ValueError):
        raise text_coding.fail(
            f"无法读取 {material.filename} 的原图尺寸，请重新打开图片。", 422
        ) from None


async def analyze_media(items, document, request, state, scope, case, coders, theme, excluded):
    from app.services.textgrid import material_duration

    conditions = coding_attributes.validated_conditions(document)
    current = set(scope["current_segment_ids"])
    image = request.modality == "image"
    locator_kind = "region" if image else "time"
    rows, differences, unrecorded = [], [], 0
    for source in items:
        material = source["material"]
        if request.material_id and material.id != request.material_id:
            continue
        keys = source["cases"]
        if request.case_key:
            if request.case_key not in keys:
                continue
            keys = {request.case_key}
        if not any(
            coding_attributes.matches_source(
                document, conditions, key, material.id, material.sha256, material.version
            )
            for key in (keys or {None})
        ):
            continue
        sides = {request.coder_a: [], request.coder_b: []}
        for segment in document.segments:
            if (
                segment.material_id != material.id
                or segment.material_sha256 != material.sha256
                or segment.material_version != material.version
            ):
                continue
            if (
                str(segment.id) not in current
                or segment.locator.kind != locator_kind
                or theme.id not in segment.theme_ids
            ):
                continue
            if not segment.coder_id:
                unrecorded += 1
            if segment.coder_id not in sides:
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
                    f"{material.filename} 的标注位置不在当前原材料范围内，请回查原标注。", 422
                ) from None
            sides[segment.coder_id].append(segment.locator)
        target = {
            "material_id": str(material.id),
            "material_sha256": material.sha256,
            "material_version": material.version,
            "filename": material.filename,
        }
        row = {**target, "unit": "像素²" if image else "秒", "start": 0, "page": None}
        if image:
            width, height = await dimensions(material)
            rects = [
                [
                    (
                        pixel(loc.x, width),
                        pixel(loc.y, height),
                        pixel(loc.x + loc.width, width),
                        pixel(loc.y + loc.height, height),
                    )
                    for loc in sides[coder]
                ]
                for coder in (request.coder_a, request.coder_b)
            ]
            rects = [[r for r in group if r[2] > r[0] and r[3] > r[1]] for group in rects]
            counts, gaps = rectangles(width, height, *rects)
            row.update(width=width, height=height, end=width * height, **counts, **metrics(counts))
            overlays = [
                {
                    "side": side,
                    "name": coders[coder],
                    "regions": [
                        {
                            "kind": "region",
                            "x": x0 / width,
                            "y": y0 / height,
                            "width": (x1 - x0) / width,
                            "height": (y1 - y0) / height,
                            "note": "",
                        }
                        for x0, y0, x1, y1 in group
                    ],
                }
                for side, coder, group in zip(("a", "b"), (request.coder_a, request.coder_b), rects)
            ]
            for x0, y0, x1, y1, kind in gaps:
                locator = {
                    "kind": "region",
                    "x": x0 / width,
                    "y": y0 / height,
                    "width": (x1 - x0) / width,
                    "height": (y1 - y0) / height,
                    "note": "",
                }
                differences.append(
                    {
                        "kind": kind,
                        "area": (x1 - x0) * (y1 - y0),
                        "pixels": [x0, y0, x1, y1],
                        "target": {**target, "locator": locator, "comparison": overlays},
                        "source_index": len(rows),
                    }
                )
        else:
            duration = None
            try:
                measured = float(await material_duration(material))
                if math.isfinite(measured) and measured > 0:
                    duration = micros(measured)
            except (StatForgeError, ValueError):
                pass
            intervals = [
                [(micros(loc.start_seconds), micros(loc.end_seconds)) for loc in sides[coder]]
                for coder in (request.coder_a, request.coder_b)
            ]
            if duration is not None and any(
                end > duration + 1 for group in intervals for _, end in group
            ):
                raise text_coding.fail(
                    f"{material.filename} 的标注超出原文件时长，请回查原标注。", 422
                )
            if duration is not None:
                intervals = [
                    [(start, min(end, duration)) for start, end in group] for group in intervals
                ]
            endpoint = (
                duration
                if duration is not None
                else max((end for group in intervals for _, end in group), default=0)
            )
            counts, gaps = compare(endpoint, *intervals)
            numbers = metrics(counts)
            row.update(**{key: value / 1000000 for key, value in counts.items()}, **numbers)
            row.update(
                total=duration / 1000000 if duration is not None else None,
                end=duration / 1000000 if duration is not None else None,
            )
            if duration is None:
                row.update(neither=None, agreement=None, kappa=None, duration_missing=True)
            for start, end, kind in gaps:
                locator = {
                    "kind": "time",
                    "start_seconds": start / 1000000,
                    "end_seconds": end / 1000000,
                    "note": "",
                }
                differences.append(
                    {
                        "kind": kind,
                        "start": start / 1000000,
                        "end": end / 1000000,
                        "target": {**target, "locator": locator},
                        "source_index": len(rows),
                    }
                )
        rows.append(row)
    names = coding_relations.theme_names(document.themes)
    return {
        "schema_version": 1,
        "modality": request.modality,
        "revision": state["revision"],
        "request": request.model_dump(mode="json"),
        "scope": scope,
        "scope_label": scope["scope_label"],
        "unit": IMAGE_METHOD if image else TIME_METHOD,
        "coder_a": {"id": request.coder_a, "name": coders[request.coder_a]},
        "coder_b": {"id": request.coder_b, "name": coders[request.coder_b]},
        "theme": {"id": str(theme.id), "name": names[str(theme.id)]},
        "summary": None,
        "sources": rows,
        "differences": differences,
        "excluded": excluded,
        "unrecorded": unrecorded,
    }


def export_media(data):
    from app.services.coding_matrix import safe

    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "记录类型",
            "范围",
            "编码修订",
            "保存时间",
            "材料类型",
            "单位",
            "方法",
            "编码者A_ID",
            "编码者A",
            "编码者B_ID",
            "编码者B",
            "主题ID",
            "主题",
            "来源",
            "材料ID",
            "材料版本",
            "材料SHA256",
            "原图宽",
            "原图高",
            "完整范围",
            "双方编码",
            "仅A编码",
            "仅B编码",
            "双方未编码",
            "总体一致率",
            "已编码交并比",
            "Cohen_kappa",
            "分歧",
            "分歧开始_秒",
            "分歧结束_秒",
            "像素左",
            "像素上",
            "像素右_不含",
            "像素下_不含",
            "分歧像素数",
            "定位记录",
        ]
    )

    def write(row, difference=None):
        d = difference or {}
        writer.writerow(
            [
                safe(v)
                for v in [
                    "分歧" if d else "来源",
                    data["scope_label"],
                    data["revision"],
                    data.get("saved_at"),
                    data["modality"],
                    row["unit"],
                    data["unit"],
                    data["coder_a"]["id"],
                    data["coder_a"]["name"],
                    data["coder_b"]["id"],
                    data["coder_b"]["name"],
                    data["theme"]["id"],
                    data["theme"]["name"],
                    *[
                        row.get(k)
                        for k in (
                            "filename",
                            "material_id",
                            "material_version",
                            "material_sha256",
                            "width",
                            "height",
                            "total",
                            "both",
                            "only_a",
                            "only_b",
                            "neither",
                            "agreement",
                            "coded_overlap",
                            "kappa",
                        )
                    ],
                    {"only_a": "仅A编码", "only_b": "仅B编码"}.get(d.get("kind")),
                    d.get("start"),
                    d.get("end"),
                    *d.get("pixels", [None] * 4),
                    d.get("area"),
                    json.dumps(d["target"], ensure_ascii=False) if d else "",
                ]
            ]
        )

    for row in data["sources"]:
        write(row)
    for d in data["differences"]:
        write(data["sources"][d["source_index"]], d)
    return {
        "csv": "\ufeff" + stream.getvalue(),
        "filename": {"image": "图片", "audio": "音频", "video": "视频"}[data["modality"]]
        + "编码者一致性.csv",
    }
