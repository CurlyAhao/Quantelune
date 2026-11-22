"""Regression methods: linear, logistic, ridge, lasso, elastic-net.

Inferential models (linear / logistic) use ``statsmodels`` and report
coefficients, standard errors, p-values, confidence intervals and diagnostics.
Regularized models (ridge / lasso / elastic-net) use ``scikit-learn`` and are
clearly labelled as *prediction* models (coefficients on standardized features,
cross-validated performance) — they do not produce p-values.

Categorical predictors are one-hot encoded with an explicit reference category
dropped to avoid the dummy-variable trap; the encoding map is reported so the
result is fully interpretable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from sklearn.linear_model import ElasticNet, Lasso, Ridge
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler

from app.statistics.assumptions import check_normality, check_sample_size
from app.statistics.base import StatisticalMethod
from app.statistics.context import StatisticalContext
from app.statistics.missing_preflight import RESULT_METADATA_VERSION, require_complete_inputs
from app.statistics.result import AssumptionCheck, SampleInfo
from app.statistics.types import MIN_SAMPLE_SIZE, MethodId


def _build_frame(ctx: StatisticalContext) -> tuple[pd.DataFrame, list[str], list[str], dict]:
    """Assemble a pandas frame of the target + predictors (numeric & categorical).

    Returns (frame, numeric_predictors, categorical_predictors, encoding_map).
    """
    target = ctx.config.target
    numeric = [v for v in ctx.config.variables if v != target]
    categorical = [c for c in ctx.config.categorical_variables if c != target and c not in numeric]
    cols = [target, *numeric, *categorical]
    cols = [c for c in cols if c and c in ctx.df.columns]
    frame = ctx.df.select(cols).to_pandas()
    # Coerce numeric predictors to float; the target is handled per-method
    # (linear needs numeric, logistic accepts string or numeric binary).
    for c in numeric:
        if c in frame.columns:
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
    encoding_map: dict[str, dict] = {}
    for c in categorical:
        encoding_map[c] = {"levels": sorted({str(v) for v in frame[c].dropna().unique()})}
    return frame, numeric, categorical, encoding_map


def _design_matrix(
    frame: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
    references: dict[str, str] | None,
    *,
    drop_target: str | None,
) -> tuple[pd.DataFrame, SampleInfo, dict, pd.DataFrame]:
    """Build X (with dummy columns) after listwise deletion.

    Returns (X, sample, encoding, clean_frame) where ``clean_frame`` holds the
    rows that survived deletion, so callers can extract an aligned target.
    """
    references = references or {}
    involved = [c for c in [drop_target, *numeric, *categorical] if c and c in frame.columns]
    original_n = len(frame)
    clean = frame.dropna(subset=involved).reset_index(drop=True)
    excluded = original_n - len(clean)

    pieces: dict[str, np.ndarray] = {}
    col_labels: list[str] = []
    encoding: dict[str, dict] = {}
    for c in numeric:
        pieces[c] = clean[c].to_numpy(dtype=float)
        col_labels.append(c)
    for c in categorical:
        levels = sorted({str(v) for v in clean[c].unique()})
        ref = references.get(c, levels[0] if levels else "")
        non_ref = [lv for lv in levels if lv != ref]
        encoding[c] = {"reference": ref, "encoded_levels": non_ref, "all_levels": levels}
        for lv in non_ref:
            label = f"{c}={lv}"
            pieces[label] = (clean[c].astype(str) == lv).to_numpy(dtype=float)
            col_labels.append(label)

    x = pd.DataFrame(pieces, columns=col_labels)
    sample = SampleInfo(
        original_n=original_n,
        excluded_n=excluded,
        final_n=len(clean),
        reason="rows with missing target/predictor removed (listwise)",
        per_variable={c: int(frame[c].isna().sum()) for c in involved},
    )
    return x, sample, encoding, clean


def _vif(x_with_const: pd.DataFrame) -> dict[str, float]:
    """Variance inflation factors per predictor (constant column excluded)."""
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    cols = [c for c in x_with_const.columns if c != "const"]
    matrix = x_with_const[cols].to_numpy(dtype=float)
    out: dict[str, float] = {}
    for i, c in enumerate(cols):
        try:
            out[c] = float(variance_inflation_factor(matrix, i))
        except Exception:  # noqa: BLE001
            out[c] = float("nan")
    return out


class LinearRegressionMethod(StatisticalMethod):
    method_id = MethodId.LINEAR_REGRESSION
    version = RESULT_METADATA_VERSION
    name = "Linear regression (OLS)"
    description = (
        "Ordinary least-squares linear/multiple regression with inferential "
        "statistics (coefficients, p-values, CI), R², VIF and residual diagnostics."
    )
    min_variables = 1
    max_variables = 50
    requires_numeric = False

    def _effective_variable_count(self, ctx):
        return len(ctx.config.variables) + len(ctx.config.categorical_variables)

    def validate(self, ctx):
        checks = super().validate(ctx)
        if not ctx.config.target:
            checks.append(
                AssumptionCheck("target", False, "A target (outcome) variable is required.")
            )
        elif not ctx.has_column(ctx.config.target):
            checks.append(
                AssumptionCheck("target", False, f"Target '{ctx.config.target}' does not exist.")
            )
        return checks

    def execute(self, ctx):
        result = self.base_result(ctx)
        require_complete_inputs(ctx)
        frame, numeric, categorical, _ = _build_frame(ctx)
        predictors = [*numeric, *categorical]
        if not predictors:
            return self.not_applicable(ctx, "At least one predictor variable is required.")
        x, sample, encoding, clean = _design_matrix(
            frame,
            numeric,
            categorical,
            ctx.config.reference_categories,
            drop_target=ctx.config.target,
        )
        y = pd.to_numeric(clean[ctx.config.target], errors="coerce").to_numpy(dtype=float)
        n = len(x)
        sample.final_n = n
        result.sample = sample
        result.add_check(check_sample_size(n, MIN_SAMPLE_SIZE[self.method_id]))
        if n <= len(x.columns) + 1:
            return self.not_applicable(
                ctx, f"Need more observations ({n}) than parameters ({len(x.columns) + 1})."
            )

        x_const = sm.add_constant(x, has_constant="add")
        model = sm.OLS(y, x_const).fit()

        conf = model.conf_int(alpha=1 - ctx.config.confidence_level)
        coefs = []
        for name in model.params.index:
            coefs.append(
                {
                    "term": name,
                    "coefficient": float(model.params[name]),
                    "std_error": float(model.bse[name]),
                    "t": float(model.tvalues[name]),
                    "p_value": float(model.pvalues[name]),
                    "ci_lower": float(conf.loc[name, 0]),
                    "ci_upper": float(conf.loc[name, 1]),
                }
            )

        vif = _vif(x_const)
        # Residual diagnostics.
        resid = model.resid
        result.add_check(check_normality(resid.to_numpy(), "residuals"))
        try:
            from statsmodels.stats.diagnostic import het_breuschpagan

            bp, bp_p, _, _ = het_breuschpagan(resid, model.exog)
            result.add_check(
                AssumptionCheck(
                    name="homoscedasticity",
                    passed=bool(bp_p >= 0.05),
                    detail=(
                        f"Breusch–Pagan LM={float(bp):.3f}, p={float(bp_p):.4g}; "
                        + (
                            "constant variance plausible."
                            if bp_p >= 0.05
                            else "evidence of heteroscedasticity."
                        )
                    ),
                    value={"statistic": float(bp), "p_value": float(bp_p)},
                    threshold=0.05,
                )
            )
        except Exception:  # noqa: BLE001
            pass
        # High-influence points via Cook's distance.
        try:
            influence = model.get_influence()
            cooks, _ = influence.cooks_distance
            n_high = int((cooks > 4 / n).sum())
            result.add_check(
                AssumptionCheck(
                    name="high_influence_points",
                    passed=n_high == 0,
                    detail=f"{n_high} point(s) with Cook's distance > 4/n.",
                    value={
                        "count": n_high,
                        "max_cooks_distance": float(np.max(cooks)) if len(cooks) else 0.0,
                    },
                )
            )
        except Exception:  # noqa: BLE001
            pass

        high_vif = {k: v for k, v in vif.items() if np.isfinite(v) and v > 10}
        if high_vif:
            result.warnings.append(
                f"Possible multicollinearity: VIF > 10 for {', '.join(high_vif.keys())}."
            )

        # Residual points for plotting (capped).
        fitted = model.fittedvalues.to_numpy()
        resid_arr = resid.to_numpy()
        cap = min(n, 300)
        idx = np.linspace(0, n - 1, cap).astype(int) if n > cap else np.arange(n)
        qq = stats.probplot(resid_arr, dist="norm")
        result.statistic = {
            "name": "F",
            "value": float(model.fvalue),
            "df": float(model.df_model),
            "df_model": float(model.df_model),
            "df_resid": float(model.df_resid),
        }
        result.p_value = float(model.f_pvalue)
        result.estimates = {
            "coefficients": coefs,
            "encoding": encoding,
            "reference_categories": {c: encoding[c]["reference"] for c in encoding},
        }
        result.confidence_interval = {
            c["term"]: {"lower": c["ci_lower"], "upper": c["ci_upper"]} for c in coefs
        }
        result.effect_size = {
            "r_squared": float(model.rsquared),
            "adj_r_squared": float(model.rsquared_adj),
        }
        result.descriptive_statistics = {
            "n": n,
            "r_squared": float(model.rsquared),
            "adj_r_squared": float(model.rsquared_adj),
            "f_statistic": float(model.fvalue),
            "f_pvalue": float(model.f_pvalue),
            "aic": float(model.aic),
            "bic": float(model.bic),
            "vif": vif,
            "residual_std": float(np.std(resid_arr, ddof=1)),
        }
        result.diagnostics = {
            "residuals": [
                {"fitted": float(fitted[i]), "residual": float(resid_arr[i])} for i in idx
            ],
            "qq": {
                "theoretical": [float(v) for v in qq[0][0]],
                "sample": [float(v) for v in qq[0][1]],
            },
        }
        result.limitations.append(
            "OLS assumes linearity, independence, homoscedasticity and approximate "
            "normality of residuals; coefficients are associational, not causal."
        )
        return result


class LogisticRegressionMethod(StatisticalMethod):
    method_id = MethodId.LOGISTIC_REGRESSION
    name = "Logistic regression"
    description = (
        "Binary logistic regression with odds ratios, confidence intervals, "
        "confusion matrix, ROC-AUC and PR-AUC. Target must have exactly two levels."
    )
    min_variables = 1
    max_variables = 50
    requires_numeric = False

    def _effective_variable_count(self, ctx):
        return len(ctx.config.variables) + len(ctx.config.categorical_variables)

    def validate(self, ctx):
        checks = super().validate(ctx)
        if not ctx.config.target:
            checks.append(
                AssumptionCheck("target", False, "A target (outcome) variable is required.")
            )
        return checks

    def execute(self, ctx):
        result = self.base_result(ctx)
        frame, numeric, categorical, _ = _build_frame(ctx)
        predictors = [*numeric, *categorical]
        if not predictors:
            return self.not_applicable(ctx, "At least one predictor variable is required.")

        target = ctx.config.target
        # Encode the target to 0/1.
        target_vals = frame[target].dropna()
        is_numeric_target = pd.to_numeric(target_vals, errors="coerce").notna().all()
        if is_numeric_target:
            uniq = sorted(target_vals.astype(float).unique())
            if len(uniq) != 2:
                return self.not_applicable(
                    ctx, "Logistic regression needs a binary target (exactly 2 levels)."
                )
            level_map = {uniq[0]: 0, uniq[1]: 1}
            frame["_y"] = pd.to_numeric(frame[target], errors="coerce").map(level_map)
            level_names = [str(uniq[0]), str(uniq[1])]
        else:
            uniq = sorted({str(v) for v in target_vals.unique()})
            if len(uniq) != 2:
                return self.not_applicable(
                    ctx, "Logistic regression needs a binary target (exactly 2 levels)."
                )
            level_map = {uniq[0]: 0, uniq[1]: 1}
            frame["_y"] = frame[target].astype(str).map(level_map)
            level_names = uniq

        x, sample, encoding, clean = _design_matrix(
            frame, numeric, categorical, ctx.config.reference_categories, drop_target=target
        )
        y = clean["_y"].to_numpy(dtype=float)
        # Defensive: drop any rows where the encoded target is still NaN.
        valid = ~np.isnan(y)
        x, y = x[valid].reset_index(drop=True), y[valid]
        n = len(x)
        sample.final_n = n
        result.sample = sample
        result.add_check(check_sample_size(n, MIN_SAMPLE_SIZE[self.method_id]))
        if n <= len(x.columns) + 1:
            return self.not_applicable(ctx, "Need more observations than parameters.")

        x_const = sm.add_constant(x, has_constant="add")
        try:
            model = sm.Logit(y, x_const).fit(disp=0, maxiter=100)
        except Exception as exc:  # noqa: BLE001
            return self.not_applicable(ctx, f"Logistic model did not fit: {exc}")

        converged = bool(getattr(model, "mle_retvals", {}).get("converged", True))
        if not converged:
            result.warnings.append("The logistic model did not converge; interpret with caution.")

        conf = model.conf_int(alpha=1 - ctx.config.confidence_level)
        coefs = []
        separation_hint = False
        for name in model.params.index:
            se = float(model.bse[name])
            if se > 10:
                separation_hint = True
            coefs.append(
                {
                    "term": name,
                    "coefficient": float(model.params[name]),
                    "std_error": se,
                    "p_value": float(model.pvalues[name]),
                    "odds_ratio": float(np.exp(model.params[name])),
                    "or_ci_lower": float(np.exp(conf.loc[name, 0])),
                    "or_ci_upper": float(np.exp(conf.loc[name, 1])),
                }
            )
        if separation_hint:
            result.warnings.append(
                "Very large standard errors suggest complete or quasi-complete separation."
            )

        # Predictions + classification metrics at threshold 0.5.
        proba = model.predict(x_const)
        pred = (proba >= 0.5).astype(int)
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        tn = int(((pred == 0) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        try:
            from sklearn.metrics import average_precision_score, roc_auc_score

            roc_auc = float(roc_auc_score(y, proba))
            pr_auc = float(average_precision_score(y, proba))
        except Exception:  # noqa: BLE001
            roc_auc = pr_auc = float("nan")

        result.statistic = {"name": "log_likelihood", "value": float(model.llf)}
        result.p_value = None
        result.estimates = {
            "coefficients": coefs,
            "encoding": encoding,
            "reference_categories": {c: encoding[c]["reference"] for c in encoding},
            "target_levels": {level_names[0]: 0, level_names[1]: 1},
        }
        result.confidence_interval = {
            c["term"]: {"or_lower": c["or_ci_lower"], "or_upper": c["or_ci_upper"]} for c in coefs
        }
        result.effect_size = {"roc_auc": roc_auc, "pr_auc": pr_auc}
        result.descriptive_statistics = {
            "n": n,
            "log_likelihood": float(model.llf),
            "aic": float(model.aic),
            "bic": float(model.bic),
            "pseudo_r_squared": float(model.prsquared),
            "converged": converged,
            "classification_threshold": 0.5,
            "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
            "roc_auc": roc_auc,
            "pr_auc": pr_auc,
        }
        result.limitations.append(
            "Odds ratios describe association, not causation; predicted probabilities "
            "depend on the 0.5 threshold unless another threshold is chosen."
        )
        return result


class _RegularizedBase(StatisticalMethod):
    """Ridge / Lasso / ElasticNet — prediction models (no p-values)."""

    min_variables = 1
    max_variables = 50
    requires_numeric = False
    _model_kind = "regression"

    def _sklearn_model(self, ctx):
        raise NotImplementedError

    def _effective_variable_count(self, ctx):
        return len(ctx.config.variables) + len(ctx.config.categorical_variables)

    def validate(self, ctx):
        checks = super().validate(ctx)
        if not ctx.config.target:
            checks.append(
                AssumptionCheck("target", False, "A target (outcome) variable is required.")
            )
        return checks

    def execute(self, ctx):
        result = self.base_result(ctx)
        frame, numeric, categorical, _ = _build_frame(ctx)
        predictors = [*numeric, *categorical]
        if not predictors:
            return self.not_applicable(ctx, "At least one predictor variable is required.")
        x, sample, encoding, clean = _design_matrix(
            frame,
            numeric,
            categorical,
            ctx.config.reference_categories,
            drop_target=ctx.config.target,
        )
        y = pd.to_numeric(clean[ctx.config.target], errors="coerce").to_numpy(dtype=float)
        valid = ~np.isnan(y)
        x, y = x[valid].reset_index(drop=True), y[valid]
        n = len(x)
        sample.final_n = n
        result.sample = sample
        result.add_check(check_sample_size(n, MIN_SAMPLE_SIZE[self.method_id]))

        scaler = StandardScaler()
        x_scaled = scaler.fit_transform(x.to_numpy(dtype=float))
        model = self._sklearn_model(ctx)
        model.fit(x_scaled, y)
        train_score = float(model.score(x_scaled, y))

        # 5-fold (or fewer) cross-validated R².
        n_splits = min(5, max(2, n // 3))
        try:
            cv = cross_val_score(self._sklearn_model(ctx), x_scaled, y, cv=n_splits, scoring="r2")
            cv_mean, cv_std = float(np.mean(cv)), float(np.std(cv))
        except Exception:  # noqa: BLE001
            cv_mean = cv_std = float("nan")

        coefs = [{"term": term, "coefficient": float(c)} for term, c in zip(x.columns, model.coef_)]
        result.statistic = {"name": "cv_r2", "value": cv_mean}
        result.p_value = None
        result.estimates = {
            "coefficients": coefs,
            "intercept": float(model.intercept_),
            "encoding": encoding,
            "note": "coefficients are on standardized features (comparable magnitudes)",
        }
        result.effect_size = {"train_r2": train_score, "cv_r2_mean": cv_mean, "cv_r2_std": cv_std}
        result.descriptive_statistics = {
            "n": n,
            "n_features": len(x.columns),
            "train_r2": train_score,
            "cv_r2_mean": cv_mean,
            "cv_r2_std": cv_std,
            "cv_folds": n_splits,
            "alpha": ctx.config.alpha,
        }
        if self.method_id == MethodId.ELASTIC_NET_REGRESSION:
            result.descriptive_statistics["l1_ratio"] = ctx.config.l1_ratio
        result.limitations.append(
            "This is a regularized PREDICTION model: it has no p-values or confidence "
            "intervals. Use OLS/logistic regression for statistical inference."
        )
        result.warnings.append(
            "Regularized coefficients are shrinkage estimates for prediction, "
            "not inferential effects."
        )
        return result


class RidgeMethod(_RegularizedBase):
    method_id = MethodId.RIDGE_REGRESSION
    name = "Ridge regression"
    description = (
        "L2-regularized linear regression for prediction (shrinks coefficients; no p-values)."
    )

    def _sklearn_model(self, ctx):
        return Ridge(alpha=ctx.config.alpha)


class LassoMethod(_RegularizedBase):
    method_id = MethodId.LASSO_REGRESSION
    name = "Lasso regression"
    description = "L1-regularized linear regression for prediction/feature selection (no p-values)."

    def _sklearn_model(self, ctx):
        return Lasso(alpha=ctx.config.alpha)


class ElasticNetMethod(_RegularizedBase):
    method_id = MethodId.ELASTIC_NET_REGRESSION
    name = "Elastic-net regression"
    description = "Combined L1+L2 regularized linear regression for prediction (no p-values)."

    def _sklearn_model(self, ctx):
        return ElasticNet(alpha=ctx.config.alpha, l1_ratio=ctx.config.l1_ratio)


LINEAR_REGRESSION = LinearRegressionMethod()
LOGISTIC_REGRESSION = LogisticRegressionMethod()
RIDGE_REGRESSION = RidgeMethod()
LASSO_REGRESSION = LassoMethod()
ELASTIC_NET_REGRESSION = ElasticNetMethod()
