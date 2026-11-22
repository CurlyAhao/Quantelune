"""Read and rebuild REFI-QDA project exchange packages without extracting assets."""

import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.archive_staging import ExpandedBudget

MAX_XML_BYTES = 64 * 1024**2
MAX_NOTE_BYTES = 8 * 1024**2
MAX_NOTES_BYTES = 64 * 1024**2

NS = "urn:QDA-XML:project:1.0"
ET.register_namespace("", NS)
KINDS = {
    "TextSource": "PlainTextSelection",
    "PictureSource": "PictureSelection",
    "PDFSource": "PDFSelection",
    "AudioSource": "AudioSelection",
    "VideoSource": "VideoSelection",
}
COORDS = {
    "PlainTextSelection": ("startPosition", "endPosition"),
    "PictureSelection": ("firstX", "firstY", "secondX", "secondY"),
    "PDFSelection": ("page", "firstX", "firstY", "secondX", "secondY"),
    "AudioSelection": ("begin", "end"),
    "VideoSelection": ("begin", "end"),
}


def q(name):
    return f"{{{NS}}}{name}"


def guid(value):
    try:
        return str(UUID(value.strip("{}")))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"Invalid GUID: {value!r}") from None


def _safe_member(name):
    p = PurePosixPath(name)
    return (
        bool(name)
        and not name.startswith("/")
        and "\\" not in name
        and ":" not in name
        and all(x not in ("", ".", "..") for x in name.rstrip("/").split("/"))
        and not p.is_absolute()
    )


def _description(node):
    return node.findtext(q("Description"), default="")


def _coords_valid(kind, values):
    if any(v < 0 for v in values.values()):
        return False
    if kind == "PlainTextSelection":
        return values["startPosition"] < values["endPosition"]
    if kind in ("AudioSelection", "VideoSelection"):
        return values["begin"] < values["end"]
    return values["firstX"] < values["secondX"] and values["firstY"] < values["secondY"]


