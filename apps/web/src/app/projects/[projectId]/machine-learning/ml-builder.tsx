"use client";

import { SearchSelect } from "@/components/ui/search-select";
import styles from "./ml-workspace.module.css";

import { useLanguage } from "@/providers/language-provider";
import { workflowEnumLabel } from "@/lib/workflow-labels";
import { Message as UiMessage } from "@/components/message";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Loader2Icon, PlayIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { cn } from "@/lib/utils";
import type {
  ColumnProfile,
  MLExperimentCreateRequest,
  MLMetadata,
  MLPreflightStats,
  MLSplitStrategy,
  MLTaskType,
  MLEditorState,
} from "@/lib/types";

interface MLBuilderProps {
  compact?: boolean;
  columns: ColumnProfile[];
  metadata: MLMetadata;
  isRunning: boolean;
  onCancel?: () => void;
  onSubmit: (request: MLExperimentCreateRequest) => void;
  onConfigChange?: (
    request: MLExperimentCreateRequest,
    complete: boolean,
    editor?: MLEditorState,
  ) => void;
  proposal?: { id: number; config: MLExperimentCreateRequest } | null;
  editor?: MLEditorState | null;
  /** SHA of the currently selected dataset; stats must match this. */
  datasetSha256?: string | null;
  /** Trusted full-data stats for the selected target; null when unavailable. */
  preflightStats?: MLPreflightStats | null;
  /** Emit the real experiment config used for trusted folds statistics. */
  onPreflightQueryChange?: (query: {
    target: string;
    taskType: string;
    splitStrategy: string;
    groupVariable: string | null;
    timeVariable: string | null;
    nSplits: number;
    testSize: number;
    randomSeed: number;
  }) => void;
}

function isNumeric(col: ColumnProfile): boolean {
  return (
    col.inferred_type.startsWith("numeric") ||
    col.polars_type.startsWith("Int") ||
    col.polars_type.startsWith("UInt") ||
    col.polars_type.startsWith("Float")
  );
}

