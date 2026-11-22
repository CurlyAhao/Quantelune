"use client";

import { useLanguage as useArrayLanguage } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { useEffect, useState } from "react";
import {
  ChevronDownIcon,
  Loader2Icon,
  PlayIcon,
  SlidersHorizontalIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MutationErrorNote, type QualityRunMutation } from "./quality-shared";
import { cn } from "@/lib/utils";
import type { QualityRun } from "@/lib/types";
import type { NoParameterMessageKey } from "@/lib/i18n-messages";

function RatioValidationMessage({
  kind,
  labelKey,
}: {
  kind: "number" | "ratio";
  labelKey: NoParameterMessageKey;
}) {
  const { t } = useArrayLanguage();
  return t(
    kind === "number"
      ? "quality.validation.number"
      : "quality.validation.ratio",
    { label: t(labelKey) },
  );
}

/** The most useful tunables — ratios validated server-side to [0, 1]. */
const _RATIO_FIELDS = [
  {
    key: "high_missingness",
    label: "ui.quality.highMissingness",
    hint: "ui.residual.quality.failMissingRate",
    def: 0.8,
  },
  {
    key: "warning_missingness",
    label: "ui.quality.warningMissingness",
    hint: "ui.residual.quality.warnMissingRate",
    def: 0.3,
  },
  {
    key: "duplicate_row_rate",
    label: "ui.quality.duplicateRowRate",
    hint: "ui.residual.quality.duplicateShareThreshold",
    def: 0.05,
  },
  {
    key: "outlier_ratio",
    label: "ui.quality.outlierRatio",
    hint: "ui.residual.quality.outlierThreshold",
    def: 0.1,
  },
  {
    key: "high_cardinality_ratio",
    label: "ui.quality.highCardinalityRatio",
    hint: "ui.residual.quality.highCardinalityUniqueShare",
    def: 0.9,
  },
  {
    key: "near_constant_ratio",
    label: "ui.quality.nearConstantRatio",
    hint: "ui.residual.quality.nearConstantDominantShare",
    def: 0.95,
  },
] as const;

const DEFAULT_MAX_COLUMNS = 500;

function initialValues(run: QualityRun): Record<string, string> {
  const config = run.threshold_config ?? {};
  const values: Record<string, string> = {};
  for (const field of _RATIO_FIELDS) {
    const stored = config[field.key];
    values[field.key] =
      typeof stored === "number" && Number.isFinite(stored)
        ? String(stored)
        : String(field.def);
  }
  const maxColumns = config.max_columns;
  values.max_columns =
    typeof maxColumns === "number" && Number.isFinite(maxColumns)
      ? String(Math.round(maxColumns))
      : String(DEFAULT_MAX_COLUMNS);
  return values;
}

interface QualityThresholdsProps {
  run: QualityRun;
  datasetId: string;
  runMutation: QualityRunMutation;
}

/** Collapsible threshold configuration for the next quality run. */
export function QualityThresholds({
  run,
  datasetId,
  runMutation,
}: QualityThresholdsProps) {
  const { t: arrayT } = useArrayLanguage();
  const RATIO_FIELDS = _RATIO_FIELDS.map((item) => ({
    ...item,
    labelKey: item.label,
    label: arrayT(item.label),
    hint: arrayT(item.hint),
  }));

  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>(() =>
    initialValues(run),
  );
  const [enableOhlcv, setEnableOhlcv] = useState(run.ohlcv_enabled);
  const [localError, setLocalError] = useState<React.ReactNode>(null);

  // Re-prefill whenever the viewed run changes.
  useEffect(() => {
    setValues(initialValues(run));
    setEnableOhlcv(run.ohlcv_enabled);
  }, [run]);

  function setField(key: string, value: string) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function handleRun() {
    const thresholds: Record<string, number> = {};

    for (const field of RATIO_FIELDS) {
      const raw = values[field.key];
      const num = Number(raw);
      if (raw.trim() === "" || !Number.isFinite(num)) {
        setLocalError(
          <RatioValidationMessage kind="number" labelKey={field.labelKey} />,
        );
        return;
      }
      if (num < 0 || num > 1) {
        setLocalError(
          <RatioValidationMessage kind="ratio" labelKey={field.labelKey} />,
        );
        return;
      }
      thresholds[field.key] = num;
    }

    const maxColumns = Number(values.max_columns);
    if (
      values.max_columns.trim() === "" ||
      !Number.isFinite(maxColumns) ||
      !Number.isInteger(maxColumns) ||
      maxColumns < 1
    ) {
      setLocalError(<UiMessage id="quality.validation.columns" />);
      return;
    }
    thresholds.max_columns = maxColumns;

    setLocalError(null);
    runMutation.mutate({
      datasetId,
      request: { thresholds, enable_ohlcv: enableOhlcv },
    });
  }

  const isPending = runMutation.isPending;

  return (
    <div className="rounded-lg border bg-card">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-controls="quality-advanced-settings"
        className="flex w-full items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <SlidersHorizontalIcon className="size-4 text-primary" />{" "}
        <UiMessage id="ui.quality.advancedSettings" />{" "}
        <span className="hidden text-xs font-normal text-muted-foreground sm:inline">
          {" "}
          <UiMessage id="ui.quality.thresholdsRulePacks" />{" "}
        </span>
        <ChevronDownIcon
          className={cn(
            "ml-auto size-4 text-muted-foreground transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div
          id="quality-advanced-settings"
          className="animate-rise space-y-4 border-t px-4 py-4"
        >
          <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
            {RATIO_FIELDS.map((field) => (
              <div key={field.key} className="space-y-1">
                <Label htmlFor={`th-${field.key}`} className="text-xs">
                  {field.label}
                </Label>
                <Input
                  id={`th-${field.key}`}
                  type="number"
                  inputMode="decimal"
                  min={0}
                  max={1}
                  step={0.05}
                  value={values[field.key]}
                  onChange={(e) => setField(field.key, e.target.value)}
                  className="font-mono text-xs"
                />
                <p className="text-[0.62rem] text-muted-foreground">
                  {field.hint}
                </p>
              </div>
            ))}
            <div className="space-y-1">
              <Label htmlFor="th-max-columns" className="text-xs">
                {" "}
                <UiMessage id="ui.quality.maxColumns" />{" "}
              </Label>
              <Input
                id="th-max-columns"
                type="number"
                inputMode="numeric"
                min={1}
                step={1}
                value={values.max_columns}
                onChange={(e) => setField("max_columns", e.target.value)}
                className="font-mono text-xs"
              />
              <p className="text-[0.62rem] text-muted-foreground">
                {" "}
                <UiMessage id="ui.quality.structureCheckFailsAboveThisColumnCount" />{" "}
              </p>
            </div>
          </div>

          <label className="flex w-fit cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enableOhlcv}
              onChange={(e) => setEnableOhlcv(e.target.checked)}
              className="size-4 accent-primary"
            />{" "}
            <UiMessage id="ui.quality.enableOhlcvCryptoRules" />{" "}
          </label>

          {localError && (
            <p role="alert" className="text-xs font-medium text-destructive">
              {localError}
            </p>
          )}
          <MutationErrorNote mutation={runMutation} />

          <div className="flex justify-end">
            <Button onClick={handleRun} disabled={isPending}>
              {isPending ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayIcon className="size-4" />
              )}{" "}
              <UiMessage id="ui.quality.runWithTheseSettings" />{" "}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