def read_package(path: Path, budget: ExpandedBudget | None = None) -> dict:
    budget = budget or ExpandedBudget()
    issues = []
    notes_size = 0
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(not _safe_member(n) for n in names):
            raise ValueError("Duplicate or unsafe ZIP member")
        if names.count("project.qde") != 1:
            raise ValueError("Package must contain one project.qde")
        with archive.open("project.qde") as stream:
            xml = budget.read(stream, MAX_XML_BYTES)
        if b"<!DOCTYPE" in xml.upper():
            raise ValueError("DTD is not supported in project XML")
        root = ET.fromstring(xml)
        if root.tag != q("Project") or root.get("name") is None:
            raise ValueError("Invalid REFI project root")
        seen = set()

        def identity(node):
            value = guid(node.get("guid"))
            if value in seen:
                raise ValueError(f"Duplicate GUID: {value}")
            seen.add(value)
            return value

        users = [
            {"guid": identity(x), "name": x.get("name", "")}
            for x in root.findall(f"{q('Users')}/{q('User')}")
        ]
        user_ids = {x["guid"] for x in users}
        notes = []
        for x in root.findall(f"{q('Notes')}/{q('Note')}"):
            key, name = identity(x), x.get("name", "")
            content = x.find(q("PlainTextContent"))
            uri = x.get("plainTextPath", "")
            body = None
            if uri.startswith("internal://"):
                member = "sources/" + uri[len("internal://") :]
                if _safe_member(member) and member in names and not member.endswith("/"):
                    try:
                        with archive.open(member) as stream:
                            raw = budget.read(
                                stream, min(MAX_NOTE_BYTES, MAX_NOTES_BYTES - notes_size)
                            )
                        notes_size += len(raw)
                        body = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        issues.append(f"Note {name} ({key}): embedded member is not UTF-8: {uri}")
                else:
                    issues.append(f"Note {name} ({key}): missing or invalid embedded member {uri}")
            elif uri:
                issues.append(f"Note {name} ({key}): external path not read: {uri}")
            elif content is not None:
                body = content.text or ""
            else:
                issues.append(f"Note {name} ({key}): no embedded text")
            note = {
                "guid": key,
                "name": name,
                "body": body,
                "user_guid": x.get("creatingUser"),
                "created_at": x.get("creationDateTime"),
                "modified_user_guid": x.get("modifyingUser"),
                "modified_at": x.get("modifiedDateTime"),
                "description": _description(x),
            }
            for field in ("user_guid", "modified_user_guid"):
                if note[field]:
                    try:
                        note[field] = guid(note[field])
                        if note[field] not in user_ids:
                            issues.append(f"Note {name} ({key}): user not in Users: {note[field]}")
                    except ValueError:
                        issues.append(f"Note {name} ({key}): invalid or missing user {note[field]}")
                        note[field] = None
            notes.append(note)
        note_ids = {x["guid"] for x in notes}
        supported_note_refs = set()

        def note_refs(node, label):
            refs = []
            for ref in node.findall(q("NoteRef")):
                supported_note_refs.add(id(ref))
                try:
                    target = guid(ref.get("targetGUID"))
                    if target not in note_ids:
                        raise ValueError
                    refs.append(target)
                except ValueError:
                    issues.append(
                        f"{label}: invalid or missing note reference {ref.get('targetGUID')}"
                    )
            return refs

        for x, note in zip(root.findall(f"{q('Notes')}/{q('Note')}"), notes):
            note["note_refs"] = note_refs(x, f"Note {note['name']}")
        codes = []

        def visit(node, parent=None):
            for x in node.findall(q("Code")):
                key = identity(x)
                flag = x.get("isCodable")
                if flag not in ("true", "false", "1", "0") or x.get("name") is None:
                    raise ValueError(f"Invalid code {key}")
                codes.append(
                    {
                        "guid": key,
                        "name": x.get("name"),
                        "parent_guid": parent,
                        "is_codable": flag in ("true", "1"),
                        "color": x.get("color"),
                        "description": _description(x),
                    }
                )
                visit(x, key)

        code_root = root.find(f"{q('CodeBook')}/{q('Codes')}")
        if code_root is not None:
            visit(code_root)
        code_ids = {x["guid"] for x in codes}
        sources = []
        source_root = root.find(q("Sources"))
        for x in source_root if source_root is not None else []:
            kind = x.tag.removeprefix(f"{{{NS}}}")
            if kind not in KINDS:
                issues.append(f"Unsupported source {kind}: {x.get('name', '')}")
                continue
            key = identity(x)
            name = x.get("name", "")
            uri = x.get("plainTextPath" if kind == "TextSource" else "path", "")
            member = None
            if uri.startswith("internal://"):
                candidate = "sources/" + uri[len("internal://") :]
                if _safe_member(candidate) and candidate in names and not candidate.endswith("/"):
                    member = candidate
                else:
                    issues.append(
                        f"Source {name} ({key}): missing or invalid embedded member {uri}"
                    )
            elif uri:
                issues.append(f"Source {name} ({key}): external path not read: {uri}")
            elif x.find(q("PlainTextContent")) is None:
                issues.append(f"Source {name} ({key}): no embedded material")
            if kind == "TextSource" and x.get("richTextPath"):
                issues.append(
                    f"Source {name} ({key}): richTextPath not imported: {x.get('richTextPath')}"
                )
            source = {
                "guid": key,
                "name": name,
                "kind": kind,
                "member_path": member,
                "selections": [],
                "note_refs": note_refs(x, f"Source {name}"),
            }
            if x.find(q("PlainTextContent")) is not None:
                source["plain_text_content"] = x.findtext(q("PlainTextContent"), default="")
            for sel in x.findall(q(KINDS[kind])):
                sid = identity(sel)
                try:
                    coordinates = {f: int(sel.attrib[f]) for f in COORDS[KINDS[kind]]}
                    if not _coords_valid(KINDS[kind], coordinates):
                        raise ValueError
                except (KeyError, ValueError):
                    issues.append(f"Source {name}: selection {sid} has invalid coordinates")
                    continue
                selection = {
                    "guid": sid,
                    "kind": KINDS[kind],
                    "name": sel.get("name", ""),
                    "description": _description(sel),
                    "coordinates": coordinates,
                    "codings": [],
                    "created_at": sel.get("creationDateTime"),
                    "user_guid": sel.get("creatingUser"),
                    "note_refs": note_refs(sel, f"Selection {sid}"),
                }
                for coding in sel.findall(q("Coding")):
                    cid = identity(coding)
                    try:
                        code = guid(coding.find(q("CodeRef")).get("targetGUID"))
                        user = guid(coding.get("creatingUser") or sel.get("creatingUser"))
                    except (ValueError, AttributeError):
                        issues.append(f"Selection {sid}: coding {cid} has invalid code or user")
                        continue
                    if code not in code_ids or user not in user_ids:
                        issues.append(
                            f"Selection {sid}: coding {cid} references missing code or user"
                        )
                        continue
                    selection["codings"].append(
                        {
                            "guid": cid,
                            "code_guid": code,
                            "user_guid": user,
                            "created_at": coding.get("creationDateTime"),
                        }
                    )
                source["selections"].append(selection)
            sources.append(source)
        source_ids = {s["guid"] for s in sources}
        cases = []
        for x in root.findall(f"{q('Cases')}/{q('Case')}"):
            key = identity(x)
            refs = []
            bad = 0
            for ref in x.findall(q("SourceRef")):
                try:
                    target = guid(ref.get("targetGUID"))
                except ValueError:
                    target = None
                if target not in source_ids:
                    bad += 1
                else:
                    refs.append(target)
            if bad:
                issues.append(
                    f"Case {x.get('name', '')} ({key}): {bad} unresolved source reference(s)"
                )
            cases.append({"guid": key, "name": x.get("name", ""), "source_guids": refs})
        object_names = {
            n.get("guid"): n.get("name") for n in root.iter() if n.get("guid") and n.get("name")
        }
        for tag in (
            "Variable",
            "VariableValue",
            "NoteRef",
            "SelectionRef",
            "Set",
            "Link",
            "Graph",
            "Transcript",
            "Representation",
            "TextDescription",
            "Coding",
        ):
            nodes = root.findall(f".//{q(tag)}")
            if tag == "NoteRef":
                nodes = [n for n in nodes if id(n) not in supported_note_refs]
            if tag == "Coding":
                nodes = [
                    n
                    for s in (source_root if source_root is not None else [])
                    for n in s.findall(q("Coding"))
                ]
            if nodes:
                labels = []
                for n in nodes:
                    target = n.get("targetGUID") or n.get("guid")
                    if tag == "VariableValue":
                        ref = n.find(q("VariableRef"))
                        target = ref.get("targetGUID") if ref is not None else None
                    label = n.get("name") or object_names.get(target) or "未命名项"
                    if label not in labels:
                        labels.append(label)
                issues.append(f"Unsupported {tag}: {len(nodes)} ({', '.join(labels)})")
        return {
            "name": root.get("name"),
            "metadata": {k: v for k, v in root.attrib.items() if k != "name"},
            "description": _description(root),
            "notes": notes,
            "users": users,
            "codes": codes,
            "sources": sources,
            "cases": cases,
            "issues": issues,
            "original_xml": xml.decode("utf-8-sig"),
        }


