"""Dataset-aware ML feasibility preflight (ML-2).

Validates that an experiment configuration is *feasible against the actual
training data* before any experiment/run is persisted. Infeasible requests must
raise a stable, actionable 422 — never silently degrade splits, never invent
folds suggestions from truncated/sampled profile summaries, and never start a
run that would later be mis-labeled.

Trusted statistics used for UI folds suggestions are computed from the full
loaded dataframe bound to a dataset SHA. Profile sampling / top-N categorical
summaries are never used to prove class counts or time order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn

import numpy as np
import polars as pl

from app.exceptions import StatForgeError
from app.ml.config import MLConfig
from app.ml.types import MAX_FOLDS, MIN_FOLDS, MLTaskType, SplitStrategy


@dataclass
class TrustedTargetStats:
    """Sufficient statistics for folds guidance and feasibility checks."""

    dataset_sha256: str
    target: str
    task_type: str
    n_rows: int
    n_non_null_target: int
    class_counts: dict[str, int] = field(default_factory=dict)
    n_classes: int | None = None
    min_class_count: int | None = None
    n_groups: int | None = None
    n_time_rows: int | None = None
    time_is_sorted_ascending: bool | None = None
    suggested_n_splits: int | None = None
    trusted: bool = True
    trust_reason: str | None = None


def _fail(code: str, message: str) -> NoReturn:
    raise StatForgeError(422, code, message)


def valid_target_mask(y_series: pl.Series) -> np.ndarray:
    """Rows kept for training: drop null **and** numeric NaN targets.

    Must match executor `_prepare_xy` / pandas `notna()` — Polars `is_not_null()`
    alone keeps float NaN and diverges from the rows the engine actually trains on.
    """
    # pandas notna is the executor's keep mask; reuse it so semantics cannot drift.
    return y_series.to_pandas().notna().to_numpy()


def require_regressable_target(
    y: np.ndarray | pl.Series, target_name: str, *, source_dtype: pl.DataType | None = None
) -> np.ndarray:
    """Regression targets must be a finite numeric column — not Utf8 text.

    Utf8/String values that merely *look* numeric are rejected at preflight so
    the executor never trains on raw strings after a pass-then-fail create.
    Boolean/Date/Datetime are also rejected (not a regression target contract).
    """
    if isinstance(y, pl.Series):
        source_dtype = y.dtype
        values = y.to_numpy()
    else:
        values = y
    if source_dtype is not None and not _is_numeric_dtype(source_dtype):
        _fail(
            "ML_TARGET_NOT_NUMERIC",
            f"Regression target '{target_name}' has non-numeric dtype {source_dtype}; "
            "regression requires a numeric column (int/float), not text or dates. "
            "Numeric strings in Utf8/Parquet are not accepted.",
        )
    # Without an explicit numeric dtype, never coerce text to float (footgun).
    if source_dtype is None:
        arr = np.asarray(values)
        if arr.dtype.kind in {"U", "S", "O"}:
            _fail(
                "ML_TARGET_NOT_NUMERIC",
                f"Regression target '{target_name}' has non-numeric values; "
                "regression requires a numeric column (int/float).",
            )
    try:
        yf = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        _fail(
            "ML_TARGET_NOT_NUMERIC",
            f"Regression target '{target_name}' must be finite numeric values; "
            f"got non-numeric values ({exc}).",
        )
    if yf.size == 0:
        _fail(
            "ML_TARGET_NOT_NUMERIC",
            f"Regression target '{target_name}' has no usable finite values.",
        )
    if not np.all(np.isfinite(yf)):
        _fail(
            "ML_TARGET_NOT_FINITE",
            f"Regression target '{target_name}' contains Inf or other non-finite "
            "values; regression requires finite numeric targets.",
        )
    return yf


def _is_numeric_dtype(dtype: pl.DataType) -> bool:
    """True only for integer/float columns (not bool, utf8, date, datetime)."""
    return bool(dtype.is_integer() or dtype.is_float())


def _require_valid_group_labels(groups: np.ndarray, group_name: str) -> None:
    """Reject null/NaN/unusable group labels before any unique/split.

    Mixed None/str arrays make np.unique raise TypeError (API 500); numeric NaN
    is not counted by Series.null_count(). Both must be stable 422s.
    """
    if groups.size == 0:
        _fail(
            "INVALID_ML_INPUT",
            f"Group variable '{group_name}' has no usable labels after filtering.",
        )
    # Object arrays: None / pd.NA / float('nan') are all invalid.
    # Float arrays: NaN is invalid.
    for i, g in enumerate(groups.tolist()):
        if g is None:
            _fail(
                "INVALID_ML_INPUT",
                f"Group variable '{group_name}' contains null labels; "
                "Group K-Fold requires complete group labels.",
            )
        if isinstance(g, float) and g != g:  # NaN
            _fail(
                "INVALID_ML_INPUT",
                f"Group variable '{group_name}' contains NaN labels; "
                "Group K-Fold requires complete group labels.",
            )
        if g != g:  # NaN-like for numpy scalars
            _fail(
                "INVALID_ML_INPUT",
                f"Group variable '{group_name}' contains invalid labels; "
                "Group K-Fold requires complete group labels.",
            )


def is_classification(config: MLConfig) -> bool:
    return config.task_type != MLTaskType.REGRESSION


def suggest_n_splits(
    *,
    task_type: MLTaskType,
    n_rows: int,
    min_class_count: int | None,
    n_groups: int | None,
    split_strategy: SplitStrategy,
    n_classes: int | None = None,
) -> int | None:
    """Derive a folds suggestion only from sufficient full-data statistics."""
    if n_rows < MIN_FOLDS:
        return None
    cap = MAX_FOLDS
    if task_type != MLTaskType.REGRESSION:
        # Never invent folds guidance for infeasible classification targets.
        if min_class_count is None or min_class_count < MIN_FOLDS:
            return None
        if n_classes is not None and n_classes < 2:
            return None
        cap = min(cap, min_class_count)
    if split_strategy == SplitStrategy.GROUP_KFOLD:
        if n_groups is None or n_groups < MIN_FOLDS:
            return None
        cap = min(cap, n_groups)
    if split_strategy == SplitStrategy.TIME_SERIES_SPLIT:
        # TimeSeriesSplit needs enough rows for n_splits+1 train blocks.
        max_ts = max(MIN_FOLDS, min(cap, (n_rows - 1) // 2))
        cap = min(cap, max_ts)
    return max(MIN_FOLDS, min(cap, MAX_FOLDS))


def compute_trusted_target_stats(
    df: pl.DataFrame,
    *,
    dataset_sha256: str,
    config: MLConfig,
    trusted: bool = True,
    trust_reason: str | None = None,
) -> TrustedTargetStats:
    """Compute full-data target/group/time statistics for preflight and UI."""
    n_rows = df.height
    stats = TrustedTargetStats(
        dataset_sha256=dataset_sha256,
        target=config.target,
        task_type=config.task_type.value,
        n_rows=n_rows,
        n_non_null_target=0,
        trusted=trusted,
        trust_reason=trust_reason,
    )
    if not trusted or config.target not in df.columns:
        stats.trusted = False
        stats.trust_reason = trust_reason or "target column missing from loaded data"
        return stats

    y = df.get_column(config.target)
    non_null = y.drop_nulls()
    stats.n_non_null_target = non_null.len()

    if is_classification(config):
        counts = non_null.value_counts(sort=True)
        class_counts = {str(row[0]): int(row[1]) for row in counts.iter_rows()}
        stats.class_counts = class_counts
        stats.n_classes = len(class_counts)
        stats.min_class_count = min(class_counts.values()) if class_counts else 0
    else:
        stats.class_counts = {}
        stats.n_classes = None
        stats.min_class_count = None

    if config.group_variable:
        if config.group_variable not in df.columns:
            stats.trusted = False
            stats.trust_reason = trust_reason or "group column missing from loaded data"
        else:
            stats.n_groups = df.get_column(config.group_variable).drop_nulls().n_unique()

    if config.time_variable:
        if config.time_variable not in df.columns:
            stats.trusted = False
            stats.trust_reason = trust_reason or "time column missing from loaded data"
        else:
            tcol = df.get_column(config.time_variable)
            stats.n_time_rows = tcol.drop_nulls().len()
            # Ascending (non-decreasing) only — descending is not a valid
            # TimeSeriesSplit direction, and unknown row order is never OK.
            stats.time_is_sorted_ascending = bool(tcol.drop_nulls().is_sorted())

    stats.suggested_n_splits = suggest_n_splits(
        task_type=config.task_type,
        n_rows=n_rows,
        min_class_count=stats.min_class_count,
        n_groups=stats.n_groups,
        split_strategy=config.split_strategy,
        n_classes=stats.n_classes,
    )
    return stats


def preflight_config_columns(config: MLConfig, column_names: set[str]) -> None:
    """Schema-level checks: required Group/Time columns must be explicit and known."""
    if config.split_strategy == SplitStrategy.GROUP_KFOLD:
        if not config.group_variable:
            _fail(
                "ML_GROUP_VARIABLE_REQUIRED",
                "Group K-Fold requires an explicit group variable. "
                "Select a grouping column; Quantelune will not fall back to plain K-Fold.",
            )
        if config.group_variable not in column_names:
            _fail(
                "ML_GROUP_COLUMN_UNKNOWN",
                f"Group variable '{config.group_variable}' is not in the dataset schema.",
            )
    if config.split_strategy == SplitStrategy.TIME_SERIES_SPLIT:
        if not config.time_variable:
            _fail(
                "ML_TIME_VARIABLE_REQUIRED",
                "Time-series split requires an explicit time variable. "
                "Select a time column; Quantelune will not run on unknown row order.",
            )
        if config.time_variable not in column_names:
            _fail(
                "ML_TIME_COLUMN_UNKNOWN",
                f"Time variable '{config.time_variable}' is not in the dataset schema.",
            )
    # Group/Time columns must also be known even when used with other strategies
    # if the client sent them (so later strategy switches cannot resurrect ghosts).
    if config.group_variable and config.group_variable not in column_names:
        _fail(
            "ML_GROUP_COLUMN_UNKNOWN",
            f"Group variable '{config.group_variable}' is not in the dataset schema.",
        )
    if config.time_variable and config.time_variable not in column_names:
        _fail(
            "ML_TIME_COLUMN_UNKNOWN",
            f"Time variable '{config.time_variable}' is not in the dataset schema.",
        )
    if (
        config.split_strategy == SplitStrategy.GROUP_KFOLD
        and config.group_variable == config.target
    ):
        _fail(
            "INVALID_ML_INPUT",
            "Group variable must not also be the target.",
        )
    if (
        config.split_strategy == SplitStrategy.TIME_SERIES_SPLIT
        and config.time_variable == config.target
    ):
        _fail(
            "INVALID_ML_INPUT",
            "Time variable must not also be the target.",
        )


def preflight_data(
    config: MLConfig,
    df: pl.DataFrame,
    stats: TrustedTargetStats | None = None,
) -> TrustedTargetStats:
    """Data-level feasibility checks against the actual training frame."""
    if stats is None:
        stats = compute_trusted_target_stats(df, dataset_sha256="", config=config)

    if config.target not in df.columns:
        _fail(
            "INVALID_ML_INPUT",
            f"Dataset does not contain the following columns: {config.target}.",
        )

    if not stats.trusted and stats.trust_reason:
        _fail(
            "ML_PREFLIGHT_INSUFFICIENT_STATS",
            f"Cannot verify ML feasibility from trusted full-data statistics "
            f"({stats.trust_reason}). Refusing to run on unverified data.",
        )

    if is_classification(config):
        if stats.n_classes is None or stats.n_classes < 2:
            _fail(
                "ML_TARGET_SINGLE_CLASS",
                f"Target '{config.target}' has a single class; classification requires "
                "at least two classes in the training data.",
            )
        if config.task_type == MLTaskType.BINARY_CLASSIFICATION and stats.n_classes != 2:
            _fail(
                "ML_NOT_BINARY",
                f"binary_classification expects 2 classes in '{config.target}', "
                f"found {stats.n_classes}.",
            )
        if stats.min_class_count is not None and stats.min_class_count < config.n_splits:
            _fail(
                "ML_MIN_CLASS_BELOW_FOLDS",
                f"The smallest target class has {stats.min_class_count} rows, which is "
                f"less than the requested {config.n_splits} folds. Reduce folds or "
                "collect more rows for the minority class.",
            )
    else:
        # Regression: target must be a finite numeric column on kept rows.
        target_series = df.get_column(config.target)
        y_kept = target_series.to_numpy()[valid_target_mask(target_series)]
        require_regressable_target(y_kept, config.target, source_dtype=target_series.dtype)

    if config.split_strategy == SplitStrategy.GROUP_KFOLD:
        if config.group_variable is None:
            _fail(
                "ML_GROUP_VARIABLE_REQUIRED",
                "Group K-Fold requires an explicit group variable.",
            )
        if config.group_variable not in df.columns:
            _fail(
                "ML_GROUP_COLUMN_UNKNOWN",
                f"Group variable '{config.group_variable}' is not in the dataset schema.",
            )
        n_groups = stats.n_groups
        if n_groups is None:
            gcol = df.get_column(config.group_variable)
            n_groups = gcol.drop_nulls().n_unique()
        if n_groups < MIN_FOLDS:
            _fail(
                "ML_GROUPS_BELOW_FOLDS",
                f"Group variable '{config.group_variable}' has {n_groups} distinct "
                f"groups; Group K-Fold needs at least {MIN_FOLDS}.",
            )
        if n_groups < config.n_splits:
            _fail(
                "ML_GROUPS_BELOW_FOLDS",
                f"Group variable '{config.group_variable}' has {n_groups} distinct "
                f"groups, which is less than the requested {config.n_splits} folds. "
                "Reduce folds or choose a grouping column with more groups. "
                "Quantelune will not silently reduce Group K-Fold.",
            )
        if df.get_column(config.group_variable).null_count() > 0:
            _fail(
                "INVALID_ML_INPUT",
                f"Group variable '{config.group_variable}' contains nulls; "
                "Group K-Fold requires complete group labels.",
            )

    if config.split_strategy == SplitStrategy.TIME_SERIES_SPLIT:
        if config.time_variable is None:
            _fail(
                "ML_TIME_VARIABLE_REQUIRED",
                "Time-series split requires an explicit time variable.",
            )
        if config.time_variable not in df.columns:
            _fail(
                "ML_TIME_COLUMN_UNKNOWN",
                f"Time variable '{config.time_variable}' is not in the dataset schema.",
            )
        tcol = df.get_column(config.time_variable)
        if tcol.null_count() > 0:
            _fail(
                "INVALID_ML_INPUT",
                f"Time variable '{config.time_variable}' contains nulls; "
                "time-series split requires complete timestamps.",
            )
        ascending = stats.time_is_sorted_ascending
        if ascending is None:
            ascending = bool(tcol.is_sorted())
        if not ascending:
            _fail(
                "ML_TIME_ORDER_INVALID",
                f"Rows are not sorted ascending by '{config.time_variable}'. "
                "Time-series split requires training input in non-decreasing time "
                "order; Quantelune will not train on unknown or unsorted row order.",
            )
        # Prefer training-subset length (after target filter + held-out); fall back
        # to full column only when stats did not supply a train-side count.
        n_time = stats.n_time_rows if stats.n_time_rows is not None else tcol.len()
        if n_time < config.n_splits + 1:
            _fail(
                "ML_TIME_ROWS_BELOW_FOLDS",
                f"Time-series split needs at least n_splits+1 training rows "
                f"({config.n_splits + 1}), found {n_time} after target filtering "
                "and held-out split.",
            )

    return stats


def training_subset_stats(
    df: pl.DataFrame,
    config: MLConfig,
    *,
    dataset_sha256: str,
    trusted: bool = True,
    trust_reason: str | None = None,
) -> TrustedTargetStats:
    """Stats on the *actual training subset* after target-null filter + held-out.

    Full-data class/group counts can look feasible while the CV training portion
    (after the engine leaves out a test fold) cannot support the requested folds.
    UI suggestions and preflight must use this training-subset view.
    """
    from app.ml.splits import held_out_test_split  # local import avoids cycle

    if config.target not in df.columns:
        return TrustedTargetStats(
            dataset_sha256=dataset_sha256,
            target=config.target,
            task_type=config.task_type.value,
            n_rows=df.height,
            n_non_null_target=0,
            trusted=False,
            trust_reason=trust_reason or "target column missing from loaded data",
        )

    y_series = df.get_column(config.target)
    keep = valid_target_mask(y_series)
    row_index = np.flatnonzero(keep)
    y = y_series.to_numpy()[row_index]
    if config.task_type == MLTaskType.REGRESSION and y.size:
        # Fail closed before any holdout/split: Utf8 text or ±Inf cannot be fitted.
        require_regressable_target(y, config.target, source_dtype=y_series.dtype)
    groups_all = None
    if config.group_variable and config.group_variable in df.columns:
        groups_all = df.get_column(config.group_variable).to_numpy()[row_index]
        # Validate labels BEFORE unique/split so null/NaN never becomes 500.
        if config.split_strategy == SplitStrategy.GROUP_KFOLD or groups_all is not None:
            try:
                _require_valid_group_labels(groups_all, config.group_variable)
            except StatForgeError:
                if config.split_strategy == SplitStrategy.GROUP_KFOLD:
                    raise
                # Non-group strategies: keep going but mark untrusted if labels bad.
                return TrustedTargetStats(
                    dataset_sha256=dataset_sha256,
                    target=config.target,
                    task_type=config.task_type.value,
                    n_rows=df.height,
                    n_non_null_target=int(len(y)),
                    trusted=False,
                    trust_reason=f"group variable '{config.group_variable}' has invalid labels",
                )

    n = len(y)
    if n == 0:
        stats = TrustedTargetStats(
            dataset_sha256=dataset_sha256,
            target=config.target,
            task_type=config.task_type.value,
            n_rows=df.height,
            n_non_null_target=0,
            trusted=trusted,
            trust_reason=trust_reason,
        )
        stats.trusted = False
        stats.trust_reason = trust_reason or "no non-null target rows"
        return stats

    try:
        train_idx, _test_idx = held_out_test_split(
            n,
            config,
            y if config.task_type != MLTaskType.REGRESSION else np.zeros(n),
            groups=groups_all,
        )
    except ValueError as exc:
        stats = TrustedTargetStats(
            dataset_sha256=dataset_sha256,
            target=config.target,
            task_type=config.task_type.value,
            n_rows=df.height,
            n_non_null_target=n,
            trusted=False,
            trust_reason=str(exc),
        )
        return stats
    except TypeError as exc:
        # Should be unreachable after label validation; keep a stable 422.
        _fail(
            "INVALID_ML_INPUT",
            f"Group variable '{config.group_variable}' has unusable labels: {exc}",
        )

    y_train = y[train_idx]
    n_train = int(len(train_idx))

    stats = TrustedTargetStats(
        dataset_sha256=dataset_sha256,
        target=config.target,
        task_type=config.task_type.value,
        n_rows=df.height,
        n_non_null_target=n,
        trusted=trusted,
        trust_reason=trust_reason,
    )
    if is_classification(config):
        vals, cnts = np.unique(y_train, return_counts=True)
        class_counts = {str(v): int(c) for v, c in zip(vals.tolist(), cnts.tolist())}
        stats.class_counts = class_counts
        stats.n_classes = len(class_counts)
        stats.min_class_count = int(cnts.min()) if len(cnts) else 0
    if groups_all is not None:
        stats.n_groups = int(np.unique(groups_all[train_idx]).size)
    if config.time_variable and config.time_variable in df.columns:
        tcol = df.get_column(config.time_variable)
        # Keep order as seen after target-null filter (engine training input order).
        kept_time = tcol.to_numpy()[row_index]
        train_time = kept_time[train_idx]
        # TimeSeriesSplit runs on the training portion only — folds feasibility
        # must use that length, not the full column.
        stats.n_time_rows = int(len(train_time))
        try:
            stats.time_is_sorted_ascending = (
                bool(np.all(kept_time[1:] >= kept_time[:-1])) if len(kept_time) > 1 else True
            )
        except TypeError:
            stats.time_is_sorted_ascending = bool(tcol.drop_nulls().is_sorted())
        if stats.time_is_sorted_ascending is False:
            # Never invent folds guidance for unsorted time order.
            stats.suggested_n_splits = None
            return stats
    stats.suggested_n_splits = suggest_n_splits(
        task_type=config.task_type,
        n_rows=n_train,
        min_class_count=stats.min_class_count,
        n_groups=stats.n_groups,
        split_strategy=config.split_strategy,
        n_classes=stats.n_classes,
    )
    return stats


def replace_group_name(config: MLConfig, group_variable: str | None) -> MLConfig:
    """Return a shallow config copy with a different group column name."""
    from dataclasses import replace as dc_replace

    return dc_replace(config, group_variable=group_variable)


def run_preflight(
    config: MLConfig,
    df: pl.DataFrame,
    *,
    dataset_sha256: str,
    column_names: set[str] | None = None,
) -> TrustedTargetStats:
    """Full preflight: schema columns + training-subset feasibility."""
    if column_names is not None:
        preflight_config_columns(config, column_names)
    else:
        preflight_config_columns(config, set(df.columns))
    stats = training_subset_stats(df, config, dataset_sha256=dataset_sha256)
    return preflight_data(config, df, stats)
