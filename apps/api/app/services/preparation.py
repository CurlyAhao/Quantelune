"""Immutable preparation copies, with full-source previews and recorded recipes."""

import hashlib
import io
import json
import math
import re
import tempfile
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import anyio
import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.dataset import Dataset
from app.schemas.preparation import Preparation
from app.services import dataset_service, storage_service
from app.services.profiling_service import load_dataframe

UNITS = {
    "s": ("time", 1),
    "ms": ("time", 0.001),
    "min": ("time", 60),
    "h": ("time", 3600),
    "g": ("mass", 1),
    "kg": ("mass", 1000),
    "mg": ("mass", 0.001),
    "m": ("length", 1),
    "cm": ("length", 0.01),
    "mm": ("length", 0.001),
}
LABELS = {
    "missing": "处理缺失",
    "units": "统一单位",
    "dates": "统一日期",
    "join": "左关联",
    "rename": "字段重命名",
    "cast": "转换类型",
    "trim": "清除首尾空格",
    "deduplicate": "去除重复行",
}


def rejected(message, code="PREPARATION_INVALID", status=422):
    return StatForgeError(status, code, message)


def cell(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def rows(frame):
    return [{name: cell(value) for name, value in row.items()} for row in frame.head(20).to_dicts()]


def load(dataset: Dataset) -> pl.DataFrame:
    with tempfile.TemporaryDirectory(prefix="sf-prepare-") as directory:
        path = Path(directory) / f"source.{dataset.file_format}"
        storage_service.download_to_path(dataset.object_key, str(path))
        if hashlib.sha256(path.read_bytes()).hexdigest() != dataset.sha256:
            raise rejected("源文件已变化，请重新导入。", "PREPARATION_SOURCE_CHANGED", 409)
        return load_dataframe(
            str(path), dataset.file_format, dataset.sheet_name, dataset.csv_encoding
        )


def transform(frame: pl.DataFrame, body: Preparation, right: pl.DataFrame | None):
    column = body.column
    if column not in frame.columns and body.operation != "deduplicate":
        raise rejected("请选择当前表中的字段。")
    notes = []
    join_summary = None
    if body.operation == "missing":
        series = frame[column]
        if body.rule == "keep":
            result = frame.clone()
        elif body.rule == "drop":
            result = frame.drop_nulls(column)
        else:
            if body.rule in {"median", "mean"}:
                if not series.dtype.is_numeric():
                    raise rejected("中位数和均值填补需要数值字段。")
                observed = series.drop_nulls().cast(pl.Float64)
                if not observed.len() or not observed.is_finite().all():
                    raise rejected("没有可用于填补的有限数值。")
                value = observed.median() if body.rule == "median" else observed.mean()
                notes.append(
                    f"{
                        ('中位数')
                        if body.rule
                        == (
                            'medi\
an'
                        )
                        else ('均值')
                    } = {value:g}；填补可能低估变异。"
                )
                result = frame.with_columns(pl.col(column).cast(pl.Float64).fill_null(value))
            else:
                value = pl.Series([body.value]).cast(series.dtype, strict=True).item()
                if isinstance(value, float) and not math.isfinite(value):
                    raise rejected("填充值必须是有限数值。")
                result = frame.with_columns(pl.col(column).fill_null(value))
    elif body.operation == "units":
        if body.target not in UNITS:
            raise rejected("请选择目标单位。")
        family, scale = UNITS[body.target]
        converted = []
        for value in frame[column].to_list():
            if value is None:
                converted.append(None)
                continue
            match = re.fullmatch(
                r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]+)\s*", str(value)
            )
            if match is None or match[2] not in UNITS or UNITS[match[2]][0] != family:
                raise rejected("字段含有无法识别或不兼容的单位，请先修正这些值。")
            number = float(
                Decimal(match[1]) * Decimal(str(UNITS[match[2]][1])) / Decimal(str(scale))
            )
            if not math.isfinite(number):
                raise rejected("单位换算产生了非有限数值。")
            converted.append(number)
        result = frame.with_columns(pl.Series(column, converted, dtype=pl.Float64))
        notes.append(f"目标单位：{body.target}")
    elif body.operation == "dates":
        converted = []
        date_formats = {
            "YYYY-MM-DD": "%Y-%m-%d",
            "YYYY/MM/DD": "%Y/%m/%d",
            "DD/MM/YYYY": "%d/%m/%Y",
            "MM/DD/YYYY": "%m/%d/%Y",
            "YYYYMMDD": "%Y%m%d",
        }
        formats = (
            [date_formats.get(body.value, body.value)]
            if body.value
            else ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"]
        )
        for value in frame[column].to_list():
            if value is None:
                converted.append(None)
                continue
            if isinstance(value, (date, datetime)):
                converted.append(value.date() if isinstance(value, datetime) else value)
                continue
            parsed = None
            for fmt in formats:
                try:
                    parsed = datetime.strptime(str(value).strip(), fmt).date()
                    break
                except ValueError:
                    pass
            if parsed is None:
                raise rejected("有日期无法识别，请指定与原数据一致的日期格式。")
            converted.append(parsed)
        result = frame.with_columns(pl.Series(column, converted, dtype=pl.Date))
    elif body.operation == "join":
        if right is None or body.right_key not in right.columns:
            raise rejected("请选择关联表和键字段。")
        if frame[column].dtype != right[body.right_key].dtype:
            raise rejected("两侧关联键类型不同，请先转换为一致类型。")
        candidates = right.filter(pl.col(body.right_key).is_not_null())
        duplicates = candidates.group_by(body.right_key).len().filter(pl.col("len") > 1)
        matched = frame[column].is_in(candidates[body.right_key].implode()).sum()
        examples = candidates.with_row_index("__right_row__", offset=1).filter(
            pl.col(body.right_key).is_in(duplicates[body.right_key].implode())
        )
        join_summary = {
            "left_rows": frame.height,
            "right_rows": right.height,
            "matched": matched,
            "unmatched": frame.height - matched,
            "duplicate_keys": duplicates.height,
            "duplicate_examples": rows(examples),
        }
        unmatched = frame.filter(
            ~pl.col(column).is_in(candidates[body.right_key].implode()).fill_null(False)
        )
        join_summary["unmatched_examples"] = (
            unmatched[column].unique(maintain_order=True).head(10).to_list()
        )
        if duplicates.height and body.duplicate == "block":
            return None, notes, join_summary
        if duplicates.height:
            candidates = candidates.unique(
                subset=[body.right_key], keep=body.duplicate, maintain_order=True
            )
        result = frame.join(
            candidates,
            left_on=column,
            right_on=body.right_key,
            how="left",
            suffix="_right",
            validate="m:1",
            maintain_order="left",
        )
    elif body.operation == "rename":
        if not body.target.strip() or (body.target != column and body.target in frame.columns):
            raise rejected("请输入未被使用的字段名。")
        result = frame.rename({column: body.target.strip()})
    elif body.operation == "cast":
        types = {
            "text": pl.String,
            "integer": pl.Int64,
            "number": pl.Float64,
            "boolean": pl.Boolean,
        }
        if body.target not in types:
            raise rejected("请选择字段类型。")
        if body.target == "integer" and frame[column].dtype.is_float():
            values = frame[column].drop_nulls()
            if (values != values.floor()).any():
                raise rejected("字段含有小数，转换为整数会丢失精度。")
        result = frame.with_columns(pl.col(column).cast(types[body.target], strict=True))
    elif body.operation == "trim":
        if frame[column].dtype != pl.String:
            raise rejected("清除空格需要文本字段。")
        result = frame.with_columns(pl.col(column).str.strip_chars())
    else:
        result = frame.unique(
            subset=[column] if column else None, keep="first", maintain_order=True
        )
    if result.height == 0:
        raise rejected("这一步会移除所有数据，请调整规则。")
    return result, notes, join_summary


