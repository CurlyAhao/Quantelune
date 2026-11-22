"""Character relations over pinned text annotations; no media/time approximation."""

import csv
import io
import json
from bisect import bisect_left
from collections import defaultdict

from app.exceptions import StatForgeError
from app.schemas.coding import CodingDocument
from app.schemas.material import CitationRequest
from app.services import coding_attributes, material_service, text_coding

PAGE_SIZE = 50
RELATION_NAMES = {
    "equal": "相同区间",
    "contains": "完全包含",
    "overlap": "部分重叠",
    "touching": "相接",
    "separate": "分离",
}
UNIT = "Unicode 字符（CRLF 统一为 LF），含起点、不含终点"


def theme_names(themes):
    by_id = {str(t.id): t for t in themes}
    names = {}
    for key, theme in by_id.items():
        parts, parent = [theme.name], theme.parent_id
        while parent:
            ancestor = by_id[str(parent)]
            parts.insert(0, ancestor.name)
            parent = ancestor.parent_id
        names[key] = " / ".join(parts)
    return names


async def inputs(db, project, user, request):
    state = await text_coding.read(db, project, user)
    if state["revision"] != request.revision:
        raise text_coding.fail("标注已更新，请刷新共现与关系后再导出。")
    document = CodingDocument.model_validate(state["payload"])
    case = await text_coding.selected_case(
        db, project, user, state, request.case_collection_id, request.case_sample_id
    )
    scope = coding_attributes.scoped(state, document, case)
    current = set(scope["current_segment_ids"])
    all_text = [
        s for s in document.segments if s.locator.kind == "text" and s.material_id and s.theme_ids
    ]
    # Missing authorship is not evidence that two legacy annotations have one coder.
    coders = {s.coder_id: s.coder_name or "已记录编码者" for s in all_text if s.coder_id}
    codable = {str(t.id): t for t in document.themes if t.is_codable}
    selected = (
        set(str(t) for t in request.theme_ids) if request.theme_ids is not None else set(codable)
    )
    if not selected <= codable.keys():
        raise text_coding.fail("主题已变化，请重新选择；主题分组不参与计算。", 422)
    if request.pair and (
        len(set(request.pair)) != 2 or not {str(t) for t in request.pair} <= selected
    ):
        raise text_coding.fail("请选择两个不同的当前主题。", 422)
    names = theme_names(document.themes)
    selected_themes = [{"id": key, "name": names[key]} for key in codable if key in selected]
    candidates = [
        s
        for s in all_text
        if str(s.id) in current and selected.intersection(str(t) for t in s.theme_ids)
    ]
    unrecorded = sum(not s.coder_id for s in candidates)
    candidates = [
        s
        for s in candidates
        if s.coder_id and (not request.coder_id or s.coder_id == request.coder_id)
    ]
    materials, pages, groups = {}, {}, defaultdict(list)
    for segment in candidates:
        if segment.material_id not in materials:
            material = await material_service.load(db, segment.material_id, user.id, project)
            try:
                await material_service.verify_version(material)
            except StatForgeError:
                raise text_coding.fail(
                    f"{segment.filename} 的原文件已变化或不可读取，请打开原材料核对版本。"
                ) from None
            materials[segment.material_id] = material
        material = materials[segment.material_id]
        try:
            material_service.validate_locator(
                material,
                CitationRequest(
                    material_sha256=segment.material_sha256,
                    material_version=segment.material_version,
                    locator=segment.locator,
                    reviewed=True,
                ),
            )
        except (StatForgeError, ValueError):
            raise text_coding.fail(
                f"{segment.filename} 的这条标注与当前文字版本不一致，请从标注打开对应版本。"
            ) from None
        loc = segment.locator
        page_key = (
            segment.material_id,
            segment.material_sha256,
            segment.material_version,
            loc.page,
            loc.text_sha256,
        )
        if page_key not in pages:
            page = next(
                p for p in json.loads(material.extraction_json)["pages"] if p["page"] == loc.page
            )
            text = page["text"]
            pages[page_key] = [
                i for i, char in enumerate(text[:-1]) if char == "\r" and text[i + 1] == "\n"
            ]
        crlf = pages[page_key]
        # Keep the original locator intact. Only analysis coordinates collapse CRLF.
        start, end = loc.start - bisect_left(crlf, loc.start), loc.end - bisect_left(crlf, loc.end)
        if end <= start:
            continue
        groups[(*page_key, segment.coder_id)].append(
            {
                "segment": segment.model_dump(mode="json"),
                "start": start,
                "end": end,
                "theme_ids": selected.intersection(str(t) for t in segment.theme_ids),
            }
        )
    return state, scope, coders, selected_themes, groups, unrecorded


