import type { StatisticalMethod } from "./types";
import type { AppTranslator, NoParameterMessageKey } from "./i18n-messages";
import { ownedRecordValue } from "./owned-record-value";
const VERSIONS: Record<string, string> = {
  evaluate_predictions: "1.0.0",
  evaluate_binary_predictions: "1.0.0",
  descriptive: "1.0.0",
  pearson: "1.0.1",
  spearman: "1.0.1",
  kendall: "1.0.0",
  one_sample_t: "1.0.0",
  welch_t: "1.1.0",
  student_t: "1.0.0",
  paired_t: "1.0.0",
  mann_whitney: "1.0.0",
  wilcoxon: "1.0.0",
  chi_square_independence: "1.0.0",
  chi_square_gof: "1.0.0",
  fisher_exact: "1.0.0",
  one_way_anova: "1.0.0",
  welch_anova: "1.0.0",
  kruskal_wallis: "1.0.0",
  linear_regression: "1.0.0",
  logistic_regression: "1.0.0",
  ridge_regression: "1.0.0",
  lasso_regression: "1.0.0",
  elastic_net_regression: "1.0.0",
};
const NAMES: Record<string, NoParameterMessageKey> = {
  evaluate_predictions: "statistics.catalog.evaluate_predictions.name",
  evaluate_binary_predictions:
    "statistics.catalog.evaluate_binary_predictions.name",
  descriptive: "statistics.catalog.descriptive.name",
  pearson: "statistics.catalog.pearson.name",
  spearman: "statistics.catalog.spearman.name",
  kendall: "statistics.catalog.kendall.name",
  one_sample_t: "statistics.catalog.one_sample_t.name",
  welch_t: "statistics.catalog.welch_t.name",
  student_t: "statistics.catalog.student_t.name",
  paired_t: "statistics.catalog.paired_t.name",
  mann_whitney: "statistics.catalog.mann_whitney.name",
  wilcoxon: "statistics.catalog.wilcoxon.name",
  chi_square_independence: "statistics.catalog.chi_square_independence.name",
  chi_square_gof: "statistics.catalog.chi_square_gof.name",
  fisher_exact: "statistics.catalog.fisher_exact.name",
  one_way_anova: "statistics.catalog.one_way_anova.name",
  welch_anova: "statistics.catalog.welch_anova.name",
  kruskal_wallis: "statistics.catalog.kruskal_wallis.name",
  linear_regression: "statistics.catalog.linear_regression.name",
  logistic_regression: "statistics.catalog.logistic_regression.name",
  ridge_regression: "statistics.catalog.ridge_regression.name",
  lasso_regression: "statistics.catalog.lasso_regression.name",
  elastic_net_regression: "statistics.catalog.elastic_net_regression.name",
};
const DESCRIPTIONS: Record<string, NoParameterMessageKey> = {
  evaluate_predictions: "statistics.catalog.evaluate_predictions.description",
  evaluate_binary_predictions:
    "statistics.catalog.evaluate_binary_predictions.description",
  descriptive: "statistics.catalog.descriptive.description",
  pearson: "statistics.catalog.pearson.description",
  spearman: "statistics.catalog.spearman.description",
  kendall: "statistics.catalog.kendall.description",
  one_sample_t: "statistics.catalog.one_sample_t.description",
  welch_t: "statistics.catalog.welch_t.description",
  student_t: "statistics.catalog.student_t.description",
  paired_t: "statistics.catalog.paired_t.description",
  mann_whitney: "statistics.catalog.mann_whitney.description",
  wilcoxon: "statistics.catalog.wilcoxon.description",
  chi_square_independence:
    "statistics.catalog.chi_square_independence.description",
  chi_square_gof: "statistics.catalog.chi_square_gof.description",
  fisher_exact: "statistics.catalog.fisher_exact.description",
  one_way_anova: "statistics.catalog.one_way_anova.description",
  welch_anova: "statistics.catalog.welch_anova.description",
  kruskal_wallis: "statistics.catalog.kruskal_wallis.description",
  linear_regression: "statistics.catalog.linear_regression.description",
  logistic_regression: "statistics.catalog.logistic_regression.description",
  ridge_regression: "statistics.catalog.ridge_regression.description",
  lasso_regression: "statistics.catalog.lasso_regression.description",
  elastic_net_regression:
    "statistics.catalog.elastic_net_regression.description",
};
const CATEGORIES: Record<string, NoParameterMessageKey> = {
  descriptive: "statistics.category.descriptive",
  correlation: "statistics.category.correlation",
  two_group: "statistics.category.two_group",
  categorical: "statistics.category.categorical",
  multi_group: "statistics.category.multi_group",
  regression: "statistics.category.regression",
  prediction_evaluation: "statistics.category.prediction_evaluation",
};
// Only the fixed, version-matched registry receives display aliases.
function knownVersion(method: StatisticalMethod) {
  return (
    ownedRecordValue(VERSIONS, method.method_id) === method.version ||
    (method.method_id === "descriptive" && ["1.0.1", "1.0.2"].includes(method.version))
  );
}
export function methodDisplayName(
  method: StatisticalMethod,
  t: AppTranslator,
): string {
  const key = ownedRecordValue(NAMES, method.method_id);
  return key && knownVersion(method) ? t(key) : method.name;
}
export function methodDisplayDescription(
  method: StatisticalMethod,
  t: AppTranslator,
): string {
  const key = ownedRecordValue(DESCRIPTIONS, method.method_id);
  return key && knownVersion(method) ? t(key) : method.description;
}
export function methodCategoryLabel(id: string, t: AppTranslator): string {
  const key = ownedRecordValue(CATEGORIES, id);
  return key ? t(key) : id;
}