def compute(dataset, body, right):
    before = load(dataset)
    try:
        result, notes, join_summary = transform(before, body, load(right) if right else None)
    except StatForgeError:
        raise
    except (pl.exceptions.PolarsError, ValueError, TypeError, OverflowError):
        raise rejected("这项规则不能完整转换当前数据，请检查字段和值。") from None
    if result is None:
        return {"blocked": True, "join": join_summary, "changes": [], "notes": notes}, None
    changes = []
    affected = before.height - result.height
    # Position remains stable for all non-removal operations and the m:1 left join.
    if (
        not (body.operation == "missing" and body.rule == "drop")
        and body.operation != "deduplicate"
    ):
        affected = 0
        for index, (old, new) in enumerate(
            zip(before.iter_rows(named=True), result.iter_rows(named=True))
        ):
            changed = False
            for name, value in new.items():
                source = (
                    body.column
                    if body.operation == "rename" and name == body.target.strip()
                    else name
                )
                previous = old.get(source)
                if (
                    source not in old
                    or cell(previous) != cell(value)
                    or (body.operation == "rename" and source != name)
                ):
                    changed = True
                    if len(changes) < 20:
                        changes.append(
                            {
                                "row": index + 1,
                                "column": name,
                                "before": cell(previous),
                                "after": cell(value),
                            }
                        )
            affected += changed
    content = io.BytesIO()
    result.write_parquet(content)
    output = content.getvalue()
    schema_changes = [
        {
            "column": name,
            "before": str(
                before.schema.get(
                    body.column
                    if body.operation == "rename" and name == body.target.strip()
                    else name,
                    "—",
                )
            ),
            "after": str(dtype),
        }
        for name, dtype in result.schema.items()
        if dtype != before.schema.get(name)
    ]
    return {
        "blocked": False,
        "_unchanged": body.operation != "join"
        and not affected
        and before.schema == result.schema
        and before.equals(result),
        "label": LABELS[body.operation],
        "before_rows": before.height,
        "after_rows": result.height,
        "after_columns": result.width,
        "affected_rows": affected,
        "removed_rows": before.height - result.height,
        "changes": changes,
        "schema_changes": schema_changes,
        "rows": rows(result),
        "columns": result.columns,
        "result_sha256": hashlib.sha256(output).hexdigest(),
        "notes": notes,
        "join": join_summary,
    }, output


