"""Data profiling engine: column statistics, missingness, correlations, quality.

All data processing uses Polars (with NumPy/SciPy for statistical helpers).
`compute_profile` returns a plain JSON-serializable dict that can be stored
verbatim in `dataset_profiles.result_json`.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import polars as pl

from app.services.csv_reader import read_csv_dataset

logger = logging.getLogger("statforge.profiling")

# Versioning (persisted alongside every profile)
ALGORITHM_VERSION = "1.0.0"
PROFILING_VERSION = 1

# Profiling limits
MAX_PROFILE_ROWS = 100_000
MAX_TOP_VALUES = 20
MAX_HISTOGRAM_BINS = 30
MAX_CORRELATION_COLUMNS = 20
MAX_BOXPLOT_OUTLIERS = 100
MAX_SAMPLE_VALUES = 5

# Quality / alert thresholds
HIGH_MISSING_RATE = 0.5
IDENTIFIER_UNIQUE_RATE = 0.95
CATEGORICAL_UNIQUE_RATE = 0.05
CATEGORICAL_MAX_UNIQUE = 50
NEAR_CONSTANT_UNIQUE_RATE = 0.01
HIGH_OUTLIER_RATE = 0.10
DUPLICATE_ALERT_RATE = 0.05
MANY_COLUMNS_THRESHOLD = 100
LONG_COLUMN_NAME_LENGTH = 100
IQR_OUTLIER_FACTOR = 1.5


# ── Helpers ──────────────────────────────────────────────────────────────────


def _num(value: Any) -> float | None:
    """Convert to a JSON-safe float: NaN/Inf become None."""
    if value is None:
        return None
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


# ── File loading ─────────────────────────────────────────────────────────────


def load_dataframe(
    file_path: str, file_format: str, sheet_name: str | None = None, csv_encoding: str | None = None
) -> pl.DataFrame:
    """Load a dataset file into a Polars DataFrame (CSV, XLSX, Parquet)."""
    fmt = file_format.lower().lstrip(".")
    if fmt == "csv":
        return read_csv_dataset(file_path, csv_encoding).dataframe
    if fmt == "xlsx":
        return pl.read_excel(
            file_path,
            sheet_name=sheet_name,
            engine="openpyxl",
            infer_schema_length=1000,
        )
    if fmt == "parquet":
        return pl.read_parquet(file_path)
    raise ValueError(f"Unsupported file format for profiling: {fmt}")


# ── Type inference ───────────────────────────────────────────────────────────


def _base_type(dtype: pl.DataType) -> str:
    """Map a Polars dtype to a coarse base type."""
    if dtype.is_integer():
        return "integer"
    if dtype.is_float():
        return "float"
    if dtype == pl.Boolean:
        return "boolean"
    if dtype == pl.Date:
        return "date"
    if dtype == pl.Datetime:
        return "datetime"
    if dtype == pl.String:
        return "string"
    if dtype == pl.Categorical:
        return "categorical"
    return "unknown"


def _refine_inferred_type(
    base: str,
    name: str,
    n_rows: int,
    null_rate: float,
    unique_count: int,
    unique_rate: float,
) -> tuple[str, float]:
    """Apply semantic refinement rules on top of the physical base type.

    Returns (inferred_type, confidence).
    """
    if n_rows == 0 or null_rate == 1.0:
        return "empty", 1.0
    if unique_count <= 1:
        return "constant", 1.0
    if (
        base == "string"
        and unique_rate < CATEGORICAL_UNIQUE_RATE
        and unique_count <= CATEGORICAL_MAX_UNIQUE
    ):
        return "categorical", 0.9
    if unique_rate > IDENTIFIER_UNIQUE_RATE and "id" in name.lower():
        return "identifier", 0.85
    if unique_rate >= 1.0:
        return "identifier", 0.8
    if base == "unknown":
        return "unknown", 0.5
    return base, 0.95


# ── Numeric statistics ───────────────────────────────────────────────────────


def _histogram(values: np.ndarray) -> dict[str, Any]:
    if values.size == 0:
        return {"bins": [], "counts": []}
    n_bins = min(MAX_HISTOGRAM_BINS, max(1, int(np.unique(values).size)))
    counts, edges = np.histogram(values, bins=n_bins)
    return {
        "bins": [float(e) for e in edges],
        "counts": [int(c) for c in counts],
    }


def _numeric_stats(finite: pl.Series) -> dict[str, Any]:
    """Compute numeric statistics from a null-free, NaN/Inf-free Float64 series."""
    from scipy.stats import kurtosis as scipy_kurtosis
    from scipy.stats import skew as scipy_skew

    values = finite.to_numpy().astype(np.float64)
    n = values.size

    q1, median, q3 = (float(np.percentile(values, p, method="linear")) for p in (25.0, 50.0, 75.0))
    iqr = q3 - q1
    lower_fence = q1 - IQR_OUTLIER_FACTOR * iqr
    upper_fence = q3 + IQR_OUTLIER_FACTOR * iqr

    outlier_mask = (values < lower_fence) | (values > upper_fence)
    outlier_values = np.sort(values[outlier_mask])

    inliers = values[~outlier_mask]
    whisker_min = float(inliers.min()) if inliers.size else float(values.min())
    whisker_max = float(inliers.max()) if inliers.size else float(values.max())

    is_constant = bool(np.all(values == values[0]))
    if is_constant:
        skewness: float | None = 0.0
        kurt: float | None = None
    else:
        skewness = _num(scipy_skew(values, bias=False)) if n >= 3 else None
        kurt = _num(scipy_kurtosis(values, bias=False)) if n >= 4 else None

    p01, p05, p25, p50, p75, p95, p99 = (
        float(np.percentile(values, p, method="linear")) for p in (1, 5, 25, 50, 75, 95, 99)
    )

    return {
        "mean": _num(values.mean()),
        "median": _num(median),
        "std": _num(values.std(ddof=1)) if n >= 2 else None,
        "variance": _num(values.var(ddof=1)) if n >= 2 else None,
        "min": _num(values.min()),
        "max": _num(values.max()),
        "range": _num(values.max() - values.min()),
        "q1": _num(q1),
        "q3": _num(q3),
        "iqr": _num(iqr),
        "p01": _num(p01),
        "p05": _num(p05),
        "p25": _num(p25),
        "p50": _num(p50),
        "p75": _num(p75),
        "p95": _num(p95),
        "p99": _num(p99),
        "skewness": _round(skewness),
        "kurtosis": _round(kurt),
        "zero_count": int((values == 0).sum()),
        "negative_count": int((values < 0).sum()),
        "outlier_count": int(outlier_mask.sum()),
        "outlier_rate": _round(_rate(int(outlier_mask.sum()), n)),
        "histogram": _histogram(values),
        "boxplot": {
            "min": _num(whisker_min),
            "q1": _num(q1),
            "median": _num(median),
            "q3": _num(q3),
            "max": _num(whisker_max),
            "outliers": [_num(v) for v in outlier_values[:MAX_BOXPLOT_OUTLIERS]],
        },
    }


# ── Categorical / string statistics ──────────────────────────────────────────


def _categorical_stats(series: pl.Series) -> dict[str, Any]:
    """Top-value frequencies and string length statistics."""
    s = series.drop_nulls().cast(pl.String)
    total = s.len()
    if total == 0:
        return {
            "top_values": [],
            "avg_length": None,
            "min_length": None,
            "max_length": None,
            "empty_string_count": 0,
        }

    value_counts = s.value_counts(sort=True)
    value_col = value_counts.columns[0]
    top = value_counts.head(MAX_TOP_VALUES)
    top_values = [
        {
            "value": str(row[value_col]),
            "count": int(row["count"]),
            "proportion": _round(_rate(int(row["count"]), total)),
        }
        for row in top.iter_rows(named=True)
    ]
    if value_counts.height > MAX_TOP_VALUES:
        rest_count = int(value_counts["count"].slice(MAX_TOP_VALUES).sum())
        top_values.append(
            {
                "value": "__other__",
                "count": rest_count,
                "proportion": _round(_rate(rest_count, total)),
            }
        )

    lengths = s.str.len_chars()
    return {
        "top_values": top_values,
        "avg_length": _round(float(lengths.mean()), 4),
        "min_length": int(lengths.min()),
        "max_length": int(lengths.max()),
        "empty_string_count": int((s == "").sum()),
    }


# ── Datetime statistics ──────────────────────────────────────────────────────


def _datetime_stats(series: pl.Series) -> dict[str, Any]:
    s = series.drop_nulls()
    if s.len() == 0:
        return {
            "earliest": None,
            "latest": None,
            "span_days": None,
            "invalid_count": 0,
            "duplicate_count": 0,
            "is_monotonic": False,
        }

    earliest = s.min()
    latest = s.max()
    span_days = (latest - earliest).total_seconds() / 86_400.0
    is_monotonic = bool(s.is_sorted() or s.is_sorted(descending=True))
    duplicate_count = s.len() - s.n_unique()

    return {
        "earliest": earliest.isoformat(),
        "latest": latest.isoformat(),
        "span_days": _round(span_days),
        "invalid_count": 0,
        "duplicate_count": int(duplicate_count),
        "is_monotonic": is_monotonic,
    }


# ── Per-column profiling ─────────────────────────────────────────────────────


def _sample_values(series: pl.Series) -> list[str]:
    non_null = series.drop_nulls().head(MAX_SAMPLE_VALUES)
    return [str(v) for v in non_null.to_list()]


def _profile_column(name: str, series: pl.Series, n_rows: int) -> dict[str, Any]:
    """Compute the full profile for a single column.

    The returned dict contains a private ``_meta`` key with internal flags
    used for quality scoring/alerts; it is stripped before the result leaves
    the engine.
    """
    dtype = series.dtype
    base = _base_type(dtype)

    null_count = int(series.null_count())
    count = n_rows - null_count
    null_rate = _rate(null_count, n_rows)

    non_null = series.drop_nulls()
    unique_count = int(non_null.n_unique())
    unique_rate = _rate(unique_count, count)

    inferred_type, confidence = _refine_inferred_type(
        base, name, n_rows, null_rate, unique_count, unique_rate
    )

    warnings: list[str] = []
    non_finite_count = 0

    numeric_stats = None
    if base in ("integer", "float"):
        as_float = non_null.cast(pl.Float64)
        nan_count = int(as_float.is_nan().sum())
        inf_count = int(as_float.is_infinite().sum())
        non_finite_count = nan_count + inf_count
        if non_finite_count:
            warnings.append(
                f"Column contains {non_finite_count} non-finite values (NaN/Inf); "
                "excluded from statistics"
            )
        finite = as_float.filter(as_float.is_finite())
        if finite.len() > 0:
            numeric_stats = _numeric_stats(finite)
        else:
            warnings.append("Column has no finite numeric values")

    categorical_stats = None
    if base in ("string", "categorical"):
        categorical_stats = _categorical_stats(series)

    datetime_stats = None
    if base in ("date", "datetime"):
        datetime_stats = _datetime_stats(series)

    if n_rows > 0 and null_rate == 1.0:
        warnings.append("Column is entirely null")
    elif null_rate > HIGH_MISSING_RATE:
        warnings.append(f"Column is more than {HIGH_MISSING_RATE:.0%} missing")
    if inferred_type == "constant":
        warnings.append("Column has a single distinct value (constant)")
    if inferred_type == "identifier":
        warnings.append("Column appears to be an identifier")
    if base == "string" and inferred_type == "string" and unique_count > CATEGORICAL_MAX_UNIQUE:
        warnings.append("High-cardinality string column (possibly free text)")

    return {
        "name": name,
        "physical_type": str(dtype),
        "inferred_type": inferred_type,
        "inference_confidence": confidence,
        "nullable": null_count > 0,
        "count": count,
        "null_count": null_count,
        "null_rate": _round(null_rate),
        "unique_count": unique_count,
        "unique_rate": _round(unique_rate),
        "sample_values": _sample_values(series),
        "numeric_stats": numeric_stats,
        "categorical_stats": categorical_stats,
        "datetime_stats": datetime_stats,
        "warnings": warnings,
        "_meta": {
            "base": base,
            "non_finite_count": non_finite_count,
            "outlier_rate": (numeric_stats or {}).get("outlier_rate") or 0.0,
        },
    }


# ── Missingness ──────────────────────────────────────────────────────────────


def _missingness(df: pl.DataFrame, column_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    n_rows = df.height
    per_column = [
        {"name": c["name"], "null_count": c["null_count"], "null_rate": c["null_rate"]}
        for c in column_profiles
    ]
    all_null_columns = [
        c["name"] for c in column_profiles if n_rows > 0 and c["null_count"] == n_rows
    ]
    high_missing_columns = [
        c["name"] for c in column_profiles if c["null_rate"] > HIGH_MISSING_RATE
    ]

    if n_rows == 0:
        complete_rows = 0
        row_distribution: list[dict[str, int]] = []
    else:
        nulls_per_row = df.select(
            pl.sum_horizontal(pl.all().is_null().cast(pl.UInt32)).alias("nulls")
        ).to_series()
        complete_rows = int((nulls_per_row == 0).sum())
        buckets = nulls_per_row.value_counts()
        row_distribution = sorted(
            (
                {"missing_count": int(row["nulls"]), "row_count": int(row["count"])}
                for row in buckets.iter_rows(named=True)
            ),
            key=lambda item: item["missing_count"],
        )

    return {
        "per_column": per_column,
        "complete_rows": complete_rows,
        "complete_row_rate": _round(_rate(complete_rows, n_rows)),
        "all_null_columns": all_null_columns,
        "high_missing_columns": high_missing_columns,
        "row_missing_distribution": row_distribution,
    }


# ── Correlations ─────────────────────────────────────────────────────────────


def _pairwise_matrix(matrix: np.ndarray) -> list[list[float | None]]:
    """Pearson correlation with pairwise-complete observations. NaN = missing."""
    n_cols = matrix.shape[1]
    result: list[list[float | None]] = []
    for i in range(n_cols):
        row: list[float | None] = []
        for j in range(n_cols):
            if i == j:
                finite = matrix[:, i][~np.isnan(matrix[:, i])]
                row.append(1.0 if np.unique(finite).size > 1 else None)
                continue
            a, b = matrix[:, i], matrix[:, j]
            mask = ~(np.isnan(a) | np.isnan(b))
            if mask.sum() < 2:
                row.append(None)
                continue
            x, y = a[mask], b[mask]
            if np.unique(x).size < 2 or np.unique(y).size < 2:
                row.append(None)
                continue
            row.append(_num(np.corrcoef(x, y)[0, 1]))
        result.append(row)
    return result


def _correlations(df: pl.DataFrame, column_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    from scipy.stats import rankdata

    numeric_names = [
        c["name"] for c in column_profiles if c["_meta"]["base"] in ("integer", "float")
    ]

    note: str | None = None
    if len(numeric_names) > MAX_CORRELATION_COLUMNS:
        note = f"Limited to first {MAX_CORRELATION_COLUMNS} numeric columns"
        numeric_names = numeric_names[:MAX_CORRELATION_COLUMNS]

    empty_section = {"columns": numeric_names, "matrix": []}
    if df.height == 0 or not numeric_names:
        if not note:
            note = "No numeric columns with data for correlation"
        return {"pearson": empty_section, "spearman": empty_section, "note": note}

    matrix = df.select(pl.col(numeric_names).cast(pl.Float64)).to_numpy().astype(np.float64)
    # null → NaN (via cast), and treat Inf as missing too
    matrix = np.where(np.isfinite(matrix), matrix, np.nan)

    if len(numeric_names) == 1:
        col = matrix[:, 0]
        finite = col[~np.isnan(col)]
        diag = 1.0 if np.unique(finite).size > 1 else None
        section = {"columns": numeric_names, "matrix": [[diag]]}
        return {"pearson": section, "spearman": section, "note": note}

    # Spearman = Pearson on ranks (per-column ranks over non-missing values)
    ranks = np.full(matrix.shape, np.nan, dtype=np.float64)
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        mask = ~np.isnan(col)
        if mask.sum() >= 1:
            ranks[mask, j] = rankdata(col[mask], method="average")

    return {
        "pearson": {"columns": numeric_names, "matrix": _pairwise_matrix(matrix)},
        "spearman": {"columns": numeric_names, "matrix": _pairwise_matrix(ranks)},
        "note": note,
    }


# ── Quality scoring and alerts ───────────────────────────────────────────────


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _quality(
    overview: dict[str, Any],
    column_profiles: list[dict[str, Any]],
    missingness: dict[str, Any],
) -> dict[str, Any]:
    n_cols = overview["column_count"]
    all_null = missingness["all_null_columns"]
    high_missing = missingness["high_missing_columns"]

    # Completeness: 100 * (1 - overall null rate), penalties for missing columns
    completeness_issues = [f"Column '{name}' is entirely null" for name in all_null]
    completeness_issues += [f"Column '{name}' is more than 50% missing" for name in high_missing]
    completeness_score = 100.0 * overview["completeness"]
    completeness_score -= 10.0 * len(all_null)
    completeness_score -= 5.0 * len(high_missing)

    # Validity: penalties for non-finite values, invalid dates, mixed/unknown types
    validity_issues: list[str] = []
    validity_score = 100.0
    for c in column_profiles:
        if c["_meta"]["non_finite_count"] > 0:
            validity_score -= 5.0
            validity_issues.append(
                f"Column '{c['name']}' contains {c['_meta']['non_finite_count']} non-finite values"
            )
        dt_stats = c["datetime_stats"]
        if dt_stats and dt_stats["invalid_count"] > 0:
            validity_score -= 5.0
            validity_issues.append(f"Column '{c['name']}' has invalid date/datetime values")
        if c["inferred_type"] == "unknown":
            validity_score -= 3.0
            validity_issues.append(f"Column '{c['name']}' has an unrecognized type")

    # Uniqueness: 100 * (1 - duplicate rate), penalty for constant columns
    constant_columns = [c["name"] for c in column_profiles if c["inferred_type"] == "constant"]
    uniqueness_issues = [f"Column '{name}' is constant" for name in constant_columns]
    uniqueness_score = 100.0 * (1.0 - overview["duplicate_row_rate"])
    uniqueness_score -= 5.0 * len(constant_columns)

    # Consistency: high-cardinality strings that might be categorical
    consistency_issues: list[str] = []
    consistency_score = 100.0
    for c in column_profiles:
        if (
            c["_meta"]["base"] == "string"
            and c["inferred_type"] == "string"
            and c["unique_rate"] < 0.5
            and c["unique_count"] > CATEGORICAL_MAX_UNIQUE
        ):
            consistency_score -= 3.0
            consistency_issues.append(
                f"Column '{c['name']}' may be categorical (many repeated values)"
            )

    # Usability: column count, empty names, very long names
    usability_issues: list[str] = []
    usability_score = 100.0
    if n_cols > MANY_COLUMNS_THRESHOLD:
        usability_score -= 10.0
        usability_issues.append(f"Dataset has {n_cols} columns (>{MANY_COLUMNS_THRESHOLD})")
    for c in column_profiles:
        if not c["name"].strip():
            usability_score -= 10.0
            usability_issues.append("Dataset has an empty column name")
        elif len(c["name"]) > LONG_COLUMN_NAME_LENGTH:
            usability_score -= 3.0
            usability_issues.append(f"Column '{c['name'][:30]}...' has a very long name")

    dimensions = {
        "completeness": {
            "score": _round(_clamp(completeness_score), 2),
            "weight": 0.30,
            "issues": completeness_issues,
        },
        "validity": {
            "score": _round(_clamp(validity_score), 2),
            "weight": 0.20,
            "issues": validity_issues,
        },
        "uniqueness": {
            "score": _round(_clamp(uniqueness_score), 2),
            "weight": 0.20,
            "issues": uniqueness_issues,
        },
        "consistency": {
            "score": _round(_clamp(consistency_score), 2),
            "weight": 0.15,
            "issues": consistency_issues,
        },
        "usability": {
            "score": _round(_clamp(usability_score), 2),
            "weight": 0.15,
            "issues": usability_issues,
        },
    }
    total = sum(dim["score"] * dim["weight"] for dim in dimensions.values())

    return {
        "total_score": _round(_clamp(total), 2),
        "dimensions": dimensions,
        "alerts": _alerts(overview, column_profiles),
    }


def _alerts(
    overview: dict[str, Any], column_profiles: list[dict[str, Any]]
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []

    def add(severity: str, category: str, message: str) -> None:
        alerts.append({"severity": severity, "category": category, "message": message})

    if overview["row_count"] == 0:
        add("critical", "structure", "Dataset contains no rows")
        return alerts
    if overview["column_count"] > MANY_COLUMNS_THRESHOLD:
        add(
            "warning",
            "structure",
            f"Dataset has {overview['column_count']} columns (>{MANY_COLUMNS_THRESHOLD})",
        )
    if overview["duplicate_row_rate"] > DUPLICATE_ALERT_RATE:
        add(
            "warning",
            "uniqueness",
            f"{overview['duplicate_row_rate']:.1%} of rows are duplicates",
        )

    for c in column_profiles:
        name = c["name"]
        if c["inferred_type"] == "empty":
            add("critical", "completeness", f"Column '{name}' is entirely null")
        elif c["null_rate"] > HIGH_MISSING_RATE:
            add(
                "warning",
                "completeness",
                f"Column '{name}' is more than {HIGH_MISSING_RATE:.0%} missing "
                f"({c['null_rate']:.1%})",
            )
        if c["inferred_type"] == "constant" and overview["row_count"] > 0:
            add("info", "uniqueness", f"Column '{name}' is constant (single distinct value)")
        elif (
            c["count"] > 0
            and c["unique_rate"] < NEAR_CONSTANT_UNIQUE_RATE
            and c["inferred_type"] not in ("empty", "constant")
        ):
            add("info", "uniqueness", f"Column '{name}' is near-constant")
        if c["_meta"]["non_finite_count"] > 0:
            add(
                "warning",
                "validity",
                f"Column '{name}' contains {c['_meta']['non_finite_count']} non-finite values",
            )
        if c["_meta"]["outlier_rate"] > HIGH_OUTLIER_RATE:
            add(
                "warning",
                "distribution",
                f"Column '{name}' has a high outlier rate ({c['_meta']['outlier_rate']:.1%})",
            )

    return alerts


# ── Main entry point ─────────────────────────────────────────────────────────


def compute_profile(df: pl.DataFrame, dataset_sha256: str) -> dict[str, Any]:
    """Compute the full data profile for a DataFrame.

    Returns a JSON-serializable dict with overview, per-column statistics,
    missingness, correlations, and quality scoring. Datasets larger than
    ``MAX_PROFILE_ROWS`` are sampled deterministically.
    """
    total_rows = df.height
    sampled = False
    sample_size: int | None = None
    if total_rows > MAX_PROFILE_ROWS:
        df = df.sample(n=MAX_PROFILE_ROWS, seed=42)
        sampled = True
        sample_size = df.height
        logger.info(
            "Profiling sampled %d of %d rows (sha256=%s)", sample_size, total_rows, dataset_sha256
        )

    n_rows = df.height
    column_profiles = [_profile_column(name, df[name], n_rows) for name in df.columns]

    type_counts: dict[str, int] = {}
    for c in column_profiles:
        base = c["_meta"]["base"]
        if base in ("integer", "float"):
            group = "numeric"
        elif base in ("date", "datetime"):
            group = "datetime"
        elif base == "boolean":
            group = "boolean"
        elif c["inferred_type"] == "categorical":
            group = "categorical"
        elif c["inferred_type"] == "string":
            group = "string"
        else:
            group = "other"
        type_counts[group] = type_counts.get(group, 0) + 1

    total_cells = n_rows * df.width
    total_nulls = sum(c["null_count"] for c in column_profiles)
    completeness = 1.0 - _rate(total_nulls, total_cells) if total_cells else 1.0
    duplicate_row_count = n_rows - df.unique().height if n_rows else 0

    missingness = _missingness(df, column_profiles)

    overview = {
        "row_count": n_rows,
        "column_count": df.width,
        "numeric_columns": type_counts.get("numeric", 0),
        "categorical_columns": type_counts.get("categorical", 0),
        "datetime_columns": type_counts.get("datetime", 0),
        "boolean_columns": type_counts.get("boolean", 0),
        "string_columns": type_counts.get("string", 0),
        "other_columns": type_counts.get("other", 0),
        "total_nulls": total_nulls,
        "completeness": _round(completeness),
        "duplicate_row_count": duplicate_row_count,
        "duplicate_row_rate": _round(_rate(duplicate_row_count, n_rows)),
        "complete_row_count": missingness["complete_rows"],
        "complete_row_rate": missingness["complete_row_rate"],
        "sampled": sampled,
        "sample_size": sample_size,
    }

    correlations = _correlations(df, column_profiles)
    quality = _quality(overview, column_profiles, missingness)

    for c in column_profiles:
        c.pop("_meta", None)

    return {
        "overview": overview,
        "columns": column_profiles,
        "missingness": missingness,
        "correlations": correlations,
        "quality": quality,
    }
