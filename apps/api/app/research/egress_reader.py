"""Fixed data-only projection inside the existing OS-bounded worker."""

import csv
import hashlib
import io
import json
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.ml.resources import MLResourceError

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_WIRE_BYTES = 128000
MAX_CELL_BYTES = 4096


def fail(code: str) -> MLResourceError:
    return MLResourceError("EGRESS_" + code, "Registered source projection unavailable.")


def _configuration(config: dict[str, Any]) -> tuple[list[str], int, str, dict[str, str]]:
    if not isinstance(config, dict) or set(config) != {"columns", "rows", "scope", "column_types"}:
        raise fail("CONFIGURATION")
    columns, rows, scope, types = (
        config[key] for key in ("columns", "rows", "scope", "column_types")
    )
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= 10
        or any(not isinstance(item, str) or not 1 <= len(item) <= 255 for item in columns)
        or len(set(columns)) != len(columns)
    ):
        raise fail("COLUMNS")
    if (
        not isinstance(scope, str)
        or scope not in {"sanitized_sample", "raw_data_approved"}
        or type(rows) is not int
        or not 1 <= rows <= (5 if scope == "sanitized_sample" else 10)
    ):
        raise fail("ROWS")
    if (
        not isinstance(types, dict)
        or set(types) != set(columns)
        or any(not isinstance(value, str) for value in types.values())
    ):
        raise fail("COLUMN_TYPES")
    if scope == "sanitized_sample" and any(
        value not in {"numeric", "boolean"} for value in types.values()
    ):
        raise fail("SANITIZED_TYPE")
    return columns, rows, scope, types


def _scalar(value: Any, scope: str, kind: str) -> str | float | int | bool | None:
    if value is None:
        return None
    if scope == "sanitized_sample":
        if kind == "boolean":
            if type(value) is bool:
                return value
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return value.lower() == "true"
            raise fail("SANITIZED_TYPE")
        if isinstance(value, str):
            if value == "":
                return None
            try:
                value = int(value) if value.lstrip("+-").isdigit() else float(value)
            except (ValueError, OverflowError):
                raise fail("SANITIZED_TYPE") from None
        if type(value) not in {int, float} or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise fail("SANITIZED_TYPE")
    elif isinstance(value, (date, datetime)):
        value = value.isoformat()
    elif isinstance(value, Decimal):
        value = str(value)
    if type(value) not in {str, int, float, bool}:
        raise fail("CELL_TYPE")
    if isinstance(value, str) and len(value.encode("utf-8")) > MAX_CELL_BYTES:
        raise fail("CELL_LIMIT")
    if isinstance(value, float) and not math.isfinite(value):
        raise fail("CELL_TYPE")
    return value


def _csv(
    path: Path,
    columns: list[str],
    count: int,
    scope: str,
    types: dict[str, str],
    csv_encoding: str | None = None,
) -> list[dict[str, Any]]:
    from app.services.csv_reader import CSV_ENCODINGS, _encoding, _separator

    encoding = csv_encoding if csv_encoding is not None else _encoding(str(path))
    if encoding not in CSV_ENCODINGS:
        raise fail("ENCODING")
    with path.open("rb") as stream:
        sample = stream.read(64 * 1024)
        truncated = bool(stream.read(1))
    if encoding == "utf-8":
        import codecs

        text = codecs.getincrementaldecoder("utf-8-sig")("strict").decode(sample)
    else:
        import codecs

        text = codecs.getincrementaldecoder(encoding)("strict").decode(sample, final=not truncated)
    separator = _separator(text, truncated)
    result = []
    with path.open("rb") as binary:
        with io.TextIOWrapper(
            binary, encoding="utf-8-sig" if encoding == "utf-8" else encoding, newline=""
        ) as stream:
            reader = csv.reader(stream, delimiter=separator, strict=True)
            header = next(reader, [])
            if (
                not header
                or len(header) > 1000
                or len(set(header)) != len(header)
                or any(not 1 <= len(item) <= 255 for item in header)
                or not set(columns).issubset(header)
            ):
                raise fail("COLUMNS")
            positions = {name: header.index(name) for name in columns}
            for row in reader:
                if not row:
                    continue
                if len(row) != len(header):
                    raise fail("RAGGED_INPUT")
                result.append(
                    {
                        name: _scalar(row[index], scope, types[name])
                        for name, index in positions.items()
                    }
                )
                if len(result) == count:
                    break
    return result


