import type { StatisticalRunRequest } from "./types";

function ordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(ordered);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => [k, ordered(v)]),
    );
  }
  return value;
}

/** Saved Run input uses missing_policy; forms use missing_value_policy. */
export function statisticalFingerprint(
  value: StatisticalRunRequest | Record<string, unknown> | null | undefined,
): string {
  if (!value) return "";
  const v = value as Record<string, unknown>;
  return JSON.stringify([
    v.method_id,
    v.variables ?? [],
    v.group_variable ?? null,
    v.paired ?? false,
    v.mu ?? 0,
    v.expected_proportions ?? null,
    v.alternative ?? "two_sided",
    v.confidence_level ?? 0.95,
    v.missing_value_policy ?? v.missing_policy ?? "listwise_delete",
    v.target ?? null,
    v.categorical_variables ?? [],
    v.reference_categories ?? null,
    v.standardize ?? false,
    v.alpha ?? 1,
    v.l1_ratio ?? 0.5,
    ...(Object.prototype.hasOwnProperty.call(v, "evaluation")
      ? [ordered(v.evaluation)]
      : []),
  ]);
}
