"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useLanguage } from "@/providers/language-provider";
import {
  methodDisplayName,
  methodDisplayDescription,
  methodCategoryLabel,
} from "@/lib/method-labels";
import { Message as UiMessage } from "@/components/message";
import { useEffect, useMemo, useState } from "react";
import { PlayIcon, Loader2Icon, CheckIcon, ArrowUpIcon, ArrowDownIcon, XIcon } from "lucide-react";
import styles from "./statistics-ui.module.css";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { supportsManualStatistics } from "@/lib/statistical-method-support";
import type {
  ColumnProfile,
  MissingPolicy,
  HypothesisAlternative,
  StatisticalMethod,
  StatisticalRunRequest,
} from "@/lib/types";

interface StatisticsConfigProps {
  methods: StatisticalMethod[];
  columns: ColumnProfile[];
  isRunning: boolean;
  onSubmit: (request: StatisticalRunRequest) => void;
  onConfigChange?: (request: StatisticalRunRequest, complete: boolean) => void;
  proposal?: { id: number; config: StatisticalRunRequest } | null;
  compact?: boolean;
}

const NUMERIC_INFERRED = new Set([
  "numeric",
  "integer",
  "float",
  "numeric_continuous",
  "numeric_discrete",
]);

function isNumericColumn(col: ColumnProfile): boolean {
  return (
    NUMERIC_INFERRED.has(col.inferred_type) ||
    col.polars_type.startsWith("Int") ||
    col.polars_type.startsWith("UInt") ||
    col.polars_type.startsWith("Float")
  );
}

