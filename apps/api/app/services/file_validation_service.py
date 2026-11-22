"""File validation and schema inference for CSV, Excel, and Parquet files."""

import json
import logging
import re
from pathlib import Path
from typing import Any

import polars as pl

from app.config import settings
from app.exceptions import FileValidationError, UnsupportedFileTypeError
from app.services.csv_reader import CsvDelimiterError, CsvEncodingError, read_csv_dataset

logger = logging.getLogger("statforge.validation")

# File signatures (magic bytes)
_PARQUET_MAGIC = b"PAR1"
_ZIP_MAGIC = b"PK\x03\x04"  # xlsx is a ZIP
_CSV_SAMPLE_SIZE = 64 * 1024  # 64 KiB for initial inspection

MIME_MAP = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/octet-stream",
}

_ILLEGAL_COL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ValidationResult:
    """Structured result of file validation and schema inference."""

    def __init__(
        self,
        file_format: str,
        row_count: int,
        column_count: int,
        columns: list[dict[str, Any]],
        preview_rows: list[dict[str, Any]],
        warnings: list[str],
        sheet_names: list[str] | None = None,
        csv_encoding: str | None = None,
    ) -> None:
        self.file_format = file_format
        self.row_count = row_count
        self.column_count = column_count
        self.columns = columns
        self.preview_rows = preview_rows
        self.warnings = warnings
        self.sheet_names = sheet_names
        self.csv_encoding = csv_encoding

    def schema_json(self) -> str:
        return json.dumps(self.columns, ensure_ascii=False, default=str)

    def preview_json(self) -> str:
        return json.dumps(
            {"rows": self.preview_rows, "total_rows": self.row_count},
            ensure_ascii=False,
            default=str,
        )


def _check_file_basics(file_path: str, extension: str) -> None:
    """Validate file size, extension, and basic content."""
    path = Path(file_path)
    file_size = path.stat().st_size

    if file_size == 0:
        raise FileValidationError("FILE_EMPTY", "The uploaded file is empty.")

    if settings.max_upload_size_bytes is not None and file_size > settings.max_upload_size_bytes:
        from app.exceptions import FileTooLargeError

        raise FileTooLargeError(settings.max_upload_size_mb)

    ext = extension.lower().lstrip(".")
    if ext not in settings.allowed_extensions:
        raise UnsupportedFileTypeError(
            f"File type '.{ext}' is not supported. "
            f"Allowed: {', '.join('.' + e for e in settings.allowed_extensions)}"
        )


def _validate_content_signature(file_path: str, extension: str) -> None:
    """Cross-check file extension against actual content bytes."""
    ext = extension.lower().lstrip(".")
    with open(file_path, "rb") as f:
        header = f.read(8)

    if ext == "parquet":
        if not header.startswith(_PARQUET_MAGIC):
            # Also check footer
            with open(file_path, "rb") as f:
                f.seek(-4, 2)
                footer = f.read(4)
            if footer != _PARQUET_MAGIC:
                raise FileValidationError(
                    "FILE_SIGNATURE_MISMATCH",
                    "File has a .parquet extension but is not a valid Parquet file.",
                )
    elif ext == "xlsx":
        if not header.startswith(_ZIP_MAGIC):
            raise FileValidationError(
                "FILE_SIGNATURE_MISMATCH",
                "File has an .xlsx extension but is not a valid Excel file.",
            )
    elif ext == "csv":
        # CSV: check for binary content (null bytes in first chunk)
        with open(file_path, "rb") as f:
            sample = f.read(_CSV_SAMPLE_SIZE)
        if b"\x00" in sample:
            raise FileValidationError(
                "FILE_NOT_TEXT",
                "File has a .csv extension but contains binary data.",
            )


def _validate_column_names(columns: list[str]) -> list[str]:
    """Validate column names and return warnings."""
    warnings: list[str] = []
    seen: dict[str, int] = {}

    for i, col in enumerate(columns):
        if not col or col.strip() == "":
            raise FileValidationError(
                "COLUMN_EMPTY_NAME",
                f"Column {i + 1} has an empty name.",
            )
        if len(col) > settings.max_column_name_length:
            raise FileValidationError(
                "COLUMN_NAME_TOO_LONG",
                f"Column '{col[:50]}...' exceeds {settings.max_column_name_length} characters.",
            )
        if _ILLEGAL_COL_CHARS.search(col):
            raise FileValidationError(
                "COLUMN_ILLEGAL_CHARS",
                f"Column '{col[:50]}' contains illegal control characters.",
            )
        lower = col.lower().strip()
        if lower in seen:
            seen[lower] += 1
            if seen[lower] == 2:
                warnings.append(f"Duplicate column name detected: '{col}'")
        else:
            seen[lower] = 1

    if len(columns) > settings.max_columns:
        raise FileValidationError(
            "TOO_MANY_COLUMNS",
            f"File has {len(columns)} columns (max {settings.max_columns}).",
        )

    return warnings


