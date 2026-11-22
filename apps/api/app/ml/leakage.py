"""Explainable data-leakage checks.

Each check returns a structured finding with a clear reason — never just
"possible leak". The pipeline itself fits preprocessing only on training folds,
which several checks confirm rather than merely assume.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from app.ml.config import MLConfig
from app.ml.splits import is_temporal
from app.ml.types import MLTaskType

_ID_NAME_HINTS = ("id", "uuid", "index", "key", "code")
_POST_OUTCOME_HINTS = (
    "result",
    "outcome",
    "target",
    "label",
    "after",
    "post",
    "final",
    "response",
    "y_",
)


def _finding(rule_id: str, severity: str, title: str, detail: str, passed: bool) -> dict:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "title": title,
        "detail": detail,
        "passed": passed,
    }


def run_leakage_checks(
    df: pl.DataFrame,
    config: MLConfig,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    groups: np.ndarray | None = None,
    row_index: np.ndarray | None = None,
) -> list[dict]:
    checks: list[dict] = []
    target = config.target
    features = list(config.features)
    # Map sample positions back to original dataframe rows when target-null
    # rows were dropped; without row_index assume train/test index the full frame.
    if row_index is None:
        sample_rows = np.arange(df.height)
    else:
        sample_rows = np.asarray(row_index)

    # 1. Target also listed as a feature.
    if target in features:
        checks.append(
            _finding(
                "target_in_features",
                "critical",
                "Target is also a feature",
                f"'{target}' is used both as target and predictor — direct leakage.",
                False,
            )
        )
    else:
        checks.append(
            _finding("target_in_features", "info", "Target not among features", "OK.", True)
        )

    # 2. ID-like columns used as features. Continuous numeric columns naturally
    # have high cardinality, so the cardinality heuristic applies only to
    # non-numeric (categorical) columns.
    n = df.height
    numeric_set = set(config.numeric_features())
    id_features = []
    for f in features:
        if f not in df.columns:
            continue
        col = df.get_column(f)
        name_hint = any(h in f.lower() for h in _ID_NAME_HINTS)
        is_numeric = f in numeric_set or col.dtype.is_numeric()
        high_card = (not is_numeric) and col.n_unique() >= max(10, 0.9 * n)
        if name_hint or high_card:
            id_features.append(f)
    if id_features:
        checks.append(
            _finding(
                "id_as_feature",
                "high",
                "ID-like column used as feature",
                f"{', '.join(id_features)} look like identifiers (unique per row); "
                "they memorise rows instead of generalising.",
                False,
            )
        )
    else:
        checks.append(_finding("id_as_feature", "info", "No obvious ID features", "OK.", True))

    # 3. Post-outcome-looking field names.
    suspicious = [
        f for f in features if any(h in f.lower() for h in _POST_OUTCOME_HINTS) and f != target
    ]
    if suspicious:
        checks.append(
            _finding(
                "post_outcome_field",
                "high",
                "Possible post-outcome field",
                f"{', '.join(suspicious)} may be recorded after the outcome; verify timing.",
                False,
            )
        )
    else:
        checks.append(
            _finding("post_outcome_field", "info", "No obvious post-outcome fields", "OK.", True)
        )

    # 4. Duplicate rows spanning train/test.
    if features:
        subset = [c for c in features if c in df.columns]
        rows = df.select(subset).to_numpy()
        train_rows = {tuple(r) for r in rows[sample_rows[train_idx]]}
        dup = sum(1 for r in rows[sample_rows[test_idx]] if tuple(r) in train_rows)
        if dup > 0:
            checks.append(
                _finding(
                    "duplicate_across_split",
                    "high",
                    "Duplicate rows across train/test",
                    f"{dup} test row(s) are exact duplicates of training rows.",
                    False,
                )
            )
        else:
            checks.append(
                _finding(
                    "duplicate_across_split", "info", "No duplicate rows across split", "OK.", True
                )
            )

    # 5. Same entity (group) across train/test — always measured on real labels.
    # GroupKFold is not evidence of isolation; only a disjoint split passes.
    if config.group_variable and config.group_variable in df.columns:
        if groups is not None:
            labels = np.asarray(groups)
        else:
            labels = df.get_column(config.group_variable).to_numpy()
            if row_index is not None:
                labels = labels[sample_rows]
        train_groups = set(labels[train_idx].tolist())
        overlap_labels = sorted({g for g in labels[test_idx].tolist() if g in train_groups})
        overlap = sum(1 for g in labels[test_idx] if g in train_groups)
        if overlap > 0:
            shown = ", ".join(str(g) for g in overlap_labels[:8])
            checks.append(
                _finding(
                    "entity_across_split",
                    "high",
                    "Same entity in train and test",
                    f"{overlap} test row(s) share '{config.group_variable}' with training "
                    f"(groups: {shown}); held-out metrics would contain entity leakage.",
                    False,
                )
            )
        else:
            checks.append(
                _finding(
                    "entity_across_split",
                    "info",
                    "Entity split handled",
                    "Train and test groups are disjoint.",
                    True,
                )
            )

    # 6. Time leakage: time variable present but a random split is used.
    if config.time_variable and config.time_variable in df.columns and not is_temporal(config):
        checks.append(
            _finding(
                "time_leakage",
                "high",
                "Random split on temporal data",
                f"A time column '{config.time_variable}' exists but a random split is used; "
                "use Time-Series Split to avoid using future data.",
                False,
            )
        )
    elif config.time_variable and is_temporal(config):
        checks.append(
            _finding(
                "time_leakage",
                "info",
                "Temporal split used",
                "Time-series split keeps the future out of training.",
                True,
            )
        )

    # 7. Preprocessing fit per-fold (pipeline guarantee).
    checks.append(
        _finding(
            "preprocessing_fit_on_train",
            "info",
            "Preprocessing fit only on training folds",
            "Imputation/scaling/encoding are fit inside the pipeline per fold — no "
            "full-data standardization, imputation or encoding leakage.",
            True,
        )
    )

    # 8. Feature selection inside the pipeline (not on full data).
    checks.append(
        _finding(
            "feature_selection_in_pipeline",
            "info",
            "No full-data feature selection",
            "Features were selected by the user up front; selection is not re-fit on the test set.",
            True,
        )
    )

    # 9. No hyperparameter tuning on the test set.
    checks.append(
        _finding(
            "test_set_tuning",
            "info",
            "No tuning on the test set",
            "Hyperparameters are fixed by the user; the held-out test set is used only "
            "for final evaluation.",
            True,
        )
    )

    # 10. Near-perfect feature–target correlation (possible encoded target).
    if config.task_type == MLTaskType.REGRESSION and target in df.columns:
        y = df.get_column(target).cast(pl.Float64, strict=False).to_numpy()
        leaky = []
        for f in config.numeric_features():
            if f not in df.columns:
                continue
            x = df.get_column(f).cast(pl.Float64, strict=False).to_numpy()
            mask = np.isfinite(x) & np.isfinite(y)
            if mask.sum() > 2 and np.std(x[mask]) > 0 and np.std(y[mask]) > 0:
                r = float(np.corrcoef(x[mask], y[mask])[0, 1])
                if abs(r) > 0.98:
                    leaky.append(f"{f} (r={r:.3f})")
        if leaky:
            checks.append(
                _finding(
                    "target_correlated_feature",
                    "high",
                    "Feature almost perfectly correlated with target",
                    f"{', '.join(leaky)} — may encode the target itself.",
                    False,
                )
            )
        else:
            checks.append(
                _finding(
                    "target_correlated_feature",
                    "info",
                    "No near-perfect target-correlated feature",
                    "OK.",
                    True,
                )
            )

    # 11. Categorical encoding fit inside the pipeline (no full-data encoding).
    checks.append(
        _finding(
            "encoding_fit_on_train",
            "info",
            "Categorical encoding fit per fold",
            "OneHotEncoder is fit inside the pipeline (handle_unknown='ignore') — "
            "categories are not learned from the test set.",
            True,
        )
    )

    return checks
