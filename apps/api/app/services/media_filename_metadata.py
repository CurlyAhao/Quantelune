"""Conservative, exact-filename metadata lookup for local media."""

import csv
import hashlib
import io

from sqlalchemy import select

from app.models.dataset import Dataset, UploadStatus
from app.services import storage_service


def filename(value):
    return value.replace("\\", "/").rsplit("/", 1)[-1].casefold()


async def filename_metadata(db, project_id, root):
    rows = (
        await db.scalars(
            select(Dataset).where(
                Dataset.project_id == project_id,
                Dataset.upload_status.in_([UploadStatus.READY, UploadStatus.UPLOADED]),
                Dataset.file_format == "csv",
            )
        )
    ).all()
    matches = {}
    resolved_root = storage_service.filesystem_path(root.resolve())
    for row in rows:
        # Prepared copies of the same metadata table are analysis versions, not
        # additional source tables. Keep the original local file as the label source.
        if not row.local_file:
            continue
        try:
            path = storage_service.get_local_object_path(row.object_key).resolve()
            if not path.is_relative_to(resolved_root) or path.stat().st_size > 10_000_000:
                continue
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != row.sha256:
                continue
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text, newline=""))
            keys = [
                k
                for k in (reader.fieldnames or [])
                if k and (k.casefold() == "filename" or k.casefold().endswith("_filename"))
            ]
            if len(keys) != 1:
                continue
            key = keys[0]
            for item in reader:
                value = item.get(key)
                if not value or None in item:
                    continue
                identity = filename(value)
                matches.setdefault(identity, []).append(
                    {"source": row.original_filename, "key": key, "values": item}
                )
        except (OSError, UnicodeError, csv.Error, ValueError, RuntimeError):
            continue
    return {
        key: {**values[0], "label": short_title(values[0], key)}
        for key, values in matches.items()
        if len(values) == 1
    }


def short_title(entry, fallback):
    values = entry["values"]
    labels = {
        "actor_id": "演员",
        "participant": "受试者",
        "subject_id": "受试者",
        "speaker": "朗读者",
        "reader": "朗读者",
        "emotion": "情绪",
        "group": "组",
        "statement_id": "台词",
        "repetition": "重复",
    }
    keys = [k for k in labels if values.get(k)] or [
        k
        for k in values
        if k != entry["key"]
        and k.casefold() not in {"transcript", "text", "description"}
        and values[k]
    ]

    def display(k, value):
        emotions = {
            "neutral": "中性",
            "calm": "平静",
            "happy": "快乐",
            "sad": "悲伤",
            "angry": "愤怒",
            "fearful": "恐惧",
            "disgust": "厌恶",
            "surprised": "惊讶",
        }
        if k == "emotion":
            return emotions.get(str(value).lower(), str(value))
        if k in {"actor_id", "statement_id", "repetition"}:
            number = str(value).strip()
            if number.isdigit():
                number = number.zfill(2) if k == "actor_id" else str(int(number))
            return f"第{number}次" if k == "repetition" else labels[k] + number
        return f"{labels.get(k, k)} {value}"

    return " · ".join(display(k, values[k]) for k in keys[:4]) or fallback
