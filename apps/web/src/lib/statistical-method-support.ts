import type { StatisticalMethod } from "./types";

const BOUND_EVALUATIONS = new Set([
  "evaluate_predictions",
  "evaluate_binary_predictions",
]);

/** These methods require source-bound Plan configuration, not the legacy form. */
export function supportsManualStatistics(method: StatisticalMethod): boolean {
  return (
    method.manual_configuration_supported !== false &&
    !BOUND_EVALUATIONS.has(method.method_id)
  );
}