async def prepare(db: AsyncSession, user_id, dataset_id, body: Preparation, *, apply=False):
    dataset = await dataset_service.get_dataset_with_access(db, dataset_id, user_id)
    await dataset_service._verify_project_access(db, dataset.project_id, user_id)
    if dataset.upload_status != "ready":
        raise rejected("数据尚未就绪，请重新导入。")
    if body.source_sha256 != dataset.sha256:
        raise rejected("当前数据版本已变化，请重新预览。", "PREPARATION_SOURCE_CHANGED", 409)
    right = None
    if body.operation == "join":
        if not body.right_dataset_id or not body.right_sha256:
            raise rejected("请选择关联表。")
        right = await dataset_service.get_dataset_with_access(db, body.right_dataset_id, user_id)
        if right.project_id != dataset.project_id or right.sha256 != body.right_sha256:
            raise rejected("关联表已变化，请重新选择。", "PREPARATION_SOURCE_CHANGED", 409)
    result, content = await anyio.to_thread.run_sync(compute, dataset, body, right)
    unchanged = result.pop("_unchanged", False)
    if not apply:
        return result
    if result["blocked"] or not body.result_sha256 or body.result_sha256 != result["result_sha256"]:
        raise rejected("请先预览并确认当前规则。", "PREPARATION_PREVIEW_CHANGED", 409)
    if unchanged:
        root_id = json.loads(dataset.preview_json or "{}").get("preparation", {}).get("root_id")
        root = await db.get(Dataset, uuid.UUID(root_id)) if root_id else dataset
        if root and root.project_id == dataset.project_id and root.prepared_source_sha256:
            return dataset
    recipe = body.model_dump(mode="json", exclude={"result_sha256"})
    candidates = await db.execute(
        select(Dataset.id, Dataset.preview_json).where(
            Dataset.project_id == dataset.project_id,
            Dataset.sha256 == result["result_sha256"],
            Dataset.upload_status == "ready",
        )
    )
    for candidate in candidates:
        prior = json.loads(candidate.preview_json or "{}").get("preparation", {})
        prior_steps = prior.get("steps", [])
        if (
            prior.get("parent_id") == str(dataset.id)
            and prior_steps
            and prior_steps[-1].get("recipe") == recipe
        ):
            return await db.get(Dataset, candidate.id)
    old_metadata = json.loads(dataset.preview_json or "{}")
    history = old_metadata.get("preparation", {}).get("steps", [])
    stem = re.sub(r"_ready(?:_\d+)?$", "", Path(dataset.original_filename).stem)
    filename = f"{stem}_ready_{len(history) + 1}.parquet"
    with tempfile.TemporaryDirectory(prefix="sf-prepared-") as directory:
        path = Path(directory) / "prepared.parquet"
        path.write_bytes(content)
        saved = await dataset_service.upload_dataset(
            db, dataset.project_id, user_id, str(path), filename, allow_duplicate=True
        )
    try:
        metadata = json.loads(saved.preview_json)
        metadata["preparation"] = {
            "parent_id": str(dataset.id),
            "root_id": old_metadata.get("preparation", {}).get("root_id", str(dataset.id)),
            "steps": [
                *history,
                {
                    "label": result["label"],
                    "recipe": recipe,
                    "affected_rows": result["affected_rows"],
                    "removed_rows": result["removed_rows"],
                    "output_sha256": saved.sha256,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            ],
        }
        if body.operation == "join":
            metadata["preparation"]["steps"][-1]["join_counts"] = {
                key: result["join"][key]
                for key in ("left_rows", "right_rows", "matched", "unmatched", "duplicate_keys")
            }
            for link in ("media_result", "source_dataset_id", "visual_result", "coding_summary"):
                if link in old_metadata:
                    metadata[link] = old_metadata[link]
            # A right-hand summary can be traced only when the explicit sample key
            # survives the join. Never infer identity from names or row position.
            if (
                right
                and body.column == body.right_key == "sample_id"
                and "sample_id" in result["columns"]
            ):
                right_metadata = json.loads(right.preview_json or "{}")
                if (
                    "coding_summary" not in metadata
                    and "sample_collection_id" in result["columns"]
                    and right_metadata.get("coding_summary")
                ):
                    metadata["coding_summary"] = right_metadata["coding_summary"]
                elif not any(
                    key in metadata for key in ("media_result", "visual_result", "coding_summary")
                ) and right_metadata.get("media_result"):
                    metadata["media_result"] = right_metadata["media_result"]
                    metadata["source_dataset_id"] = right_metadata.get("source_dataset_id")
            metadata["display_name"] = (
                dataset.display_name or dataset.original_filename
            ) + " · 关联表"
        saved.preview_json = json.dumps(metadata, ensure_ascii=False)
        await db.flush()
        await db.refresh(saved)
        return saved
    except Exception:
        storage_service.delete_object(saved.object_key)
        raise