export function StatisticsConfigForm({
  methods,
  columns,
  isRunning,
  onSubmit,
  onConfigChange,
  proposal,
  compact = false,
}: StatisticsConfigProps) {
  const { t: statT, locale } = useLanguage();
  const [methodId, setMethodId] = useState<string>(
    methods.find(supportsManualStatistics)?.method_id ??
      methods[0]?.method_id ??
      "descriptive",
  );
  const [variables, setVariables] = useState<string[]>([]);
  const [groupVariable, setGroupVariable] = useState<string>("");
  const [alternative, setAlternative] =
    useState<HypothesisAlternative>("two_sided");
  const [confidenceLevel, setConfidenceLevel] = useState<number>(0.95);
  const [missingPolicy, setMissingPolicy] =
    useState<MissingPolicy>("listwise_delete");
  const [mu, setMu] = useState<number>(0);
  // Regression-specific.
  const [target, setTarget] = useState<string>("");
  const [categoricalVariables, setCategoricalVariables] = useState<string[]>(
    [],
  );
  const [alpha, setAlpha] = useState<number>(1.0);
  const [l1Ratio, setL1Ratio] = useState<number>(0.5);
  const [extra, setExtra] = useState<
    Pick<
      StatisticalRunRequest,
      "expected_proportions" | "reference_categories" | "standardize"
    >
  >({});
  const [proposalNote, setProposalNote] = useState<React.ReactNode>("");
  const [proposalIncompatible, setProposalIncompatible] = useState(false);
  const [handledProposal, setHandledProposal] = useState<number | null>(null);
  useEffect(() => {
    if (!proposal) return;
    setProposalIncompatible(false);
    const spec = proposal.config;
    const proposedMethod = methods.find((m) => m.method_id === spec.method_id);
    if (proposedMethod && !supportsManualStatistics(proposedMethod)) {
      setMethodId(spec.method_id);
      setVariables([]);
      setProposalNote(<UiMessage id="ui.statistics.configureInResearchPlan" />);
      setHandledProposal(proposal.id);
      return;
    }
    const names = new Set(columns.map((c) => c.name));
    const used = [
      ...spec.variables,
      ...(spec.categorical_variables ?? []),
      ...(spec.group_variable ? [spec.group_variable] : []),
      ...(spec.target ? [spec.target] : []),
    ];
    if (
      !methods.some((m) => m.method_id === spec.method_id) ||
      used.some((c) => !names.has(c))
    ) {
      setProposalIncompatible(true);
      setProposalNote(
        <UiMessage id="ui.displayBranch.statistics.proposalIncompatibleMethodsColumns" />,
      );
      setHandledProposal(proposal.id);
      return;
    }
    setMethodId(spec.method_id);
    setVariables(spec.variables);
    setGroupVariable(spec.group_variable ?? "");
    setAlternative(spec.alternative ?? "two_sided");
    setConfidenceLevel(spec.confidence_level ?? 0.95);
    setMissingPolicy(spec.missing_value_policy ?? "listwise_delete");
    setMu(spec.mu ?? 0);
    setTarget(spec.target ?? "");
    setCategoricalVariables(spec.categorical_variables ?? []);
    setAlpha(spec.alpha ?? 1);
    setL1Ratio(spec.l1_ratio ?? 0.5);
    setExtra({
      expected_proportions: spec.expected_proportions,
      reference_categories: spec.reference_categories,
      standardize: spec.standardize,
    });
    setHandledProposal(proposal.id);
    setProposalNote(
      proposal.id === 0 ? (
        <UiMessage id="ui.displayBranch.statistics.savedParametersRestored" />
      ) : (
        <UiMessage id="ui.displayBranch.statistics.proposalReadyForReview" />
      ),
    );
  }, [proposal, columns, methods]);

  const method = useMemo(
    () => methods.find((m) => m.method_id === methodId),
    [methods, methodId],
  );
  const isRegression = method?.category === "regression";
  const isRegularized = [
    "ridge_regression",
    "lasso_regression",
    "elastic_net_regression",
  ].includes(methodId);

  const grouped = useMemo(() => {
    const byCategory: Record<string, StatisticalMethod[]> = {};
    for (const m of methods) {
      (byCategory[m.category] ??= []).push(m);
    }
    return byCategory;
  }, [methods]);

  const eligibleColumns = useMemo(() => {
    if (!method) return columns;
    let cols = method.requires_numeric
      ? columns.filter(isNumericColumn)
      : columns;
    if (isRegression) {
      // Numeric predictors only; categorical predictors chosen separately.
      cols = columns.filter((c) => isNumericColumn(c) && c.name !== target);
    }
    return cols;
  }, [method, columns, isRegression, target]);

  const categoricalOptions = useMemo(
    () =>
      columns.filter(
        (c) =>
          c.name !== target &&
          !variables.includes(c.name) &&
          (!isNumericColumn(c) || c.unique_count <= 20),
      ),
    [columns, target, variables],
  );

  const groupColumns = useMemo(
    () => columns.filter((c) => !isNumericColumn(c) || c.unique_count <= 20),
    [columns],
  );

  const toggleVariable = (name: string) => {
    if (!method) return;
    setVariables((prev) => {
      if (prev.includes(name)) return prev.filter((v) => v !== name);
      if (prev.length >= method.max_variables) {
        // For single/dual-variable methods, replace the selection.
        return method.max_variables === 1 ? [name] : prev;
      }
      return [...prev, name];
    });
  };

  const totalPredictors = isRegression
    ? variables.length + categoricalVariables.length
    : variables.length;
  const isValid =
    !!method &&
    supportsManualStatistics(method) &&
    totalPredictors >= method.min_variables &&
    totalPredictors <= method.max_variables &&
    (!method.needs_group || groupVariable !== "") &&
    (!isRegression || target !== "");

  const draft = useMemo(() => {
    if (
      method &&
      !supportsManualStatistics(method) &&
      proposal?.config.method_id === methodId
    ) {
      return proposal.config;
    }
    const request: StatisticalRunRequest = {
      ...extra,
      method_id: methodId,
      variables,
      alternative,
      confidence_level: confidenceLevel,
      missing_value_policy: missingPolicy,
    };
    if (method?.needs_group || (methodId === "descriptive" && groupVariable)) request.group_variable = groupVariable;
    if (methodId === "one_sample_t") request.mu = mu;
    if (methodId === "paired_t" || methodId === "wilcoxon")
      request.paired = true;
    if (isRegression) {
      request.target = target;
      request.categorical_variables = categoricalVariables;
      if (isRegularized) request.alpha = alpha;
      if (methodId === "elastic_net_regression") request.l1_ratio = l1Ratio;
    }
    return request;
  }, [
    extra,
    methodId,
    variables,
    alternative,
    confidenceLevel,
    missingPolicy,
    method,
    groupVariable,
    mu,
    isRegression,
    target,
    categoricalVariables,
    isRegularized,
    alpha,
    l1Ratio,
    proposal,
  ]);
  useEffect(() => {
    if (proposal && handledProposal !== proposal.id) return;
    onConfigChange?.(draft, isValid);
  }, [draft, isValid, onConfigChange, proposal, handledProposal]);
  const handleSubmit = () => {
    if (isValid) onSubmit(draft);
  };
  if (!method)
    return (
      <p className="text-sm text-muted-foreground">
        <UiMessage id="ui.statistics.noMethodsAvailable" />
      </p>
    );

  const showTestOptions = !["descriptive"].includes(methodId);
  const testOptions = (content: React.ReactNode) => compact ? (
    <details className="space-y-3">
      <summary className="cursor-pointer text-sm"><UiMessage id="ui.quality.advancedSettings" /></summary>
      {content}
    </details>
  ) : content;

  return (
    <Card className={compact ? styles.compactForm : undefined}>
      {!compact && <CardHeader className="pb-3">
        <CardTitle className="text-sm">
          <UiMessage id="ui.statistics.configureAnalysis" />
        </CardTitle>
      </CardHeader>}
      <CardContent className={cn("space-y-4", compact && styles.compactContent)}>
        {!compact && proposalNote && <p role="status">{proposalNote}</p>}
        {compact && proposalIncompatible && <p role="alert">{locale === "zh-CN" ? "保存的参数与当前字段不匹配，请重新选择变量。" : "Saved settings do not match the current columns. Select the variables again."}</p>}
        {/* Method picker */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">
            {" "}
            <UiMessage id="workflow.parameter.method" />{" "}
          </label>
          {compact ? (
            <SearchSelect
              aria-label={statT("workflow.parameter.method")}
              value={supportsManualStatistics(method) ? methodId : ""}
              disabled={isRunning}
              className="workspace-input w-full"
              onChange={(event) => {
                setMethodId(event.target.value);
                setVariables([]);
                setGroupVariable("");
                setTarget("");
                setCategoricalVariables([]);
              }}
            >
              {!supportsManualStatistics(method) && <option value="" disabled><UiMessage id="workflow.parameter.method" /></option>}
              {Object.entries(grouped).map(([category, list]) => {
                const available = list.filter(supportsManualStatistics);
                return available.length ? <optgroup key={category} label={methodCategoryLabel(category, statT)}>
                  {available.map((item) => <option key={item.method_id} value={item.method_id}>{methodDisplayName(item, statT)}</option>)}
                </optgroup> : null;
              })}
            </SearchSelect>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(grouped).map(([category, list]) => (
                <div key={category} className="space-y-1">
                  <p className="font-mono text-[0.6rem] uppercase tracking-wide text-muted-foreground">
                    {methodCategoryLabel(category, statT)}
                  </p>
                  <div className="flex flex-col gap-1">
                    {list.map((m) => {
                      const active = m.method_id === methodId;
                      return (
                        <button
                          key={m.method_id}
                          type="button"
                          onClick={() => {
                            setMethodId(m.method_id);
                            setVariables([]);
                            setGroupVariable("");
                            setTarget("");
                            setCategoricalVariables([]);
                          }}
                          aria-pressed={active}
                          disabled={!supportsManualStatistics(m)}
                          title={methodDisplayDescription(m, statT)}
                          className={cn(
                            "rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:text-muted-foreground",
                            active
                              ? "border-primary bg-primary/8 font-medium text-primary"
                              : "hover:bg-muted",
                          )}
                        >
                          {methodDisplayName(m, statT)}
                          {!supportsManualStatistics(m) && (
                            <small className="block">
                              <UiMessage id="ui.statistics.configureInResearchPlan" />
                            </small>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
          {(!compact || supportsManualStatistics(method)) && <p className="text-xs text-muted-foreground">
            {methodDisplayDescription(method, statT)}
          </p>}
        </div>

        {!compact && methods.some((m) => !supportsManualStatistics(m)) && (
          <p className="text-xs text-muted-foreground">
            <UiMessage id="ui.statistics.boundPredictionPlanRequired" />
          </p>
        )}
        <div hidden={!supportsManualStatistics(method)} className="space-y-4">
          {/* Variable / predictor selection */}
          <div className={compact && (methodId === "paired_t" || methodId === "wilcoxon") ? styles.pairLayout : undefined}>
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              {statT(
                isRegression
                  ? "statistics.variables.besidesTarget"
                  : "statistics.variables.title",
              )}{" "}
              <span className="text-muted-foreground/70">
                {statT(
                  method.requires_numeric || isRegression
                    ? "statistics.variables.selectNumeric"
                    : "statistics.variables.select",
                  {
                    range:
                      method.max_variables > method.min_variables
                        ? `${method.min_variables}–${method.max_variables}`
                        : method.min_variables,
                  },
                )}
              </span>
            </label>
            {eligibleColumns.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                {statT(
                  isRegression && target
                    ? "statistics.variables.noneBesidesTarget"
                    : method.requires_numeric || isRegression
                      ? "statistics.variables.noneNumeric"
                      : "statistics.variables.none",
                )}
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {eligibleColumns.map((col) => {
                  const active = variables.includes(col.name);
                  return (
                    <button
                      key={col.name}
                      type="button"
                      onClick={() => toggleVariable(col.name)}
                      aria-pressed={active}
                      className={cn(
                        styles.variable,
                        active && styles.variableSelected,
                      )}
                    >
                      <span className={styles.check} aria-hidden="true">{active && <CheckIcon size={12} />}</span>
                      {col.name}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          {compact && (methodId === "paired_t" || methodId === "wilcoxon") && (
            <section className={styles.pairOrder} aria-label={locale === "zh-CN" ? "配对顺序" : "Pair order"}>
              <strong>{locale === "zh-CN" ? "配对顺序" : "Pair order"}</strong>
              <p>{locale === "zh-CN" ? "差值按第一项减第二项计算" : "The difference is the first variable minus the second."}</p>
              {variables.length === 0 && <span className={styles.pairEmpty}>{locale === "zh-CN" ? "选择两个字段" : "Select two variables"}</span>}
              <ol>
                {variables.map((name, index) => (
                  <li key={name}>
                    <span className={styles.pairName} title={name}>{index + 1}. {name}</span>
                    <span className={styles.pairActions}>
                      <button type="button" disabled={index === 0 || isRunning} aria-label={locale === "zh-CN" ? `上移 ${name}` : `Move ${name} up`} onClick={() => setVariables(previous => {
                        const next = [...previous];
                        [next[index - 1], next[index]] = [next[index], next[index - 1]];
                        return next;
                      })}><ArrowUpIcon size={14} /></button>
                      <button type="button" disabled={index === variables.length - 1 || isRunning} aria-label={locale === "zh-CN" ? `下移 ${name}` : `Move ${name} down`} onClick={() => setVariables(previous => {
                        const next = [...previous];
                        [next[index], next[index + 1]] = [next[index + 1], next[index]];
                        return next;
                      })}><ArrowDownIcon size={14} /></button>
                      <button type="button" disabled={isRunning} aria-label={locale === "zh-CN" ? `移除 ${name}` : `Remove ${name}`} onClick={() => toggleVariable(name)}><XIcon size={14} /></button>
                    </span>
                  </li>
                ))}
              </ol>
            </section>
          )}
          </div>

          {/* Regression: target, categorical predictors, regularization */}
          {isRegression && (
            <div className="space-y-3 rounded-md border bg-muted/30 p-3">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.statistics.targetOutcomeVariable" />{" "}
                </label>
                <SearchSelect
                  value={target}
                  onChange={(e) => {
                    setTarget(e.target.value);
                    setVariables((prev) =>
                      prev.filter((v) => v !== e.target.value),
                    );
                    setCategoricalVariables((prev) =>
                      prev.filter((v) => v !== e.target.value),
                    );
                  }}
                  className="w-full max-w-xs rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  <option value="">
                    <UiMessage id="ui.statistics.selectATargetColumn" />
                  </option>
                  {columns.map((col) => (
                    <option key={col.name} value={col.name}>
                      {col.name}
                    </option>
                  ))}
                </SearchSelect>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.statistics.categoricalPredictors" />{" "}
                  <span className="text-muted-foreground/70">
                    {" "}
                    <UiMessage id="ui.statistics.oneHotEncodedReferenceLevelDroppedAutomatically" />{" "}
                  </span>
                </label>
                {categoricalOptions.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    {" "}
                    <UiMessage id="ui.ml.noCategoricalColumnsAvailable" />{" "}
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {categoricalOptions.map((col) => {
                      const active = categoricalVariables.includes(col.name);
                      return (
                        <button
                          key={col.name}
                          type="button"
                          onClick={() =>
                            setCategoricalVariables((prev) =>
                              prev.includes(col.name)
                                ? prev.filter((v) => v !== col.name)
                                : [...prev, col.name],
                            )
                          }
                          aria-pressed={active}
                          className={cn(
                            styles.variable,
                            active && styles.variableSelected,
                          )}
                        >
                          <span className={styles.check} aria-hidden="true">{active && <CheckIcon size={12} />}</span>
                          {col.name}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {isRegularized && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">
                      {" "}
                      <UiMessage id="ui.statistics.regularizationAlpha" />{" "}
                    </label>
                    <input
                      type="number"
                      min={0}
                      step={0.1}
                      value={alpha}
                      onChange={(e) => setAlpha(Number(e.target.value))}
                      className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                    />
                  </div>
                  {methodId === "elastic_net_regression" && (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">
                        {" "}
                        <UiMessage id="ui.statistics.l1Ratio0Ridge1Lasso" />{" "}
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={1}
                        step={0.1}
                        value={l1Ratio}
                        onChange={(e) => setL1Ratio(Number(e.target.value))}
                        className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {methodId === "descriptive" && (
            <p className={styles.metrics}>
              {locale === "zh-CN"
                ? "统计指标：有效数、缺失数、均值、标准差、最小值、四分位数、中位数、最大值及均值置信区间。"
                : "Measures: valid and missing counts, mean, standard deviation, minimum, quartiles, median, maximum and mean confidence interval."}
            </p>
          )}

          {/* Grouping variable */}
          {(method.needs_group || methodId === "descriptive") && (
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground">
                {" "}
                <UiMessage id="ui.statistics.groupingVariable" />{" "}
              </label>
              <SearchSelect
                value={groupVariable}
                onChange={(e) => setGroupVariable(e.target.value)}
                className="w-full max-w-xs rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                <option value="">
                  {methodId === "descriptive" ? "不分组" : <UiMessage id="ui.statistics.selectAGroupingColumn" />}
                </option>
                {groupColumns.map((col) => (
                  <option key={col.name} value={col.name}>
                    {col.name}
                  </option>
                ))}
              </SearchSelect>
            </div>
          )}

          {/* Reference value for one-sample t */}
          {methodId === "one_sample_t" && (
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground">
                {" "}
                <UiMessage id="ui.statistics.referenceValue" />{" "}
              </label>
              <input
                type="number"
                value={mu}
                onChange={(e) => setMu(Number(e.target.value))}
                className="w-full max-w-xs rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              />
            </div>
          )}

          {/* Test options */}
          {showTestOptions && testOptions(
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.statistics.alternativeHypothesis" />{" "}
                </label>
                <SearchSelect
                  value={alternative}
                  onChange={(e) =>
                    setAlternative(e.target.value as HypothesisAlternative)
                  }
                  className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  <option value="two_sided">
                    <UiMessage id="ui.statistics.twoSided" />
                  </option>
                  <option value="less">
                    <UiMessage id="ui.statistics.less" />
                  </option>
                  <option value="greater">
                    <UiMessage id="ui.statistics.greater" />
                  </option>
                </SearchSelect>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.report.confidenceLevel" />{" "}
                </label>
                <SearchSelect
                  value={confidenceLevel}
                  onChange={(e) => setConfidenceLevel(Number(e.target.value))}
                  className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  <option value={0.9}>90%</option>
                  <option value={0.95}>95%</option>
                  <option value={0.99}>99%</option>
                </SearchSelect>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.statistics.missingValues" />{" "}
                </label>
                <SearchSelect
                  value={missingPolicy}
                  onChange={(e) =>
                    setMissingPolicy(e.target.value as MissingPolicy)
                  }
                  className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  <option value="listwise_delete">
                    <UiMessage id="ui.statistics.listwiseDeletion" />
                  </option>
                  <option value="pairwise_delete">
                    <UiMessage id="ui.statistics.pairwiseDeletion" />
                  </option>
                  <option value="error_on_missing">
                    <UiMessage id="ui.statistics.errorOnMissing" />
                  </option>
                </SearchSelect>
              </div>
            </div>
          )}

          <div className="flex items-center gap-3 pt-1">
            <Button className={styles.runButton} onClick={handleSubmit} disabled={!isValid || isRunning}>
              {isRunning ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayIcon className="size-4" />
              )}{" "}
              {isRunning ? (locale === "zh-CN" ? "正在运行…" : "Running…") : <UiMessage id="ui.statistics.runAnalysis" />}
            </Button>
            {!isValid && (
              <span className="text-xs text-muted-foreground">
                {isRegression && target === "" ? (
                  <UiMessage id="ui.displayBranch.statistics.targetRequired" />
                ) : method.needs_group && groupVariable === "" ? (
                  <UiMessage id="ui.displayBranch.statistics.groupRequired" />
                ) : (
                  <UiMessage
                    id="ui.statistics.selectValueValuePredictorSToRun"
                    values={{
                      value0: String(method.min_variables),
                      value1: String(method.max_variables),
                    }}
                  />
                )}
              </span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