def write_package(destination: Path, document: dict, files: dict[str, Path]) -> None:
    """Build a portable package from current objects with structural checks."""
    metadata = document.get("metadata", {})
    allowed_metadata = {
        "origin",
        "creatingUserGUID",
        "creationDateTime",
        "modifyingUserGUID",
        "modifiedDateTime",
        "basePath",
    }
    if set(metadata) - allowed_metadata:
        raise ValueError("Unsupported project metadata")
    root = ET.Element(
        q("Project"),
        {
            **({"origin": "StatForge"} if "metadata" not in document else metadata),
            "name": document["name"],
        },
    )
    claimed = set()

    def claim(value):
        value = guid(value)
        if value in claimed:
            raise ValueError(f"Duplicate GUID: {value}")
        claimed.add(value)
        return value

    user_ids = set()
    if document.get("users"):
        parent = ET.SubElement(root, q("Users"))
        for user in document["users"]:
            key = claim(user["guid"])
            user_ids.add(key)
            ET.SubElement(parent, q("User"), {"guid": key, "name": user["name"]})
    for field in ("creatingUserGUID", "modifyingUserGUID"):
        if field in metadata:
            guid(metadata[field])
    notes = document.get("notes", [])
    note_ids = {guid(x["guid"]) for x in notes}
    if len(note_ids) != len(notes):
        raise ValueError("Duplicate note GUID")

    def emit_note_refs(node, refs):
        for ref in refs:
            target = guid(ref)
            if target not in note_ids:
                raise ValueError(f"Unknown note reference {target}")
            ET.SubElement(node, q("NoteRef"), {"targetGUID": target})

    codes = document.get("codes", [])
    code_ids = {guid(x["guid"]) for x in codes}
    if len(code_ids) != len(codes):
        raise ValueError("Duplicate code GUID")
    if codes:
        parent = ET.SubElement(ET.SubElement(root, q("CodeBook")), q("Codes"))
        by_parent = {}
        for code in codes:
            p = guid(code["parent_guid"]) if code.get("parent_guid") else None
            if p is not None and p not in code_ids:
                raise ValueError(f"Unknown parent code {p}")
            by_parent.setdefault(p, []).append(code)

        def emit_codes(container, p):
            for code in by_parent.get(p, []):
                attrs = {
                    "guid": claim(code["guid"]),
                    "name": code["name"],
                    "isCodable": "true" if code.get("is_codable") else "false",
                }
                if code.get("color"):
                    attrs["color"] = code["color"]
                node = ET.SubElement(container, q("Code"), attrs)
                if code.get("description"):
                    ET.SubElement(node, q("Description")).text = code["description"]
                emit_codes(node, attrs["guid"])

        emit_codes(parent, None)
        if not code_ids.issubset(claimed):
            raise ValueError("Cyclic or unreachable code hierarchy")
    sources = document.get("sources", [])
    source_ids = {guid(x["guid"]) for x in sources}
    if len(source_ids) != len(sources):
        raise ValueError("Duplicate source GUID")
    if document.get("cases"):
        parent = ET.SubElement(root, q("Cases"))
        for case in document["cases"]:
            node = ET.SubElement(
                parent, q("Case"), {"guid": claim(case["guid"]), "name": case["name"]}
            )
            for ref in case.get("source_guids", []):
                target = guid(ref)
                if target not in source_ids:
                    raise ValueError(f"Unknown case source {target}")
                ET.SubElement(node, q("SourceRef"), {"targetGUID": target})
    assets = []
    if sources:
        parent = ET.SubElement(root, q("Sources"))
        for source in sources:
            key = claim(source["guid"])
            kind = source["kind"]
            if kind not in KINDS:
                raise ValueError(f"Unsupported source kind {kind}")
            file = files.get(source["guid"]) or files.get(source.get("member_path"))
            attrs = {"guid": key, "name": source["name"]}
            if file is not None:
                file = Path(file)
                if not file.is_file():
                    raise ValueError(f"Source {key} file is not readable: {file}")
                suffix = file.suffix
                if not suffix or any(c in suffix for c in "/\\:"):
                    raise ValueError(f"Source {key} requires a real file extension")
                member = f"sources/{key}{suffix}"
                attrs["plainTextPath" if kind == "TextSource" else "path"] = (
                    "internal://" + member.removeprefix("sources/")
                )
                assets.append((member, file))
            elif kind != "TextSource" or "plain_text_content" not in source:
                raise ValueError(f"Source {key} has no embedded file")
            node = ET.SubElement(parent, q(kind), attrs)
            if file is None:
                ET.SubElement(node, q("PlainTextContent")).text = source["plain_text_content"]
            for selection in source.get("selections", []):
                skind = selection["kind"]
                if skind != KINDS[kind]:
                    raise ValueError(f"Invalid selection type {skind} for {kind}")
                coords = selection["coordinates"]
                if (
                    set(coords) != set(COORDS[skind])
                    or any(type(v) is not int for v in coords.values())
                    or not _coords_valid(skind, coords)
                ):
                    raise ValueError(f"Invalid coordinates for selection {selection['guid']}")
                values = {"guid": claim(selection["guid"]), "name": selection.get("name", "")}
                if selection.get("created_at"):
                    values["creationDateTime"] = selection["created_at"]
                if selection.get("user_guid") in user_ids:
                    values["creatingUser"] = selection["user_guid"]
                values.update({k: str(v) for k, v in coords.items()})
                sel = ET.SubElement(node, q(skind), values)
                if selection.get("description"):
                    ET.SubElement(sel, q("Description")).text = selection["description"]
                for coding in selection.get("codings", []):
                    code, user = guid(coding["code_guid"]), guid(coding["user_guid"])
                    if code not in code_ids or user not in user_ids:
                        raise ValueError("Coding references unknown code or user")
                    cattrs = {"guid": claim(coding["guid"]), "creatingUser": user}
                    if coding.get("created_at"):
                        cattrs["creationDateTime"] = coding["created_at"]
                    cnode = ET.SubElement(sel, q("Coding"), cattrs)
                    ET.SubElement(cnode, q("CodeRef"), {"targetGUID": code})
                emit_note_refs(sel, selection.get("note_refs", []))
            emit_note_refs(node, source.get("note_refs", []))
    note_assets = []
    if notes:
        parent = ET.SubElement(root, q("Notes"))
        for note in notes:
            key = claim(note["guid"])
            if not isinstance(note.get("body"), str):
                raise ValueError(f"Note {key} has no readable text")
            attrs = {
                "guid": key,
                "name": note.get("name", ""),
                "plainTextPath": f"internal://{key}.txt",
            }
            for field, attr in (
                ("user_guid", "creatingUser"),
                ("modified_user_guid", "modifyingUser"),
            ):
                if note.get(field):
                    author = guid(note[field])
                    attrs[attr] = author
            for field, attr in (
                ("created_at", "creationDateTime"),
                ("modified_at", "modifiedDateTime"),
            ):
                if note.get(field):
                    attrs[attr] = note[field]
            node = ET.SubElement(parent, q("Note"), attrs)
            if note.get("description"):
                ET.SubElement(node, q("Description")).text = note["description"]
            emit_note_refs(node, note.get("note_refs", []))
            note_assets.append((f"sources/{key}.txt", note["body"].encode("utf-8")))
    if document.get("description"):
        ET.SubElement(root, q("Description")).text = document["description"]
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("project.qde", xml)
        for member, file in assets:
            archive.write(file, member)
        for member, body in note_assets:
            archive.writestr(member, body)


def validate_package_xsd(path: Path) -> None:
    """Validate project.qde against the bundled official XSD; requires lxml."""
    try:
        from lxml import etree
    except ImportError as exc:
        raise RuntimeError("Formal XSD validation requires lxml") from exc
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(not _safe_member(n) for n in names):
            raise ValueError("Duplicate or unsafe ZIP member")
        if names.count("project.qde") != 1:
            raise ValueError("Package must contain one project.qde")
        with archive.open("project.qde") as stream:
            xml = ExpandedBudget().read(stream, MAX_XML_BYTES)
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema = etree.XMLSchema(
        etree.parse(str(Path(__file__).parent / "schemas" / "Project.xsd"), parser)
    )
    schema.assertValid(etree.fromstring(xml, parser))
