"""Read record-oriented JSON for the workspace preview and analysis table."""

import hashlib
import json
import math
from pathlib import Path

import polars as pl

from app.exceptions import FileValidationError


def _invalid_number(token):
    raise FileValidationError("JSON_PARSE_ERROR", f"{token} 不是有效的 JSON 数值。")


def _unique_fields(pairs):
    record = {}
    for name, value in pairs:
        if name in record:
            raise FileValidationError("JSON_DUPLICATE_KEY", f"同一对象内出现了重复字段：{name}")
        record[name] = value
    return record


def _column(name, values):
    kinds = {type(v) for v in values if v is not None}
    if kinds == {int}:
        numbers = [v for v in values if v is not None]
        lo, hi = min(numbers), max(numbers)
        if -(2**63) <= lo and hi < 2**63:
            return pl.Series(name, values, dtype=pl.Int64)
        if lo >= 0 and hi < 2**64:
            return pl.Series(name, values, dtype=pl.UInt64)
    elif kinds <= {int, float} and kinds:
        if any(type(v) is float and not math.isfinite(v) for v in values):
            raise FileValidationError("JSON_NUMBER_RANGE", f"字段 {name} 包含无法表示的数值。")
        if all(type(v) is not int or abs(v) <= 2**53 for v in values):
            return pl.Series(name, values, dtype=pl.Float64)
    elif kinds <= {str} or kinds == {bool}:
        return pl.Series(name, values)
    # A Parquet column has one type. Retain nested and mixed cells as JSON
    # literals, including quotes on mixed string values, without exploding rows.
    return pl.Series(
        name,
        [
            None if v is None else json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            for v in values
        ],
        dtype=pl.String,
    )


def read_table(path: str | Path, expected_sha: str) -> pl.DataFrame:
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise FileValidationError("SOURCE_CHANGED", "原文件已变化，请刷新工作区或重新导入。")
    try:
        records = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_fields,
            parse_constant=_invalid_number,
        )
    except UnicodeDecodeError as exc:
        raise FileValidationError("JSON_ENCODING", "请将 JSON 文件保存为 UTF-8 编码。") from exc
    except json.JSONDecodeError as exc:
        raise FileValidationError(
            "JSON_PARSE_ERROR", f"JSON 第 {exc.lineno} 行、第 {exc.colno} 列有语法错误。"
        ) from exc
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise FileValidationError(
            "JSON_RECORDS_EXPECTED", "表格读取需要由对象组成的 JSON 数组，每个对象对应一行。"
        )
    fields = dict.fromkeys(k for row in records for k in row)
    if not fields:
        raise FileValidationError("JSON_NO_FIELDS", "JSON 中没有可读取的记录字段。")
    return pl.DataFrame([_column(k, [row.get(k) for row in records]) for k in fields])


def display_value(value):
    # WebView uses JavaScript numbers. Return the full decimal representation
    # for display while keeping the integer column in the saved Parquet file.
    if type(value) is int and abs(value) >= 2**53:
        return str(value)
    return value