def relations(groups):
    for group in groups.values():
        for i, left in enumerate(group):
            for right in group[i:]:
                pairs = {
                    tuple(sorted((a, b)))
                    for a in left["theme_ids"]
                    for b in right["theme_ids"]
                    if a != b
                }
                for theme_a, theme_b in sorted(pairs):
                    a, b = (
                        (left, right)
                        if theme_a in left["theme_ids"] and theme_b in right["theme_ids"]
                        else (right, left)
                    )
                    overlap = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
                    distance = max(0, max(a["start"], b["start"]) - min(a["end"], b["end"]))
                    if a["start"] == b["start"] and a["end"] == b["end"]:
                        kind = "equal"
                    elif overlap and (
                        (a["start"] <= b["start"] and a["end"] >= b["end"])
                        or (b["start"] <= a["start"] and b["end"] >= a["end"])
                    ):
                        kind = "contains"
                    elif overlap:
                        kind = "overlap"
                    else:
                        kind = "touching" if distance == 0 else "separate"
                    yield {
                        "theme_a": theme_a,
                        "theme_b": theme_b,
                        "relation": kind,
                        "overlap": overlap,
                        "distance": distance,
                        "left": {k: v for k, v in a.items() if k != "theme_ids"},
                        "right": {k: v for k, v in b.items() if k != "theme_ids"},
                    }


def matches(item, request):
    return (request.relation == "all" or item["relation"] == request.relation) and (
        not request.pair or {item["theme_a"], item["theme_b"]} == {str(t) for t in request.pair}
    )


async def analyze(db, project, user, request, export=None):
    state, scope, coders, themes, groups, unrecorded = await inputs(db, project, user, request)
    counts, rows, total, positive = defaultdict(int), [], 0, 0
    csv_rows = []
    names = {t["id"]: t["name"] for t in themes}
    for item in relations(groups):
        if item["overlap"] > 0:
            counts[(item["theme_a"], item["theme_b"])] += 1
            positive += 1
        if matches(item, request):
            if export == "relations":
                a, b = item["left"], item["right"]
                s = a["segment"]
                csv_rows.append(
                    [
                        scope["scope_label"],
                        state["revision"],
                        s["coder_id"],
                        coders[s["coder_id"]],
                        s["filename"],
                        s["material_id"],
                        s["material_version"],
                        s["material_sha256"],
                        s["locator"]["page"],
                        item["theme_a"],
                        names[item["theme_a"]],
                        item["theme_b"],
                        names[item["theme_b"]],
                        RELATION_NAMES[item["relation"]],
                        item["overlap"],
                        item["distance"],
                        s["id"],
                        a["start"],
                        a["end"],
                        s["locator"]["start"],
                        s["locator"]["end"],
                        s["locator"]["quote"],
                        b["segment"]["id"],
                        b["start"],
                        b["end"],
                        b["segment"]["locator"]["start"],
                        b["segment"]["locator"]["end"],
                        b["segment"]["locator"]["quote"],
                        UNIT,
                    ]
                )
            elif request.page * PAGE_SIZE <= total < (request.page + 1) * PAGE_SIZE:
                rows.append(item)
            total += 1
    matrix = [
        [counts.get(tuple(sorted((a["id"], b["id"]))), 0) if a != b else 0 for b in themes]
        for a in themes
    ]
    comparable = sum(len(g) for g in groups.values())
    coder_label = (
        coders.get(request.coder_id, "未记录") if request.coder_id else "全部编码者（分别计算）"
    )
    if export:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        if export == "matrix":
            writer.writerow(
                [
                    "范围",
                    "编码修订",
                    "编码者",
                    "单位",
                    "主题ID",
                    "主题",
                    *[f"{t['name']} [{t['id']}]" for t in themes],
                ]
            )
            if comparable:
                for i, theme in enumerate(themes):
                    writer.writerow(
                        [
                            scope["scope_label"],
                            state["revision"],
                            coder_label,
                            UNIT,
                            theme["id"],
                            theme["name"],
                            *matrix[i],
                        ]
                    )
        else:
            writer.writerow(
                [
                    "范围",
                    "编码修订",
                    "编码者ID",
                    "编码者",
                    "来源",
                    "材料ID",
                    "材料版本",
                    "材料SHA256",
                    "页码",
                    "主题A_ID",
                    "主题A",
                    "主题B_ID",
                    "主题B",
                    "关系",
                    "重叠字符数",
                    "距离字符数",
                    "标注A_ID",
                    "A起点_LF",
                    "A终点_LF",
                    "A原文起点",
                    "A原文终点",
                    "A原文",
                    "标注B_ID",
                    "B起点_LF",
                    "B终点_LF",
                    "B原文起点",
                    "B原文终点",
                    "B原文",
                    "单位",
                ]
            )
            writer.writerows(csv_rows)
        return {
            "csv": "\ufeff" + output.getvalue(),
            "filename": "文本共现矩阵.csv" if export == "matrix" else "文本编码关系.csv",
        }
    return {
        "revision": state["revision"],
        "scope": scope,
        "coders": [{"id": key, "name": name} for key, name in coders.items()],
        "themes": themes,
        "matrix": matrix,
        "cooccurrences": positive,
        "comparable_annotations": comparable,
        "unrecorded_annotations": unrecorded,
        "relations": rows,
        "total_relations": total,
        "page": request.page,
        "page_size": PAGE_SIZE,
        "unit": UNIT,
    }
