"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { Message as UiMessage } from "@/components/message";
import { useLanguage } from "@/providers/language-provider";
import { derivedDisplayTitle } from "@/lib/derived-display-title";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { DesktopLink } from "./desktop-link";
import { WorkspaceTemplateEditor } from "./workspace-template-editor";
import { apiFetch } from "@/lib/api-client";
import { getActorEpoch } from "@/lib/auth-session";
import {
  sessionSourceKey,
  type PreviewContext,
  type PreviewScope,
} from "@/lib/preview-types";
import {
  workspaceApi,
  type Execution,
  type PlanBody,
  type PlanStep,
  type TemplateBody,
  type WorkspaceRecord,
  type WorkspaceSummary,
} from "@/lib/workspace-api";

export function WorkspacePlanPanel({
  scope,
  identity,
  refresh,
}: {
  scope: PreviewScope;
  identity: string;
  refresh: number;
}) {
  const { t } = useLanguage();
  const [items, setItems] = useState<WorkspaceSummary[]>([]);
  const [plan, setPlan] = useState<WorkspaceRecord<PlanBody> | null>(null);
  const [text, setText] = useState(""),
    [title, setTitle] = useState(() => t("workspace.defaultPlanTitle"));
  const [dirty, setDirty] = useState(false),
    [approved, setApproved] = useState(false);
  const [execution, setExecution] = useState<Execution | null>(null),
    [requestId, setRequestId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<React.ReactNode>(""),
    [templateId, setTemplateId] = useState("");
  const generation = useRef(0),
    scopeRef = useRef(scope);
  scopeRef.current = scope;
  const binding = sessionSourceKey(scope);
  const ticket = () => {
    const value = generation.current,
      actor = getActorEpoch();
    return () => value === generation.current && actor === getActorEpoch();
  };
  function show(row: WorkspaceRecord<PlanBody>) {
    setPlan(row);
    setTitle(row.payload.title);
    setText(JSON.stringify(row.payload.steps, null, 2));
    setDirty(false);
    setApproved(false);
    setRequestId(null);
    setExecution(null);
  }
  async function lists(signal?: AbortSignal) {
    const result = await workspaceApi.list(
      scopeRef.current.project_id,
      undefined,
      signal,
    );
    if (!signal?.aborted) setItems(result.filter((x) => x.available));
  }
  const invalidate = useCallback(() => {
    generation.current++;
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    generation.current++;
    setItems([]);
    setBusy(false);
    setPlan(null);
    setText("");
    setExecution(null);
    setApproved(false);
    setError("");
    void lists(controller.signal).catch(() => {
      if (!controller.signal.aborted)
        setError(<UiMessage id="ui.residual.preview.planListUnavailable" />);
    });
    return () => {
      invalidate();
      controller.abort();
    };
  }, [binding, identity, refresh, invalidate]);
  function edit(value: string) {
    setText(value);
    setDirty(true);
    setApproved(false);
  }
  async function action(job: (current: () => boolean) => Promise<void>) {
    const current = ticket();
    setBusy(true);
    setError("");
    try {
      await job(current);
    } catch {
      if (current())
        setError(
          <UiMessage id="ui.residual.workspace.operationIncompleteCheckSavedState" />,
        );
    } finally {
      if (current()) setBusy(false);
    }
  }
  async function select(id: string) {
    generation.current++;
    await action(async (current) => {
      const row = await workspaceApi.read(id);
      if (row.payload.kind !== "plan" || row.project_id !== scope.project_id)
        throw new Error();
      if (current()) {
        show(row as WorkspaceRecord<PlanBody>);
        const saved = await workspaceApi.executions(id);
        if (current() && saved.executions[0]) {
          setExecution(saved.executions[0]);
          setRequestId(saved.executions[0].request_id);
        }
      }
    });
  }
  function newPlan() {
    setPlan(null);
    setExecution(null);
    setRequestId(null);
    setTitle("研究计划");
    edit(
      JSON.stringify(
        scope.statistical_config
          ? [
              {
                id: "analysis",
                capability: "statistics",
                capability_version: "1",
                depends_on: [],
                statistics: scope.statistical_config,
                expected_output: "statistical_run",
                approval: "explicit_local_execution",
              },
            ]
          : [],
        null,
        2,
      ),
    );
  }
  function ridge() {
    try {
      const steps = JSON.parse(text) as PlanStep[],
        cols = (scope.columns ?? []).filter(
          (x) => x !== scope.statistical_config?.group_variable,
        );
      const step: PlanStep = {
        id: `ridge_${steps.length + 1}`,
        capability: "ridge",
        capability_version: "1",
        depends_on: steps.length ? [steps.at(-1)!.id] : [],
        expected_output: "ml_run",
        approval: "explicit_local_execution",
        ml: {
          task_type: "regression",
          target: cols.at(-1) ?? "",
          features: cols.slice(0, 1),
          model_ids: ["ridge"],
          split_strategy: "kfold",
          n_splits: 3,
          test_size: 0.2,
          random_seed: 42,
        },
      };
      edit(JSON.stringify([...steps, step], null, 2));
    } catch {
      setError(<UiMessage id="ui.residual.preview.fixStepJsonBeforeRidge" />);
    }
  }
  async function save() {
    await action(async (current) => {
      const payload: PlanBody = {
        kind: "plan",
        schema_version: 1,
        title,
        scope: { ...scope, statistical_config: null, ml_config: null },
        research_question: plan?.payload.research_question ?? "",
        steps: JSON.parse(text),
      };
      const saved = await workspaceApi.save(
        scope.project_id,
        payload,
        plan ?? undefined,
      );
      if (current()) {
        show(saved as WorkspaceRecord<PlanBody>);
        await lists();
      }
    });
  }
  async function execute(retry = false) {
    if (!plan || dirty || (!approved && !retry)) return;
    const id = requestId ?? crypto.randomUUID();
    setRequestId(id);
    await action(async (current) => {
      const result = await workspaceApi.execute(plan, id);
      if (current()) {
        setExecution(result);
        setApproved(false);
      }
    });
  }
  async function template() {
    if (!plan || dirty) return;
    await action(async (current) => {
      const context = await apiFetch<PreviewContext>(
        "/api/v1/preview/context",
        { method: "POST", body: JSON.stringify(plan.payload.scope) },
      );
      const columns = (
        context.categories.schema as {
          columns: { name: string; dtype: string }[];
        }
      ).columns;
      const used = new Set(
        plan.payload.steps.flatMap((s) =>
          s.statistics
            ? [
                ...s.statistics.variables,
                ...(s.statistics.categorical_variables ?? []),
                ...(s.statistics.group_variable
                  ? [s.statistics.group_variable]
                  : []),
                ...(s.statistics.target ? [s.statistics.target] : []),
              ]
            : [
                ...(s.ml?.features ?? []),
                ...(s.ml ? [s.ml.target] : []),
                ...(s.ml?.categorical_features ?? []),
                ...(s.ml?.group_variable ? [s.ml.group_variable] : []),
                ...(s.ml?.time_variable ? [s.ml.time_variable] : []),
              ],
        ),
      );
      const payload: TemplateBody = {
        kind: "template",
        schema_version: 1,
        title: derivedDisplayTitle(plan.payload.title, "template", t),
        steps: plan.payload.steps,
        research_question: plan.payload.research_question,
        required_columns: Object.fromEntries(
          columns.filter((c) => used.has(c.name)).map((c) => [c.name, c.dtype]),
        ),
      };
      await workspaceApi.save(scope.project_id, payload);
      if (current()) await lists();
    });
  }
  async function bind() {
    await action(async (current) => {
      const row = await workspaceApi.read(templateId);
      if (row.payload.kind !== "template") throw new Error();
      const saved = await workspaceApi.bind(
        row as WorkspaceRecord<TemplateBody>,
        {
          ...scope,
          statistical_run_id: null,
          ml_run_id: null,
          statistical_config: null,
          ml_config: null,
        },
        `${row.payload.title} · 新计划`,
      );
      if (current()) {
        show(saved);
        await lists();
      }
    });
  }
  return (
    <details className="workspace-plan">
      <summary>
        <UiMessage id="ui.workspace.plansAndTemplates" />
      </summary>
      <div className="space-y-3 py-3">
        <p className="text-xs text-muted-foreground">
          {" "}
          <UiMessage id="ui.workspace.savingAndEditingDoNotCreateARunUpTo8OrderedAcyclicStatisticalRidgeStepsAreSupportedEachSavedRevisionRequiresSeparateExecutionApproval" />{" "}
        </p>
        <label className="block">
          {" "}
          <UiMessage id="ui.workspace.savePlan" />{" "}
          <SearchSelect
            className="workspace-input w-full"
            value={plan?.id ?? ""}
            disabled={busy}
            onChange={(e) => void select(e.target.value)}
          >
            <option value="">
              <UiMessage id="ui.workspace.selectPlan" />
            </option>
            {items
              .filter((x) => x.kind === "plan")
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title} · r{p.revision}
                </option>
              ))}
          </SearchSelect>
        </label>
        <Button size="sm" disabled={busy} onClick={newPlan}>
          {" "}
          <UiMessage id="ui.workspace.buildAPlanFromTheCurrentValidForm" />{" "}
        </Button>
        {!!text && (
          <>
            <label className="block">
              {" "}
              <UiMessage id="ui.workspace.planName" />{" "}
              <input
                className="workspace-input w-full"
                value={title}
                maxLength={160}
                onChange={(e) => {
                  setTitle(e.target.value);
                  setDirty(true);
                  setApproved(false);
                }}
              />
            </label>
            <label className="block">
              {" "}
              <UiMessage id="ui.workspace.typedStepsEditableJson" />{" "}
              <textarea
                className="workspace-input w-full font-mono text-xs"
                rows={14}
                value={text}
                maxLength={24000}
                onChange={(e) => edit(e.target.value)}
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy} onClick={ridge}>
                {" "}
                <UiMessage id="ui.workspace.addRidgeStep" />{" "}
              </Button>
              <Button
                size="sm"
                disabled={busy || !dirty}
                onClick={() => void save()}
              >
                {" "}
                <UiMessage id="ui.workspace.validateAndSavePlan" />{" "}
              </Button>
              <Button
                size="sm"
                disabled={busy || dirty || !plan}
                onClick={() => void template()}
              >
                {" "}
                <UiMessage id="ui.workspace.saveAsTemplate" />{" "}
              </Button>
            </div>
            {plan && !dirty && (
              <>
                <label className="flex gap-2">
                  <input
                    type="checkbox"
                    checked={approved}
                    disabled={busy || !!requestId}
                    onChange={(e) => setApproved(e.target.checked)}
                  />{" "}
                  <UiMessage id="ui.workspace.approveExecutionOfTheSavedRevisionOnTheCurrentDataVersion" />{" "}
                  {plan.revision}
                  <UiMessage id="ui.workspace.producingActualStatisticalMlRuns" />{" "}
                </label>
                <Button
                  disabled={busy || !approved || !!requestId}
                  onClick={() => void execute()}
                >
                  {" "}
                  <UiMessage id="ui.workspace.executeSavedRevision" />{" "}
                </Button>
              </>
            )}
            {requestId && (
              <>
                <p className="break-all text-xs">
                  {" "}
                  <UiMessage id="ui.workspace.request" /> {requestId} ·{" "}
                  {execution?.status ?? (
                    <UiMessage id="ui.displayBranch.common.pendingVerification" />
                  )}
                </p>
                <Button
                  size="sm"
                  disabled={
                    busy ||
                    dirty ||
                    (!!execution && execution.plan_revision !== plan?.revision)
                  }
                  onClick={() => void execute(true)}
                >
                  {" "}
                  <UiMessage id="ui.workspace.reviewTheSameRequest" />{" "}
                </Button>
                {execution && (
                  <Button
                    size="sm"
                    disabled={busy || dirty}
                    onClick={() => {
                      setRequestId(null);
                      setApproved(false);
                    }}
                  >
                    {" "}
                    <UiMessage id="ui.workspace.prepareANewExecutionRequest" />{" "}
                  </Button>
                )}
              </>
            )}
            {execution && (
              <>
                <p role="status">
                  {" "}
                  <UiMessage id="ui.workspace.executionStatus" />
                  {execution.status} {execution.error_code ?? ""}{" "}
                  <UiMessage id="ui.workspace.unfinishedStepsAreNotMarkedAsSuccessful" />{" "}
                </p>
                {execution.results.map((r) => (
                  <p key={r.step_id}>
                    {r.step_id}：{r.status} ·{" "}
                    <DesktopLink
                      className="text-primary underline"
                      href={`/projects/${scope.project_id}/${r.statistical_run_id ? "statistics" : "machine-learning"}?datasetId=${scope.dataset_id}&runId=${r.statistical_run_id ?? r.ml_run_id}`}
                    >
                      {" "}
                      <UiMessage id="ui.workspace.locateTheActualRun" />{" "}
                    </DesktopLink>
                  </p>
                ))}
              </>
            )}
          </>
        )}
        <label className="block">
          {" "}
          <UiMessage id="ui.workspace.rebindTemplate" />{" "}
          <SearchSelect
            className="workspace-input w-full"
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
          >
            <option value="">
              <UiMessage id="ui.workspace.selectTemplate" />
            </option>
            {items
              .filter((x) => x.kind === "template")
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
          </SearchSelect>
        </label>
        <Button
          size="sm"
          disabled={busy || !templateId}
          onClick={() => void bind()}
        >
          {" "}
          <UiMessage id="ui.workspace.bindCurrentDataToANewPlan" />{" "}
        </Button>
        {templateId && (
          <WorkspaceTemplateEditor
            key={`${scope.project_id}:${templateId}`}
            projectId={scope.project_id}
            templateId={templateId}
            onSaved={() => void lists()}
          />
        )}
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
      </div>
    </details>
  );
}