def _infer_polars_type(dtype: pl.DataType) -> dict[str, Any]:
    """Map a Polars dtype to our schema type system."""
    type_map = {
        pl.Int8: "integer",
        pl.Int16: "integer",
        pl.Int32: "integer",
        pl.Int64: "integer",
        pl.UInt8: "integer",
        pl.UInt16: "integer",
        pl.UInt32: "integer",
        pl.UInt64: "integer",
        pl.Float32: "float",
        pl.Float64: "float",
        pl.Boolean: "boolean",
        pl.Date: "date",
        pl.Datetime: "datetime",
        pl.String: "string",
        pl.Utf8: "string",
        pl.Categorical: "categorical",
    }
    base_type = type(dtype)
    inferred = type_map.get(base_type, "unknown")

    # Check if string column might be categorical
    if inferred == "string":
        inferred = "string"  # Will refine with unique ratio later

    return {
        "inferred_type": inferred,
        "polars_type": str(dtype),
        "nullable": True,  # Polars columns are nullable by default
    }


def _build_column_info(df: pl.DataFrame, n_rows: int) -> list[dict[str, Any]]:
    """Build column metadata from a Polars DataFrame sample."""
    columns = []
    for col_name in df.columns:
        series = df[col_name]
        type_info = _infer_polars_type(series.dtype)
        null_count = series.null_count()
        n_unique = series.n_unique()

        # Sample values (first 5 non-null)
        non_null = series.drop_nulls()
        sample_vals = non_null.head(5).to_list()
        sample_vals = [str(v) if v is not None else None for v in sample_vals]

        # Refine: high-cardinality string → categorical candidate
        inferred = type_info["inferred_type"]
        if inferred == "string" and n_rows > 0:
            unique_ratio = n_unique / max(n_rows, 1)
            if unique_ratio < 0.05 and n_unique <= 50:
                inferred = "categorical"

        columns.append(
            {
                "name": col_name,
                "inferred_type": inferred,
                "polars_type": type_info["polars_type"],
                "nullable": type_info["nullable"],
                "null_count": null_count,
                "null_rate": round(null_count / max(n_rows, 1), 4),
                "unique_count": n_unique,
                "sample_values": sample_vals,
            }
        )
    return columns


def _df_to_preview_rows(df: pl.DataFrame, max_rows: int) -> list[dict[str, Any]]:
    """Convert a Polars DataFrame to a list of dicts for JSON preview."""
    rows = []
    for row in df.head(max_rows).iter_rows(named=True):
        clean: dict[str, Any] = {}
        for k, v in row.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, (int, float, bool)):
                clean[k] = v
            else:
                clean[k] = str(v)
        rows.append(clean)
    return rows


def validate_csv(
    file_path: str, preview_rows: int = 20, csv_encoding: str | None = "auto"
) -> ValidationResult:
    """Validate and parse a CSV file."""
    warnings: list[str] = []

    # Keep upload preview and all downstream reads on the same CSV parser.
    try:
        parsed = read_csv_dataset(file_path, csv_encoding)
    except CsvEncodingError as exc:
        raise FileValidationError("FILE_CSV_ENCODING_REQUIRED", str(exc)) from exc
    except CsvDelimiterError as exc:
        raise FileValidationError("FILE_CSV_DELIMITER_AMBIGUOUS", str(exc)) from exc
    except Exception as exc:
        raise FileValidationError(
            "FILE_PARSE_ERROR",
            f"Failed to parse CSV: {type(exc).__name__}",
        )
    df = parsed.dataframe
    if parsed.encoding == "latin-1":
        warnings.append("File is not UTF-8 encoded; interpreted as Latin-1.")

    if df.width == 0:
        raise FileValidationError("FILE_NO_COLUMNS", "CSV file has no columns.")

    col_warnings = _validate_column_names(df.columns)
    warnings.extend(col_warnings)

    n_rows = df.height
    if n_rows == 0:
        warnings.append("CSV file has headers but no data rows.")

    columns = _build_column_info(df, n_rows)
    preview = _df_to_preview_rows(df, min(preview_rows, settings.preview_max_rows))

    return ValidationResult(
        file_format="csv",
        row_count=n_rows,
        column_count=df.width,
        columns=columns,
        preview_rows=preview,
        warnings=warnings,
        csv_encoding=parsed.encoding,
    )


def excel_sheet_names(file_path: str) -> list[str]:
    _check_file_basics(file_path, "xlsx")
    _validate_content_signature(file_path, "xlsx")
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        try:
            names = wb.sheetnames
            if not names:
                raise FileValidationError("FILE_NO_SHEETS", "Excel file has no worksheets.")
            return names
        finally:
            wb.close()
    except FileValidationError:
        raise
    except Exception as exc:
        raise FileValidationError("FILE_PARSE_ERROR", "Cannot open this Excel workbook.") from exc