export function MLBuilder({
  columns,
  metadata,
  isRunning,
  onCancel,
  onSubmit,
  onConfigChange,
  proposal,
  editor,
  datasetSha256,
  preflightStats,
  onPreflightQueryChange,
  compact = false,
}: MLBuilderProps) {
  const fractionId = useId();
  const { t: mlT, locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const initial = editor ? {...editor, n_splits: Number(editor.n_splits), random_seed: Number(editor.random_seed), test_size: Number(editor.test_size), feature_selection_k: editor.feature_selection_k ? Number(editor.feature_selection_k) : null, model_params: {}} : proposal?.config;
  const [taskType, setTaskType] = useState<MLTaskType>(initial?.task_type ?? "binary_classification");
  const [target, setTarget] = useState(initial?.target ?? "");
  const [features, setFeatures] = useState<string[]>(() => (initial?.features ?? []).filter(name => !initial?.categorical_features?.includes(name)));
  const [categoricalFeatures, setCategoricalFeatures] = useState<string[]>(initial?.categorical_features ?? []);
  const [splitStrategy, setSplitStrategy] =
    useState<MLSplitStrategy>(initial?.split_strategy ?? "stratified_kfold");
  const [groupVariable, setGroupVariable] = useState(initial?.group_variable ?? "");
  const [timeVariable, setTimeVariable] = useState(initial?.time_variable ?? "");
  const [foldText, setFoldText] = useState(editor?.n_splits ?? String(initial?.n_splits ?? 5));
  const [seedText, setSeedText] = useState(editor?.random_seed ?? String(initial?.random_seed ?? 42));
  const nSplits = Number(foldText), seed = Number(seedText);
  const [modelIds, setModelIds] = useState<string[]>(initial?.model_ids ?? []);
  const [standardize, setStandardize] = useState(initial?.standardize ?? true);
  const [handleOutliers, setHandleOutliers] = useState(initial?.handle_outliers ?? false);
  const [testText, setTestText] = useState(editor?.test_size ?? String(initial?.test_size ?? 0.2));
  const testSize = Number(testText);
  const [selectionText, setSelectionText] = useState(editor?.feature_selection_k ?? (initial?.feature_selection_k == null ? "" : String(initial.feature_selection_k)));
  const [modelParams, setModelParams] = useState(editor?.model_params ?? JSON.stringify(initial?.model_params ?? {}, null, 2));
  const [extra, setExtra] = useState<
    Pick<
      MLExperimentCreateRequest,
      "name" | "val_size" | "impute_numeric" | "impute_categorical"
    >
  >({name: initial?.name, val_size: initial?.val_size, impute_numeric: initial?.impute_numeric, impute_categorical: initial?.impute_categorical});
  const [proposalNote, setProposalNote] = useState<React.ReactNode>("");
  const [proposalIncompatible, setProposalIncompatible] = useState(false);
  const advanced = useRef<HTMLDetailsElement>(null);
  const appliedProposal = useRef<MLBuilderProps["proposal"]>(undefined);
  useEffect(() => {
    if (!proposal || appliedProposal.current === proposal) return;
    const spec = proposal.config;
    const names = new Set(columns.map((c) => c.name));
    const used = [
      ...spec.features,
      ...(spec.categorical_features ?? []),
      spec.target,
      ...(spec.group_variable ? [spec.group_variable] : []),
      ...(spec.time_variable ? [spec.time_variable] : []),
    ];
    if (
      used.some((c) => !names.has(c)) ||
      !metadata.tasks.includes(spec.task_type) ||
      (spec.model_ids ?? []).some(
        (id) =>
          !metadata.models.some(
            (m) => m.model_id === id && m.tasks.includes(spec.task_type),
          ),
      )
    ) {
      setProposalIncompatible(true);
      setProposalNote(
        <UiMessage id="ui.displayBranch.ml.proposalIncompatibleColumnsModels" />,
      );
      return;
    }
    setTaskType(spec.task_type);
    setProposalIncompatible(false);
    appliedProposal.current = proposal;
    setTarget(spec.target);
    setFeatures(spec.features.filter(name => !spec.categorical_features?.includes(name)));
    setCategoricalFeatures(spec.categorical_features ?? []);
    setSplitStrategy(spec.split_strategy ?? "stratified_kfold");
    setGroupVariable(spec.group_variable ?? "");
    setTimeVariable(spec.time_variable ?? "");
    setFoldText(String(spec.n_splits ?? 5));
    setSeedText(String(spec.random_seed ?? 42));
    setModelIds(spec.model_ids ?? []);
    setStandardize(spec.standardize ?? true);
    setHandleOutliers(spec.handle_outliers ?? false);
    setTestText(String(spec.test_size ?? 0.2));
    setSelectionText(spec.feature_selection_k == null ? "" : String(spec.feature_selection_k));
    setModelParams(JSON.stringify(spec.model_params ?? {}, null, 2));
    setExtra({
      name: spec.name,
      val_size: spec.val_size,
      impute_numeric: spec.impute_numeric,
      impute_categorical: spec.impute_categorical,
    });
    setProposalNote(locale === "zh-CN" ? "参数已填入" : "Parameters loaded");
  }, [proposal, columns, metadata, locale]);

  const numericColumns = useMemo(
    () => columns.filter((c) => isNumeric(c) && c.name !== target),
    [columns, target],
  );
  const categoricalColumns = useMemo(
    () =>
      columns.filter(
        (c) =>
          c.name !== target &&
          !features.includes(c.name) &&
          (!isNumeric(c) || c.unique_count <= 20),
      ),
    [columns, target, features],
  );
  const availableModels = useMemo(
    () => metadata.models.filter((m) => m.tasks.includes(taskType)),
    [metadata, taskType],
  );

  const toggle = (list: string[], set: (v: string[]) => void, name: string) =>
    set(list.includes(name) ? list.filter((v) => v !== name) : [...list, name]);

  const regressionTask = taskType === "regression";
  const effectiveStrategy =
    regressionTask && splitStrategy === "stratified_kfold"
      ? "kfold"
      : splitStrategy;
  const needsGroup = effectiveStrategy === "group_kfold";
  const needsTime = effectiveStrategy === "time_series_split";

  // Keep folds statistics aligned with the real request config.
  useEffect(() => {
    if (!onPreflightQueryChange || !/^\d+$/.test(seedText) || !/^\d+$/.test(foldText) || !Number.isFinite(testSize) || testText.trim() === "") return;
    onPreflightQueryChange({
      target,
      taskType,
      splitStrategy: effectiveStrategy,
      groupVariable: needsGroup && groupVariable ? groupVariable : null,
      timeVariable: needsTime && timeVariable ? timeVariable : null,
      nSplits,
      testSize,
      randomSeed: seed,
    });
  }, [
    onPreflightQueryChange,
    target,
    taskType,
    effectiveStrategy,
    needsGroup,
    groupVariable,
    needsTime,
    timeVariable,
    nSplits,
    seed,
    testSize,
    seedText, foldText, testText,
  ]);

  // Folds suggestion only from trusted stats matching the live request config
  // and the current dataset SHA (never a stale response from another dataset).
  const statsMatchConfig =
    preflightStats != null &&
    datasetSha256 != null &&
    preflightStats.dataset_sha256 === datasetSha256 &&
    preflightStats.target === target &&
    preflightStats.task_type === taskType &&
    preflightStats.split_strategy === effectiveStrategy &&
    (preflightStats.group_variable ?? null) ===
      (needsGroup && groupVariable ? groupVariable : null) &&
    (preflightStats.time_variable ?? null) ===
      (needsTime && timeVariable ? timeVariable : null) &&
    preflightStats.n_splits === nSplits &&
    preflightStats.random_seed === seed &&
    preflightStats.test_size != null &&
    Math.abs(preflightStats.test_size - testSize) < 1e-9;
  const timeOrderOk =
    !needsTime || preflightStats?.time_is_sorted_ascending !== false;
  const trustedFoldsSuggestion =
    preflightStats?.trusted &&
    statsMatchConfig &&
    timeOrderOk &&
    preflightStats.suggested_n_splits != null &&
    (taskType === "regression" || (preflightStats.n_classes ?? 0) >= 2)
      ? preflightStats.suggested_n_splits
      : null;

  const parsedModelParams = useMemo(() => {
    try {
      const parsed = JSON.parse(modelParams);
      const finite = (value: unknown): boolean => typeof value === "number" ? Number.isFinite(value) : value !== null && typeof value === "object" ? Object.values(value).every(finite) : true;
      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed) || !finite(parsed) ||
        Object.values(parsed).some(
          (value) =>
            !value || typeof value !== "object" || Array.isArray(value),
        )
      )
        return null;
      return parsed as Record<string, Record<string, unknown>>;
    } catch {
      return null;
    }
  }, [modelParams]);
  const isValid =
    /^\d+$/.test(seedText) && /^\d+$/.test(foldText) && testText.trim() !== "" &&
    (selectionText === "" || (/^\d+$/.test(selectionText) && Number(selectionText) >= 1 && Number(selectionText) <= 512)) &&
    Number.isInteger(seed) && seed >= 0 && seed <= 2 ** 32 - 1 &&
    Number.isInteger(nSplits) && nSplits >= 2 && nSplits <= 10 &&
    Number.isFinite(testSize) && testSize > 0 && testSize < 0.9 &&
    parsedModelParams !== null &&
    target !== "" &&
    features.length + categoricalFeatures.length >= 1 &&
    modelIds.length >= 1 &&
    modelIds.length <= 6 && features.length + categoricalFeatures.length <= 128 &&
    (!needsGroup || groupVariable !== "") &&
    (!needsTime || timeVariable !== "");

  const draft = useMemo<MLExperimentCreateRequest>(() => {
    // Feature contract: features is the stable deduped union of plain and
    // categorical features; categorical_features is always its subset.
    const mergedFeatures = [...new Set([...features, ...categoricalFeatures])];
    return {
      ...extra,
      name:
        extra.name ??
        mlT("statistics.defaultExperiment", {
          task: workflowEnumLabel(taskType, mlT),
        }),
      task_type: taskType,
      target,
      features: mergedFeatures,
      categorical_features: categoricalFeatures,
      group_variable: needsGroup ? groupVariable : null,
      time_variable: needsTime ? timeVariable : null,
      split_strategy: effectiveStrategy,
      n_splits: nSplits,
      random_seed: seed,
      model_ids: modelIds,
      model_params: Object.fromEntries(
        Object.entries(parsedModelParams ?? {}).filter(([key]) =>
          modelIds.includes(key),
        ),
      ),
      standardize,
      handle_outliers: handleOutliers,
      test_size: testSize,
      feature_selection_k: selectionText === "" ? null : Number(selectionText),
    };
  }, [
    extra,
    mlT,
    taskType,
    target,
    features,
    categoricalFeatures,
    needsGroup,
    groupVariable,
    needsTime,
    timeVariable,
    effectiveStrategy,
    nSplits,
    seed,
    modelIds,
    standardize,
    handleOutliers,
    testSize,
    parsedModelParams,
    selectionText,
  ]);
  const editorState = useMemo<MLEditorState>(() => ({
    name: draft.name || "模型实验", task_type: taskType, target,
    features: draft.features, categorical_features: categoricalFeatures,
    group_variable: groupVariable || null, time_variable: timeVariable || null,
    split_strategy: splitStrategy, n_splits: foldText, random_seed: seedText, test_size: testText,
    feature_selection_k: selectionText, val_size: extra.val_size ?? 0.15,
    model_ids: modelIds, standardize, handle_outliers: handleOutliers,
    impute_numeric: extra.impute_numeric ?? "median", impute_categorical: extra.impute_categorical ?? "most_frequent",
    model_params: modelParams,
  }), [draft.name, draft.features, taskType, target, categoricalFeatures, groupVariable, timeVariable, splitStrategy, foldText, seedText, testText, selectionText, extra, modelIds, standardize, handleOutliers, modelParams]);
  useEffect(() => {
    onConfigChange?.(draft, isValid, editorState);
  }, [draft, isValid, editorState, onConfigChange]);
  const handleSubmit = () => {
    if (isValid) onSubmit(draft);
  };

  const displayedNumericColumns = compact ? numericColumns.filter((column) => !categoricalFeatures.includes(column.name)) : numericColumns;
  const categoricalSection = (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">
        {" "}
        <UiMessage id="ui.ml.categoricalFeatures" />{" "}
        {compact && categoricalFeatures.length > 0 && <span> · {copy(`已选 ${categoricalFeatures.length}`, `${categoricalFeatures.length} selected`)}</span>}
        <span className="text-muted-foreground/70">
          <UiMessage id="ui.ml.oneHotEncoded" />
        </span>
      </label>
      {categoricalColumns.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {" "}
          <UiMessage id="ui.ml.noCategoricalColumnsAvailable" />{" "}
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {categoricalColumns.map((c) => (
            <button
              key={c.name}
              type="button"
              onClick={() =>
                toggle(categoricalFeatures, setCategoricalFeatures, c.name)
              }
              aria-pressed={categoricalFeatures.includes(c.name)}
              className={cn(
                "rounded-md border px-2 py-1 font-mono text-xs transition-colors",
                categoricalFeatures.includes(c.name)
                  ? "border-primary bg-primary/10 text-primary"
                  : "hover:bg-muted",
              )}
            >
              {c.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
  const validationSection = (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground">
          {" "}
          <UiMessage id="ui.ml.validation" />{" "}
        </label>
        <SearchSelect
          value={effectiveStrategy}
          onChange={(e) =>
            setSplitStrategy(e.target.value as MLSplitStrategy)
          }
          className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          {metadata.split_strategies.map((s) => (
            <option key={s} value={s}>
              {workflowEnumLabel(s, mlT)}
            </option>
          ))}
        </SearchSelect>
      </div>
      {needsGroup ? (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.groupVariable" />{" "}
            <span className="text-destructive">
              {" "}
              <UiMessage id="ui.ml.requiredForGroupKFold" />{" "}
            </span>
          </label>
          <SearchSelect
            value={groupVariable}
            onChange={(e) => setGroupVariable(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <option value="">
              <UiMessage id="ui.ml.selectAGroupColumn" />
            </option>
            {columns
              .filter((c) => c.name !== target)
              .map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
          </SearchSelect>
          <p className="text-xs text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.statforgeWillNotFallBackToPlainKFoldIfTheGroupColumnIsMissing" />{" "}
          </p>
        </div>
      ) : null}
      {needsTime ? (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.timeVariable" />{" "}
            <span className="text-destructive">
              {" "}
              <UiMessage id="ui.ml.requiredForTimeSeriesSplit" />{" "}
            </span>
          </label>
          <SearchSelect
            value={timeVariable}
            onChange={(e) => setTimeVariable(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <option value="">
              <UiMessage id="ui.ml.selectATimeColumn" />
            </option>
            {columns
              .filter((c) => c.name !== target)
              .map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
          </SearchSelect>
          <p className="text-xs text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.rowsMustBeSortedAscendingByThisColumnUnsortedDataIsRejected" />{" "}
          </p>
        </div>
      ) : null}
      {!["train_test", "train_val_test"].includes(effectiveStrategy) && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.folds" />{" "}
          </label>
          <input
            type="text"
            inputMode="numeric"
            aria-label={copy("折数", "Folds")}
            min={2}
            max={10}
            value={foldText}
            maxLength={40}
            onChange={(e) => setFoldText(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          />
          {trustedFoldsSuggestion != null ? (
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.ml.suggestedFoldsFromTrainingSubsetTargetStatistics" />{" "}
              <span className="font-mono text-foreground">
                {trustedFoldsSuggestion}
              </span>
              {preflightStats?.min_class_count != null ? (
                <UiMessage
                  id="ui.ml.minClassCountAfterHoldoutValue"
                  values={{
                    value0: String(preflightStats.min_class_count),
                  }}
                />
              ) : (
                ""
              )}{" "}
            </p>
          ) : target !== "" ? (
            <p className="text-xs text-amber-700">
              {needsTime &&
              preflightStats?.time_is_sorted_ascending === false ? (
                <UiMessage id="ui.displayBranch.ml.foldsTimeOrderRejected" />
              ) : preflightStats?.trusted === false &&
                preflightStats.trust_reason ? (
                <UiMessage
                  id="ui.ml.noTrustedFoldsSuggestionValue"
                  values={{ value0: String(preflightStats.trust_reason) }}
                />
              ) : needsTime && timeVariable === "" ? (
                <UiMessage id="ui.displayBranch.ml.foldsTimeRequired" />
              ) : needsGroup && groupVariable === "" ? (
                <UiMessage id="ui.displayBranch.ml.foldsGroupRequired" />
              ) : (
                <UiMessage id="ui.displayBranch.ml.foldsStatsUnavailable" />
              )}
            </p>
          ) : null}
        </div>
      )}
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground">
          {" "}
          <UiMessage id="ui.ml.randomSeed" />{" "}
        </label>
        <input
          type="text"
          inputMode="numeric"
          aria-label={copy("随机种子", "Random seed")}
          value={seedText}
          maxLength={40}
          onChange={(e) => setSeedText(e.target.value)}
          className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground" htmlFor={fractionId}>
          {copy("测试集比例", "Test fraction")}
        </label>
        <input id={fractionId} type="text" inputMode="decimal" value={testText} maxLength={40}
          aria-label={copy("测试集比例", "Test fraction")}
          onChange={event => setTestText(event.target.value)}
          className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"/>
      </div>
      <div className="flex items-end gap-4 pb-1.5">
        <label className="flex items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            checked={standardize}
            onChange={(e) => setStandardize(e.target.checked)}
          />{" "}
          <UiMessage id="ui.ml.scale" />{" "}
        </label>
        <label className="flex items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            checked={handleOutliers}
            onChange={(e) => setHandleOutliers(e.target.checked)}
          />{" "}
          <UiMessage id="ui.ml.winsorize" />{" "}
        </label>
      </div>
    </div>
  );
  const modelOptionFields = (
    <>
      <label className="mt-3 block text-sm">
        {copy("保留特征数（编码后）", "Features to retain after encoding")}
        <input type="text" inputMode="numeric" value={selectionText} maxLength={40}
          aria-label={copy("保留特征数", "Features to retain")} placeholder={copy("全部", "All")}
          onChange={event => setSelectionText(event.target.value)}
          className="mt-2 w-full rounded-md border bg-background px-3 py-2"/>
      </label>
      <label className="mt-3 block text-sm">
        <span className="sr-only">
          {copy("模型参数 JSON", "Model parameters JSON")}
        </span>
        <textarea
          aria-label={copy("模型参数 JSON", "Model parameters JSON")}
          className="mt-2 min-h-32 w-full rounded-md border bg-background p-3 font-mono text-sm"
          maxLength={16000}
          spellCheck={false}
          value={modelParams}
          onChange={(event) => setModelParams(event.target.value)}
        />
      </label>
      {parsedModelParams === null && (
        <p role="alert" className="text-sm text-destructive">
          {copy(
            "请按模型名称填写有效的 JSON 参数对象。",
            "Enter a valid JSON parameter object keyed by model name.",
          )}
        </p>
      )}
    </>
  );
  const modelOptions = (
    <details>
      <summary className="cursor-pointer text-sm">
        {copy("模型参数", "Model parameters")}
      </summary>
      {modelOptionFields}
    </details>
  );

  return (
    <Card className={compact ? styles.builder : undefined}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">
          <UiMessage id="ui.ml.configureExperiment" />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {!compact && proposalNote && <p role="status">{proposalNote}</p>}
        {compact && proposalIncompatible && <p role="alert">{copy("保存的参数与当前字段或模型不匹配，请重新选择。", "Saved settings do not match the current columns or models. Select them again.")}</p>}
        {!compact && proposal && (
          <details>
            <summary>{copy("其他参数", "Other parameters")}</summary>
            <pre className="whitespace-pre-wrap text-xs">
              {JSON.stringify({ ...extra, test_size: testSize }, null, 2)}
            </pre>
          </details>
        )}
        <div className={compact ? styles.taskFields : undefined}>
        {/* Task type */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="ui.projects.taskType" />{" "}
          </label>
          <div className="flex flex-wrap gap-2">
            {metadata.tasks.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => {
                  setTaskType(t as MLTaskType);
                  setModelIds([]);
                  setModelParams("{}");
                }}
                aria-pressed={taskType === t}
                className={cn(
                  "rounded-md border px-2.5 py-1.5 text-xs transition-colors",
                  taskType === t
                    ? "border-primary bg-primary/8 font-medium text-primary"
                    : "hover:bg-muted",
                )}
              >
                {workflowEnumLabel(t, mlT)}
              </button>
            ))}
          </div>
        </div>

        {/* Target */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.targetVariable" />{" "}
          </label>
          <SearchSelect
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              setFeatures((p) => p.filter((v) => v !== e.target.value));
              setCategoricalFeatures((p) =>
                p.filter((v) => v !== e.target.value),
              );
            }}
            className="w-full max-w-xs rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <option value="">
              <UiMessage id="ui.ml.selectATarget" />
            </option>
            {columns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.inferred_type})
              </option>
            ))}
          </SearchSelect>
        </div>

        </div>
        <section className={compact ? styles.featureSection : undefined} aria-label={copy("输入特征", "Input features")}>
          {compact && <h3>{copy("输入特征", "Input features")}</h3>}
          <div className={compact ? styles.featureGroups : undefined}>
        {/* Numeric features */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.numericFeatures" />{" "}
            {compact && features.length > 0 && <span> · {copy(`已选 ${features.length}`, `${features.length} selected`)}</span>}
          </label>
          {displayedNumericColumns.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {" "}
              <UiMessage id="ui.ml.noNumericColumnsAvailable" />{" "}
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {displayedNumericColumns.map((c) => (
                <button
                  key={c.name}
                  type="button"
                  onClick={() => toggle(features, setFeatures, c.name)}
                  aria-pressed={features.includes(c.name)}
                  className={cn(
                    "rounded-md border px-2 py-1 font-mono text-xs transition-colors",
                    features.includes(c.name)
                      ? "border-primary bg-primary/10 text-primary"
                      : "hover:bg-muted",
                  )}
                >
                  {c.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {categoricalSection}
          </div>
        </section>
        <section className={compact ? styles.modelSection : undefined} aria-label={copy("模型与验证", "Models and validation")}>
          {compact && <h3>{copy("模型与验证", "Models and validation")}</h3>}
        {/* Models */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="ui.ml.modelsCompareCandidates" />{" "}
          </label>
          <div className="flex flex-wrap gap-1.5">
            {availableModels.map((m) => (
              <button
                key={m.model_id}
                type="button"
                onClick={() => toggle(modelIds, setModelIds, m.model_id)}
                aria-pressed={modelIds.includes(m.model_id)}
                className={cn(
                  "rounded-md border px-2 py-1 text-xs transition-colors",
                  modelIds.includes(m.model_id)
                    ? "border-primary bg-primary/10 text-primary"
                    : "hover:bg-muted",
                )}
              >
                {workflowEnumLabel(m.model_id, mlT)}
                {m.interpretable && (
                  <span className="ml-1 text-[0.6rem] opacity-60">
                    {" "}
                    <UiMessage id="ui.ml.interp" />{" "}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        {validationSection}
        </section>
        {compact ? <details ref={advanced} className={styles.advanced}>
          <summary>{copy("模型参数及其他设置", "Model parameters and other settings")}</summary>
          <div className="space-y-4 pt-3">{modelOptionFields}</div>
        </details> : modelOptions}
        <div className="flex items-center gap-3 pt-1">
          <Button onClick={handleSubmit} disabled={!isValid || isRunning}>
            {isRunning ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <PlayIcon className="size-4" />
            )}{" "}
            <UiMessage id="ui.ml.trainEvaluate" />{" "}
          </Button>
          {isRunning && onCancel && (
            <Button variant="outline" onClick={onCancel}>
              {" "}
              <UiMessage id="ui.ml.requestCancellation" />{" "}
            </Button>
          )}
          {!isValid && (
            <span className="text-xs text-muted-foreground">
              {" "}
              {compact && target && features.length + categoricalFeatures.length >= 1 && modelIds.length >= 1 ? <button type="button" className="underline" onClick={() => {
                if (advanced.current) {
                  advanced.current.open = true;
                  advanced.current.scrollIntoView({ block: "nearest" });
                }
              }}>{copy("请检查划分和模型参数。", "Check the split and model parameters.")}</button> : <UiMessage id="ui.ml.selectATargetAtLeastOneFeatureAndAtLeastOneModel" />}{" "}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
