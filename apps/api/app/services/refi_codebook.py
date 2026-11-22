"""REFI-QDA 1.0 Codes tree exchange, using existing theme identities."""

import re
import uuid
from xml.etree import ElementTree as ET

from app.exceptions import StatForgeError
from app.schemas.coding import Theme

NS = "urn:QDA-XML:codebook:1.0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
PALETTE = {
    "green": "#34735A",
    "blue": "#326BA0",
    "amber": "#926414",
    "purple": "#7953A1",
    "rose": "#A44867",
    "neutral": "#666B63",
}


def invalid(message):
    return StatForgeError(422, "CODEBOOK_INVALID", message)


def parse(text):
    """Validate the finite Codes contract in Codebook.xsd; Sets stay distinct."""
    if not text or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.I):
        raise invalid("请选择不含外部声明的 QDC 代码本。")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise invalid("QDC 文件格式不完整，请重新导出代码本。") from None
    if root.tag != f"{{{NS}}}CodeBook":
        raise invalid("请选择 REFI-QDA 1.0 的 .qdc 代码本。")
    if set(root.attrib) - {"origin", f"{{{XSI}}}schemaLocation"}:
        raise invalid("代码本包含尚不支持的根属性，请使用 REFI-QDA 1.0 导出。")
    if root.find(f"{{{NS}}}Sets") is not None:
        raise invalid(
            "此代码本包含引用集合（Sets），目前只支持标签层级。请另导出不含集合的代码本；本次未导入。"
        )
    if [child.tag for child in root] != [f"{{{NS}}}Codes"] or not len(root[0]):
        raise invalid("代码本中没有有效的 Codes 标签树。")
    if root[0].attrib:
        raise invalid("Codes 含不支持的属性。")
    themes, identities = [], set()

    def visit(element, parent=None):
        if element.tag != f"{{{NS}}}Code" or set(element.attrib) - {
            "guid",
            "name",
            "isCodable",
            "color",
        }:
            raise invalid("代码本标签结构不符合 REFI-QDA 1.0。")
        raw_guid = element.get("guid", "").strip()
        if not re.fullmatch(
            r"(?:[\da-fA-F]{8}(?:-[\da-fA-F]{4}){3}-[\da-fA-F]{12}|\{[\da-fA-F]{8}(?:-[\da-fA-F]{4}){3}-[\da-fA-F]{12}\})",
            raw_guid,
        ):
            raise invalid("代码本中有无效的标签 GUID，请重新导出。")
        identity = uuid.UUID(raw_guid)
        if identity in identities:
            raise invalid("代码本中同一 GUID 出现多次，无法确定标签关系。")
        identities.add(identity)
        name = element.get("name", "")
        if not name.strip() or len(name) > 80:
            raise invalid("标签名称不能为空，且应不超过 80 字。")
        codable = element.get("isCodable", "").strip()
        if codable not in {"true", "false", "1", "0"}:
            raise invalid(f"“{name}”缺少有效的可编码属性。")
        rgb = element.get("color")
        if rgb is not None:
            rgb = rgb.strip()
            if not re.fullmatch(r"#(?:[\da-fA-F]{3}|[\da-fA-F]{6})", rgb):
                raise invalid(f"“{name}”的颜色无效，请使用 RGB 颜色。")
            rgb = ("#" + "".join(c * 2 for c in rgb[1:]) if len(rgb) == 4 else rgb).upper()
        children = list(element)
        description = ""
        if children and children[0].tag == f"{{{NS}}}Description":
            desc = children.pop(0)
            if desc.attrib or len(desc):
                raise invalid(f"“{name}”的说明应为纯文字。")
            description = desc.text or ""
        if len(description) > 2000:
            raise invalid(f"“{name}”的说明超过 2000 字，本次未导入。")
        themes.append(
            Theme(
                id=identity,
                name=name,
                parent_id=parent,
                memo=description,
                is_codable=codable in {"true", "1"},
                color_rgb=rgb,
            )
        )
        if len(themes) > 500:
            raise invalid("代码本超过项目的 500 个标签上限，本次未导入。")
        for child in children:
            visit(child, identity)

    for code in root[0]:
        visit(code)
    return themes


def combine(existing, imported):
    # A name is never an identity. Reimporting also leaves local edits intact.
    matches = {
        identity: theme.id for theme in existing for identity in [theme.id, *theme.merged_ids]
    }
    result = list(existing)
    for theme in imported:
        if theme.id not in matches:
            result.append(
                theme.model_copy(
                    update={"parent_id": matches.get(theme.parent_id, theme.parent_id)}
                )
            )
            matches[theme.id] = theme.id
    return result, len(result) - len(existing)


def export(themes):
    if not themes:
        raise invalid("请先新建或导入标签。")
    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}CodeBook", {"origin": "StatForge"})
    codes = ET.SubElement(root, f"{{{NS}}}Codes")

    def append(parent, parent_id):
        for theme in themes:
            if theme.parent_id != parent_id:
                continue
            attrs = {
                "guid": str(theme.id),
                "name": theme.name,
                "isCodable": "true" if theme.is_codable else "false",
            }
            if theme.color_rgb or theme.is_codable:
                attrs["color"] = theme.color_rgb or PALETTE[theme.color]
            code = ET.SubElement(parent, f"{{{NS}}}Code", attrs)
            if theme.memo:
                ET.SubElement(code, f"{{{NS}}}Description").text = theme.memo
            append(code, theme.id)

    append(codes, None)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