def validate_excel(
    file_path: str,
    preview_rows: int = 20,
    sheet_name: str | None = None,
) -> ValidationResult:
    """Validate and parse an Excel (.xlsx) file."""
    warnings: list[str] = []

    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except Exception as exc:
        raise FileValidationError(
            "FILE_PARSE_ERROR",
            f"Failed to open Excel file: {type(exc).__name__}",
        )

    sheet_names = wb.sheetnames
    if not sheet_names:
        wb.close()
        raise FileValidationError("FILE_NO_SHEETS", "Excel file has no worksheets.")

    if len(sheet_names) > 1:
        warnings.append(
            f"Workbook has {len(sheet_names)} sheets: {', '.join(sheet_names)}. "
            f"Using '{sheet_name or sheet_names[0]}'."
        )

    target_sheet = sheet_name or sheet_names[0]
    if target_sheet not in sheet_names:
        wb.close()
        raise FileValidationError(
            "FILE_SHEET_NOT_FOUND",
            f"Sheet '{target_sheet}' not found. Available: {', '.join(sheet_names)}",
        )

    # Check for formulas in non-data_only mode
    try:
        wb_check = openpyxl.load_workbook(file_path, read_only=True, data_only=False)
        ws_check = wb_check[target_sheet]
        formula_count = 0
        for i, row in enumerate(ws_check.iter_rows(max_row=100)):
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formula_count += 1
            if formula_count > 0:
                break
        wb_check.close()
        if formula_count > 0:
            warnings.append("Sheet contains formula cells; values shown as computed results.")
    except Exception:
        pass

    wb.close()

    # Parse with Polars (openpyxl engine — calamine requires fastexcel)
    try:
        df = pl.read_excel(
            file_path,
            sheet_name=target_sheet,
            engine="openpyxl",
            infer_schema_length=1000,
        )
    except Exception as exc:
        raise FileValidationError(
            "FILE_PARSE_ERROR",
            f"Failed to parse Excel sheet: {type(exc).__name__}",
        )

    if df.width == 0:
        raise FileValidationError("FILE_NO_COLUMNS", "Excel sheet has no columns.")

    col_warnings = _validate_column_names(df.columns)
    warnings.extend(col_warnings)

    n_rows = df.height
    columns = _build_column_info(df, n_rows)
    preview = _df_to_preview_rows(df, min(preview_rows, settings.preview_max_rows))

    return ValidationResult(
        file_format="xlsx",
        row_count=n_rows,
        column_count=df.width,
        columns=columns,
        preview_rows=preview,
        warnings=warnings,
        sheet_names=sheet_names,
    )


def validate_parquet(file_path: str, preview_rows: int = 20) -> ValidationResult:
    """Validate and parse a Parquet file."""
    warnings: list[str] = []

    try:
        # Read metadata first without loading all data
        lf = pl.scan_parquet(file_path)
        schema = lf.collect_schema()
        col_names = schema.names()

        # Get row count from metadata
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(file_path)
        n_rows = pf.metadata.num_rows
    except Exception as exc:
        raise FileValidationError(
            "FILE_PARSE_ERROR",
            f"Failed to read Parquet metadata: {type(exc).__name__}",
        )

    if len(col_names) == 0:
        raise FileValidationError("FILE_NO_COLUMNS", "Parquet file has no columns.")

    col_warnings = _validate_column_names(col_names)
    warnings.extend(col_warnings)

    # Read only preview rows
    try:
        df = pl.read_parquet(file_path, n_rows=min(preview_rows + 100, n_rows or 1000))
    except Exception as exc:
        raise FileValidationError(
            "FILE_PARSE_ERROR",
            f"Failed to read Parquet data: {type(exc).__name__}",
        )

    columns = _build_column_info(df, n_rows or df.height)
    preview = _df_to_preview_rows(df, min(preview_rows, settings.preview_max_rows))

    return ValidationResult(
        file_format="parquet",
        row_count=n_rows or df.height,
        column_count=len(col_names),
        columns=columns,
        preview_rows=preview,
        warnings=warnings,
    )


def validate_file(
    file_path: str,
    extension: str,
    preview_rows: int = 20,
    sheet_name: str | None = None,
    csv_encoding: str | None = "auto",
) -> ValidationResult:
    """Validate an uploaded file based on its extension.

    Performs: basic checks → signature verification → format-specific parsing.
    """
    _check_file_basics(file_path, extension)
    _validate_content_signature(file_path, extension)

    ext = extension.lower().lstrip(".")
    clamped_rows = min(max(preview_rows, 1), settings.preview_max_rows)

    if ext == "csv":
        return validate_csv(file_path, clamped_rows, csv_encoding)
    elif ext == "xlsx":
        return validate_excel(file_path, clamped_rows, sheet_name)
    elif ext == "parquet":
        return validate_parquet(file_path, clamped_rows)
    else:
        raise UnsupportedFileTypeError(f"Unsupported file type: .{ext}")