def _parquet(
    path: Path, columns: list[str], count: int, scope: str, types: dict[str, str]
) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq
    from pyarrow import types as arrow_types

    file = pq.ParquetFile(path)
    names = file.schema_arrow.names
    if len(set(names)) != len(names) or not set(columns).issubset(names):
        raise fail("COLUMNS")
    for name in columns:
        kind = file.schema_arrow.field(name).type
        # JSON numeric samples cannot preserve arbitrary Decimal precision.
        # Keep exact Decimal strings only in the explicitly approved raw scope.
        if scope == "sanitized_sample" and arrow_types.is_decimal(kind):
            raise fail("SANITIZED_TYPE")
        if not any(
            check(kind)
            for check in (
                arrow_types.is_null,
                arrow_types.is_boolean,
                arrow_types.is_integer,
                arrow_types.is_floating,
                arrow_types.is_decimal,
                arrow_types.is_date,
                arrow_types.is_timestamp,
                arrow_types.is_string,
                arrow_types.is_large_string,
            )
        ):
            raise fail("COLUMN_TYPE")
    needed = count
    for group_index in range(file.metadata.num_row_groups):
        group = file.metadata.row_group(group_index)
        selected = [
            group.column(index)
            for index in range(group.num_columns)
            if group.column(index).path_in_schema in columns
        ]
        if sum(column.total_uncompressed_size for column in selected) > 64 * 1024 * 1024:
            raise fail("PARQUET_DECODE_LIMIT")
        needed -= group.num_rows
        if needed <= 0:
            break
    result = []
    # Decode only the approved columns, one row at a time, under the OS Job.
    for batch in file.iter_batches(batch_size=1, columns=columns, use_threads=False):
        for row in batch.to_pylist():
            result.append({name: _scalar(row[name], scope, types[name]) for name in columns})
            if len(result) == count:
                return result
    return result


def project(
    path: Path,
    file_format: str,
    config: dict[str, Any],
    expected_sha256: str,
    csv_encoding: str | None = None,
) -> dict[str, Any]:
    if (
        file_format not in {"csv", "parquet"}
        or not path.is_file()
        or not 0 < path.stat().st_size <= MAX_SOURCE_BYTES
    ):
        raise fail("SOURCE_LIMIT")
    columns, count, scope, types = _configuration(config)
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
            raise fail("INPUT_CHANGED")
    try:
        rows = (
            _csv(path, columns, count, scope, types, csv_encoding)
            if file_format == "csv"
            else _parquet(path, columns, count, scope, types)
        )
    except MLResourceError:
        raise
    except Exception:
        raise fail("PARSE_FAILED") from None
    if not rows:
        raise fail("EMPTY_SOURCE")
    response = {
        "version": 1,
        "status": "ok",
        "projection": {
            "sha256": expected_sha256,
            "columns": columns,
            "rows": rows,
            "scope": scope,
            "sample_policy": "explicit_raw_rows"
            if scope == "raw_data_approved"
            else "numeric_boolean_sample_not_anonymous",
        },
    }
    if (
        len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        > MAX_WIRE_BYTES - 1024
    ):
        raise fail("OUTPUT_LIMIT")
    return response


def verify_projection(
    response: dict[str, Any], config: dict[str, Any], expected_sha256: str
) -> None:
    """Validate the child data channel without reading a source in the parent."""
    columns, count, scope, types = _configuration(config)
    projection = response.get("projection")
    if (
        response.get("version") != 1
        or response.get("status") != "ok"
        or not isinstance(projection, dict)
        or set(projection) != {"sha256", "columns", "rows", "scope", "sample_policy"}
        or projection["sha256"] != expected_sha256
        or projection["columns"] != columns
        or projection["scope"] != scope
        or projection["sample_policy"]
        != (
            "explicit_raw_rows"
            if scope == "raw_data_approved"
            else "numeric_boolean_sample_not_anonymous"
        )
    ):
        raise fail("CHANNEL_INVALID")
    rows = projection["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= count:
        raise fail("CHANNEL_INVALID")
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise fail("CHANNEL_INVALID")
        for name in columns:
            scalar = _scalar(row[name], scope, types[name])
            if type(scalar) is not type(row[name]) or scalar != row[name]:
                raise fail("CHANNEL_INVALID")
