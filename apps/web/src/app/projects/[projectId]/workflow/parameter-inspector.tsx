"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useLanguage } from "@/providers/language-provider";
import {
  workflowParameterLabel,
  workflowEnumLabel,
  workflowParameterDescription,
} from "@/lib/workflow-labels";
import { Message as UiMessage } from "@/components/message";
import type {
  Dataset,
  WorkflowCatalogNode,
  WorkflowParameterSchema,
  WorkflowParameterValue,
} from "@/lib/types";

const fieldClass =
  "w-full min-w-0 rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";
const inheritedModelFields = new Set([
  "model_id",
  "task_type",
  "target",
  "test_size",
  "val_size",
  "seed",
  "n_splits",
  "split_strategy",
  "group_variable",
  "time_variable",
  "standardize",
  "handle_outliers",
  "impute_numeric",
  "impute_categorical",
]);

export function resolvedParameterSchema(
  schema: WorkflowParameterSchema,
): WorkflowParameterSchema {
  const branch = schema.anyOf?.find((part) => part.type !== "null");
  return branch ? { ...schema, ...branch } : schema;
}

export function ParameterInspector({
  spec,
  parameters,
  columns,
  datasets,
  disabled,
  onChange,
}: {
  spec: WorkflowCatalogNode;
  parameters: Record<string, WorkflowParameterValue>;
  columns: Array<{ name: string; is_numeric?: boolean }>;
  datasets: Dataset[];
  disabled: boolean;
  onChange: (key: string, value: WorkflowParameterValue | undefined) => void;
}) {
  const { t } = useLanguage();
  const schemas: Record<string, WorkflowParameterSchema> =
    spec.parameter_schema?.properties ??
    Object.fromEntries(spec.params.map((key) => [key, { type: "string" }]));
  return (
    <fieldset disabled={disabled} className="min-w-0 space-y-4">
      {Object.entries(schemas).map(([key, original]) => {
        const baseSchema = resolvedParameterSchema(original);
        const numericFilter =
          spec.node_type === "filter" &&
          key === "value" &&
          (["gt", "lt"].includes(String(parameters.operator)) ||
            columns.find((column) => column.name === parameters.column)
              ?.is_numeric);
        const schema = numericFilter
          ? { ...baseSchema, type: "number" }
          : baseSchema;
        const inherit =
          ((spec.node_type === "model" ||
            spec.node_type === "cross_validation") &&
            inheritedModelFields.has(key)) ||
          original.anyOf?.some((item) => item.type === "null");
        const raw = parameters[key];
        const value =
          raw ?? (inherit ? "" : (original.default ?? schema.default ?? ""));
        const label = workflowParameterLabel(
          spec.node_type,
          key,
          t,
          schema.title ?? key,
        );
        const id = `workflow-param-${key}`;
        const columnField =
          schema["x-ui"]?.includes("column") ||
          [
            "columns",
            "variables",
            "predictors",
            "features",
            "categorical",
            "column",
            "variable",
            "group",
            "target",
            "group_variable",
            "time_variable",
          ].includes(key);
        const choices =
          schema["x-column-kind"] === "numeric"
            ? columns.filter((column) => column.is_numeric !== false)
            : columns;
        let control;
        if (key === "dataset_id") {
          control = (
            <SearchSelect
              id={id}
              className={fieldClass}
              value={String(value)}
              onChange={(event) =>
                onChange(key, event.target.value || undefined)
              }
            >
              <option value="">
                <UiMessage id="ui.workflow.useWorkflowDataset" />
              </option>
              {datasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.original_filename}
                </option>
              ))}
            </SearchSelect>
          );
        } else if (columnField && schema.type === "array") {
          const selected = Array.isArray(value)
            ? value.map(String)
            : typeof value === "string"
              ? value
                  .split(",")
                  .map((item) => item.trim())
                  .filter(Boolean)
              : [];
          control = (
            <div
              id={id}
              role="group"
              aria-label={label}
              className="max-h-44 space-y-1 overflow-y-auto rounded-md border p-2"
            >
              {choices.length ? (
                choices.map((column) => (
                  <label
                    key={column.name}
                    className="flex min-h-8 cursor-pointer items-center gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(column.name)}
                      onChange={(event) =>
                        onChange(
                          key,
                          event.target.checked
                            ? [...selected, column.name]
                            : selected.filter((name) => name !== column.name),
                        )
                      }
                      className="size-4 accent-primary"
                    />
                    {column.name}
                  </label>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.workflow.selectADatasetToChooseActualColumns" />{" "}
                </p>
              )}
            </div>
          );
        } else if (columnField) {
          control = (
            <SearchSelect
              id={id}
              className={fieldClass}
              value={String(value)}
              onChange={(event) =>
                onChange(key, event.target.value || undefined)
              }
            >
              <option value="">
                {inherit ? (
                  <UiMessage id="ui.workflow.inheritOrUseTaskDefault" />
                ) : (
                  <UiMessage id="ui.displayBranch.workflow.chooseColumn" />
                )}
              </option>
              {choices.map((column) => (
                <option key={column.name} value={column.name}>
                  {column.name}
                </option>
              ))}
            </SearchSelect>
          );
        } else if (schema.enum || (schema.type === "boolean" && inherit)) {
          const options = schema.enum ?? [true, false];
          control = (
            <SearchSelect
              id={id}
              className={fieldClass}
              value={String(value)}
              onChange={(event) =>
                onChange(
                  key,
                  event.target.value === ""
                    ? undefined
                    : schema.type === "boolean"
                      ? event.target.value === "true"
                      : event.target.value,
                )
              }
            >
              {inherit && (
                <option value="">
                  <UiMessage id="ui.workflow.inheritOrUseTaskDefault" />
                </option>
              )}
              {options.map((option) => (
                <option key={String(option)} value={String(option)}>
                  {workflowEnumLabel(option, t)}
                </option>
              ))}
            </SearchSelect>
          );
        } else if (schema.type === "boolean") {
          control = (
            <label className="flex min-h-9 items-center gap-2 text-sm">
              <input
                id={id}
                type="checkbox"
                className="size-4 accent-primary"
                checked={value === true || value === "true"}
                onChange={(event) => onChange(key, event.target.checked)}
              />{" "}
              <UiMessage id="ui.workflow.enabled" />{" "}
            </label>
          );
        } else if (schema.type === "integer" || schema.type === "number") {
          control = (
            <input
              id={id}
              type="number"
              className={fieldClass}
              value={
                typeof value === "number" || typeof value === "string"
                  ? value
                  : ""
              }
              min={schema.minimum}
              max={schema.maximum}
              step={schema.type === "integer" ? 1 : "any"}
              placeholder={
                inherit ? t("ui.workflow.inheritOrUseTaskDefault") : undefined
              }
              onChange={(event) =>
                onChange(
                  key,
                  event.target.value === ""
                    ? undefined
                    : Number(event.target.value),
                )
              }
            />
          );
        } else if (schema.type === "object") {
          const text =
            typeof value === "string"
              ? value
              : JSON.stringify(value || {}, null, 2);
          control = (
            <>
              <textarea
                id={id}
                rows={5}
                className={fieldClass + " font-mono text-xs"}
                value={text}
                onChange={(event) => {
                  try {
                    const parsed: WorkflowParameterValue = JSON.parse(
                      event.target.value,
                    );
                    onChange(key, parsed);
                  } catch {
                    onChange(key, event.target.value);
                  }
                }}
              />
              <p className="text-xs text-muted-foreground">
                {" "}
                <UiMessage id="ui.workflow.modelSettingsAsAJsonObjectInvalidValuesBlockValidationAndExecution" />{" "}
              </p>
            </>
          );
        } else {
          control = (
            <input
              id={id}
              className={fieldClass}
              value={
                typeof value === "string" || typeof value === "number"
                  ? value
                  : ""
              }
              maxLength={schema.maxLength}
              onChange={(event) =>
                onChange(key, event.target.value || undefined)
              }
            />
          );
        }
        return (
          <div key={key} className="space-y-1.5">
            <label htmlFor={id} className="block text-sm font-medium">
              {label}
              {spec.parameter_schema?.required?.includes(key) && (
                <span className="ml-1 text-muted-foreground">
                  <UiMessage id="ui.workflow.required" />
                </span>
              )}
            </label>
            {control}
            {schema.description && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {workflowParameterDescription(
                  spec.node_type,
                  key,
                  schema.description,
                  t,
                )}
              </p>
            )}
            {(schema.exclusiveMinimum != null ||
              schema.exclusiveMaximum != null) && (
              <p className="text-xs text-muted-foreground">
                {schema.exclusiveMinimum != null &&
                  t("workflow.parameter.greaterThan", {
                    bound: schema.exclusiveMinimum,
                  })}{" "}
                {schema.exclusiveMaximum != null &&
                  t("workflow.parameter.lessThan", {
                    bound: schema.exclusiveMaximum,
                  })}
              </p>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}
