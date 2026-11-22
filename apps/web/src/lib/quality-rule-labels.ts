import type { AppTranslator, NoParameterMessageKey } from "./i18n-messages";
import { ownedRecordValue } from "./owned-record-value";
const RULES: Record<
  string,
  {
    version: string;
    name: NoParameterMessageKey;
    description: NoParameterMessageKey;
  }
> = {
  EMPTY_DATASET: {
    version: "1",
    name: "quality.rule.EMPTY_DATASET.name",
    description: "quality.rule.EMPTY_DATASET.description",
  },
  DUPLICATE_ROWS: {
    version: "1",
    name: "quality.rule.DUPLICATE_ROWS.name",
    description: "quality.rule.DUPLICATE_ROWS.description",
  },
  TOO_MANY_COLUMNS: {
    version: "1",
    name: "quality.rule.TOO_MANY_COLUMNS.name",
    description: "quality.rule.TOO_MANY_COLUMNS.description",
  },
  HIGH_OVERALL_MISSINGNESS: {
    version: "1",
    name: "quality.rule.HIGH_OVERALL_MISSINGNESS.name",
    description: "quality.rule.HIGH_OVERALL_MISSINGNESS.description",
  },
  EMPTY_COLUMN: {
    version: "1",
    name: "quality.rule.EMPTY_COLUMN.name",
    description: "quality.rule.EMPTY_COLUMN.description",
  },
  HIGH_MISSINGNESS: {
    version: "1",
    name: "quality.rule.HIGH_MISSINGNESS.name",
    description: "quality.rule.HIGH_MISSINGNESS.description",
  },
  CONSTANT_COLUMN: {
    version: "1",
    name: "quality.rule.CONSTANT_COLUMN.name",
    description: "quality.rule.CONSTANT_COLUMN.description",
  },
  NEAR_CONSTANT_COLUMN: {
    version: "1",
    name: "quality.rule.NEAR_CONSTANT_COLUMN.name",
    description: "quality.rule.NEAR_CONSTANT_COLUMN.description",
  },
  HIGH_CARDINALITY: {
    version: "1",
    name: "quality.rule.HIGH_CARDINALITY.name",
    description: "quality.rule.HIGH_CARDINALITY.description",
  },
  POSSIBLE_IDENTIFIER: {
    version: "1",
    name: "quality.rule.POSSIBLE_IDENTIFIER.name",
    description: "quality.rule.POSSIBLE_IDENTIFIER.description",
  },
  MIXED_TYPES: {
    version: "1",
    name: "quality.rule.MIXED_TYPES.name",
    description: "quality.rule.MIXED_TYPES.description",
  },
  NUMERIC_OUTLIERS: {
    version: "1",
    name: "quality.rule.NUMERIC_OUTLIERS.name",
    description: "quality.rule.NUMERIC_OUTLIERS.description",
  },
  NON_FINITE_NUMERIC: {
    version: "1",
    name: "quality.rule.NON_FINITE_NUMERIC.name",
    description: "quality.rule.NON_FINITE_NUMERIC.description",
  },
  INVALID_RANGE: {
    version: "1",
    name: "quality.rule.INVALID_RANGE.name",
    description: "quality.rule.INVALID_RANGE.description",
  },
  INVALID_DATETIME: {
    version: "1",
    name: "quality.rule.INVALID_DATETIME.name",
    description: "quality.rule.INVALID_DATETIME.description",
  },
  DUPLICATE_TIMESTAMPS: {
    version: "1",
    name: "quality.rule.DUPLICATE_TIMESTAMPS.name",
    description: "quality.rule.DUPLICATE_TIMESTAMPS.description",
  },
  NON_MONOTONIC_TIME: {
    version: "1",
    name: "quality.rule.NON_MONOTONIC_TIME.name",
    description: "quality.rule.NON_MONOTONIC_TIME.description",
  },
  IRREGULAR_TIME_INTERVAL: {
    version: "1",
    name: "quality.rule.IRREGULAR_TIME_INTERVAL.name",
    description: "quality.rule.IRREGULAR_TIME_INTERVAL.description",
  },
  OHLC_CONSISTENCY: {
    version: "1",
    name: "quality.rule.OHLC_CONSISTENCY.name",
    description: "quality.rule.OHLC_CONSISTENCY.description",
  },
  NON_POSITIVE_PRICE: {
    version: "1",
    name: "quality.rule.NON_POSITIVE_PRICE.name",
    description: "quality.rule.NON_POSITIVE_PRICE.description",
  },
  NEGATIVE_VOLUME: {
    version: "1",
    name: "quality.rule.NEGATIVE_VOLUME.name",
    description: "quality.rule.NEGATIVE_VOLUME.description",
  },
  DUPLICATE_MARKET_TIMESTAMP: {
    version: "1",
    name: "quality.rule.DUPLICATE_MARKET_TIMESTAMP.name",
    description: "quality.rule.DUPLICATE_MARKET_TIMESTAMP.description",
  },
  MISSING_TIME_PERIODS: {
    version: "1",
    name: "quality.rule.MISSING_TIME_PERIODS.name",
    description: "quality.rule.MISSING_TIME_PERIODS.description",
  },
  EXTREME_RETURN: {
    version: "1",
    name: "quality.rule.EXTREME_RETURN.name",
    description: "quality.rule.EXTREME_RETURN.description",
  },
  ZERO_VOLUME: {
    version: "1",
    name: "quality.rule.ZERO_VOLUME.name",
    description: "quality.rule.ZERO_VOLUME.description",
  },
};
// Only fixed ID/version pairs receive aliases. Saved finding prose is retained separately.
export function qualityRuleName(
  id: string,
  version: string,
  t: AppTranslator,
): string | null {
  const rule = ownedRecordValue(RULES, id);
  return rule && rule.version === version ? t(rule.name) : null;
}
export function qualityRuleDescription(
  id: string,
  version: string,
  t: AppTranslator,
): string | null {
  const rule = ownedRecordValue(RULES, id);
  return rule && rule.version === version ? t(rule.description) : null;
}
