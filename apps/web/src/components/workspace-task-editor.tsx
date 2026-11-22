"use client";

import { TaskResultToolbar, ResultSource } from "./task-result-toolbar";
import { AnalysisOwnership } from "./workspace-result-navigation";
import { SearchSelect } from "@/components/ui/search-select";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { analysisTableChoices, analysisTableVersions, versionTitle } from "@/lib/analysis-tables";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckIcon,
  MessageSquareIcon,
  PlusIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { apiFetch, ApiError } from "@/lib/api-client";
import { datasetsApi, statisticsApi, mlApi } from "@/lib/api-endpoints";
import {
  workspaceApi,
  type AnalysisDraft,
  type FigureData,
  type FigureSpec,
  type WorkspaceRecord,
  type PlotRun,
} from "@/lib/workspace-api";
import { sameTaskValue, useWorkspaceTask, type TaskRecord } from "@/lib/use-workspace-task";
import { mlColumns, type PreviewContext } from "@/lib/preview-types";
import type {
  Dataset,
  MLEditorState,
  MLExperimentCreateRequest,
  StatisticalRun,
  StatisticalRunRequest,
} from "@/lib/types";
import { getActorEpoch } from "@/lib/auth-session";
import { analysisCsv, analysisHtml } from "@/lib/research-output";
import { statisticsTableCsv } from "@/lib/statistics-table-export";
import { datasetTitle } from "@/lib/workspace-files";
import { groupTaskPlots } from "@/lib/task-plot-groups";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "./ui/dialog";
import { figureBlob, figureSvg } from "@/lib/workspace-figure";
import { saveGeneratedFile } from "@/lib/api-download";
import { methodDisplayName } from "@/lib/method-labels";
import { useLanguage } from "@/providers/language-provider";
import { StatisticsConfigForm } from "@/app/projects/[projectId]/statistics/statistics-config";
import { StatisticsResultView } from "@/app/projects/[projectId]/statistics/statistics-result";
import { ResizeHandle, usePreviewHistory, backLabel } from "./workspace-layout-controls";
import { WorkspaceDataTable } from "./workspace-data-browser";
import { AdviceHeaderSlot } from "./material-suggestions";
import { ProjectAssistant } from "./project-assistant";
import { WorkspaceFigure } from "./workspace-figure";
import { DownloadButton } from "./download-button";
import { DesktopLink } from "./desktop-link";
import { registerProjectEditor } from "@/lib/project-save";
import { projectResearchApi } from "@/lib/project-research";
import { WorkspaceTaskML, WorkspaceMLResult } from "./workspace-task-ml";
import { WorkspaceTaskPlots } from "./workspace-task-plots";
import type { AssistantFigureTarget, AssistantResult } from "@/lib/project-assistant-api";

type Analysis = WorkspaceRecord<AnalysisDraft>;
type Figure = WorkspaceRecord<FigureSpec>;
type Tab = "data" | "settings" | "figures" | "results";
export type WorkspaceTaskNavigation = { nonce: number; tab?: Tab; analysis?: "statistics" | "ml"; analysisId?: string; plotRunId?: string; plotDraftId?: string; figureId?: string; datasetId?: string; statisticsProposal?: StatisticalRunRequest };
const failure = (reason: unknown) =>
  reason instanceof ApiError
    ? reason.detail
    : reason instanceof Error
      ? reason.message
      : "操作未完成，请重试";
const figureFailure = (reason: unknown) => reason instanceof ApiError && reason.errorCode === "PREVIEW_SCOPE_INVALID"
  ? "所选分析引用的数据版本或字段已变化。请返回结果确认数据；若原文件已改变，请用原参数重新分析。"
  : failure(reason);

export function WorkspaceTaskEditor({
  initial,
  datasets,
  agentOpen,
  adviceHost,
  onCloseAgent,
  onSaved,
  onStatus,
  active = true,
  navigation,
  rightWidth, onRightWidthChange, rightMax,
}: {
  initial: TaskRecord;
  datasets: Dataset[];
  agentOpen: boolean;
  adviceHost?: HTMLElement | null;
  onCloseAgent: () => void;
  onSaved: (row: TaskRecord) => void;
  onStatus: (id: string, status: string) => void;
  active?: boolean;
  navigation?: WorkspaceTaskNavigation;
  rightWidth: number; onRightWidthChange: (width: number) => void; rightMax: number;
}) {
  const task = useWorkspaceTask(initial, onSaved);
  const value = task.value,
    projectId = initial.project_id;
  const { locale, t } = useLanguage();
  const client = useQueryClient();
  const [tab, setTab] = useState<Tab>("data");
  const previewHistory = usePreviewHistory(value.dataset_id ?? undefined, `[data-task-scroll="${initial.id}"]`);
  const [plotDraftId, setPlotDraftId] = useState("");
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [family, setFamily] = useState<"statistics" | "ml">(
    !value.statistics_draft && (value.ml_draft || datasets.some((item) => item.id === value.dataset_id && item.multimodal)) ? "ml" : "statistics",
  );
  const [mlSeed, setMlSeed] = useState(0);
  const [selected, setSelected] = useState(
    value.analysis_ids.at(-1) || value.adopted_id || "",
  );
  const [compare, setCompare] = useState("");
  const [requestedFigure, setRequestedFigure] = useState("");
  const [selectedPlot, setSelectedPlot] = useState("");
  const [plotOpenRequest, setPlotOpenRequest] = useState(0);
  const [plotEditorOpen, setPlotEditorOpen] = useState(false);
  const [statFiguresOpen,setStatFiguresOpen]=useState(false);
  const [figureTarget,setFigureTarget]=useState<AssistantFigureTarget | null>(null);
  const hasPlotRuns=!!value.plot_run_ids?.length;
  const readingPlots=hasPlotRuns && !statFiguresOpen && !plotEditorOpen;
  function askAboutFigure(row: Figure | WorkspaceRecord<PlotRun>, name = row.payload.title) {
    setFigureTarget({id:row.id, revision:row.revision, name});
    window.dispatchEvent(new CustomEvent("statforge:show-agent",{detail:{project:projectId,context:initial.id}}));
  }
  useEffect(()=>{setFigureTarget(null);},[active,tab,plotEditorOpen,statFiguresOpen]);
  async function linkPlot(row: WorkspaceRecord<PlotRun>) {
    task.update(prior=>({...prior,plot_run_ids:[...new Set([...(prior.plot_run_ids??[]),row.id])]}));
    await task.flush();
    await client.invalidateQueries({queryKey:["task-plots",initial.id]});
  }

  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [proposal, setProposal] = useState<{
    id: number;
    config: StatisticalRunRequest;
  } | null>(() =>
    value.statistics_draft ? { id: 0, config: value.statistics_draft } : null,
  );
  const [showHistory, setShowHistory] = useState(false);
  const actor = useRef(getActorEpoch()).current;
  const running = useRef(false);
  // Only the view from which a manual run was started may follow its result.
  const view = useRef({ active, tab, analysisOpen, datasetId: value.dataset_id, epoch: 0 });
  if (view.current.active !== active || view.current.tab !== tab || view.current.analysisOpen !== analysisOpen || view.current.datasetId !== value.dataset_id)
    view.current = { active, tab, analysisOpen, datasetId: value.dataset_id, epoch: view.current.epoch + 1 };
  const selectedDataset = useQuery({ queryKey: ["dataset", value.dataset_id],
    queryFn: ({ signal }) => datasetsApi.get(value.dataset_id!, signal),
    enabled: !!value.dataset_id && !datasets.some(item => item.id === value.dataset_id) });
  const dataset = datasets.find(item => item.id === value.dataset_id) ?? selectedDataset.data;
  const taskDatasetIds = new Set([...(value.dataset_ids ?? []), value.dataset_id].filter(Boolean));
  const tables = useMemo(() => analysisTableVersions(datasets), [datasets]);
  const tableChoices = analysisTableChoices(datasets, tables, [value.dataset_id ?? "", ...(value.dataset_ids ?? [])]);
  const tableTitle = (d: Dataset) => tables.has(d.id) ? versionTitle(tables.get(d.id)!) : datasetTitle(d, datasets);
  const canPreviewTable = (item: Dataset) => item.upload_status === "ready" && !item.raw_file && !item.signal_session
    && !item.media_case && item.file_format !== "pitch" && !(item.media_result && item.file_format === "npz");
  const usingAnalysisTable = !!dataset && canPreviewTable(dataset);
  const availableTables = tableChoices.filter(canPreviewTable);
  const analysisTables = usingAnalysisTable && !availableTables.some(table => table.id === dataset.id)
    ? [dataset, ...availableTables] : availableTables;
  const methods = useQuery({
    queryKey: ["statistics", "methods"],
    queryFn: ({ signal }) => statisticsApi.listMethods(signal),
  });
  const preview = useQuery({
    queryKey: ["dataset-preview", dataset?.id, 100],
    queryFn: ({ signal }) => datasetsApi.preview(dataset!.id, 100, signal),
    enabled: usingAnalysisTable,
  });
  const plotRuns = useQuery({ queryKey: ["task-plots", initial.id, value.plot_run_ids ?? []],
    queryFn: ({ signal }) => Promise.all((value.plot_run_ids ?? []).map(async id => {
      const record = await workspaceApi.read(id, signal);
      if (record.project_id !== projectId || record.payload.kind !== "plot_run") throw new Error("图表暂时无法读取");
      return record as WorkspaceRecord<PlotRun>;
    })), enabled: !!value.plot_run_ids?.length });
  const figureCount = value.figure_ids.length + groupTaskPlots(plotRuns.data ?? [], value.plot_version_order).filter(group => group.completed).length;
  const analyses = useQuery({
    queryKey: ["task-analyses", initial.id, value.analysis_ids],
    queryFn: ({ signal }) =>
      Promise.all(
        value.analysis_ids.map((id) =>
          workspaceApi.read(id, signal).then((record) => {
            if (
              record.project_id !== projectId ||
              record.payload.kind !== "analysis"
            )
              throw new Error("分析来源不可用");
            return record as Analysis;
          }),
        ),
      ),
  });
  const current =
    analyses.data?.find((item) => item.id === selected) ??
    analyses.data?.find((item) => item.id === value.adopted_id) ??
    analyses.data?.at(-1);
  const run = useQuery({
    queryKey: ["statistics-run", current?.payload.scope.statistical_run_id],
    queryFn: ({ signal }) =>
      statisticsApi.getRun(current!.payload.scope.statistical_run_id!, signal),
    enabled: !!current?.payload.scope.statistical_run_id,
  });
  const mlRun = useQuery({
    queryKey: ["ml-run", current?.payload.scope.ml_run_id],
    queryFn: ({ signal }) =>
      mlApi.getRun(current!.payload.scope.ml_run_id!, signal),
    enabled: !!current?.payload.scope.ml_run_id,
  });
  const history = useQuery({
    queryKey: ["task-existing-runs", dataset?.id],
    queryFn: ({ signal }) =>
      statisticsApi.listRuns(dataset!.id, { pageSize: 100 }, signal),
    enabled: !!dataset && showHistory,
  });
  const modelHistory = useQuery({
    queryKey: ["task-existing-ml", projectId],
    queryFn: ({ signal }) =>
      projectResearchApi.history(projectId, { kind: "ml", limit: 100 }, signal),
    enabled: !!dataset && showHistory,
  });
  useEffect(() => {
    if (!selected && value.analysis_ids.length)
      setSelected(value.analysis_ids.at(-1)!);
  }, [selected, value.analysis_ids, value.adopted_id]);
  const methodName = (id: string) => {
    const method = methods.data?.methods.find((item) => item.method_id === id);
    return method ? methodDisplayName(method, t) : id;
  };
  async function context(source: Dataset) {
    const data = await datasetsApi.preview(source.id, 5);
    return apiFetch<PreviewContext>("/api/v1/preview/context", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        dataset_id: source.id,
        columns: data.columns.map((column) => column.name),
      }),
    });
  }
  async function execute(config: StatisticalRunRequest) {
    if (!dataset || running.current) return;
    const startedView = view.current.epoch;
    running.current = true;
    setBusy(true);
    setError("");
    onStatus(initial.id, "运行中");
    let saved: Analysis | null = null;
    try {
      task.update({ statistics_draft: config });
      await task.flush();
      const source = await context(dataset);
      if (getActorEpoch() !== actor) return;
      saved = (await workspaceApi.save(projectId, {
        kind: "analysis",
        schema_version: 1,
        title:
          `${methodName(config.method_id)} · ${value.analysis_ids.length + 1}`.slice(
            0,
            160,
          ),
        scope: source.scope,
        statistics: config,
        research_question: value.question.slice(0, 1000),
        observation_unit: "",
        measurement_scales: {},
      })) as Analysis;
      const id = saved.id;
      task.update((prior) => ({
        ...prior,
        analysis_ids: [...prior.analysis_ids, id],
      }));
      await task.flush();
      const result = await statisticsApi.run(dataset.id, config);
      if (getActorEpoch() !== actor) return;
      saved = (await workspaceApi.save(
        projectId,
        {
          ...saved.payload,
          scope: {
            ...saved.payload.scope,
            statistical_run_id: result.id,
            statistical_config: config,
          },
        },
        saved,
      )) as Analysis;
      task.update((prior) => ({
        ...prior,
        adopted_id: prior.adopted_id || saved!.id,
      }));
      await task.flush();
      await client.invalidateQueries({
        queryKey: ["task-analyses", initial.id],
      });
      client.setQueryData(["statistics-run", result.id], result);
      if (result.status === "completed" && view.current.active && view.current.epoch === startedView) {
        setSelected(saved.id);
        setCompare("");
        setAnalysisOpen(false);
        setTab("results");
      } else if (result.status !== "completed") {
        setError(result.error_message || result.result?.error_message || "分析未完成，请检查所选变量与方法。");
      }
      onStatus(
        initial.id,
        result.status === "completed" ? "有新结果" : "请查看结果",
      );
      toast(
        `${value.title}：${result.status === "completed" ? "分析已完成" : "分析未完成"}`,
      );
    } catch (reason) {
      if (getActorEpoch() === actor) {
        setError(
          `${failure(reason)}${saved ? "。本次配置已保留，实际运行可从项目运行记录打开。" : ""}`,
        );
        onStatus(initial.id, "需处理");
      }
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  async function attachRun(runId: string) {
    setError("");
    setBusy(true);
    try {
      const result = await statisticsApi.getRun(runId);
      const source = datasets.find((item) => item.id === result.dataset_id);
      if (!source || !result.input_config)
        throw new Error("此结果缺少可用数据或参数");
      const ctx = await context(source);
      if (ctx.scope.dataset_sha256 !== result.dataset_sha256)
        throw new Error("数据版本不匹配");
      const config = {
        ...result.input_config,
        missing_value_policy: result.missing_value_policy,
      } as unknown as StatisticalRunRequest;
      const row = (await workspaceApi.save(projectId, {
        kind: "analysis",
        schema_version: 1,
        title: methodName(result.method),
        scope: {
          ...ctx.scope,
          statistical_run_id: result.id,
          statistical_config: config,
        },
        statistics: config,
        research_question: value.question.slice(0, 1000),
        observation_unit: "",
        measurement_scales: {},
      })) as Analysis;
      task.update((prior) => ({
        ...prior,
        analysis_ids: [...prior.analysis_ids, row.id],
        adopted_id: prior.adopted_id || row.id,
      }));
      await task.flush();
      setSelected(row.id);
      setShowHistory(false);
    } catch (reason) {
      setError(failure(reason));
    } finally {
      setBusy(false);
    }
  }
  async function executeML(config: MLExperimentCreateRequest) {
    if (!dataset || running.current) return;
    const startedView = view.current.epoch;
    running.current = true;
    setBusy(true);
    setError("");
    onStatus(initial.id, "运行中");
    let saved: Analysis | null = null;
    try {
      task.update({ ml_draft: config });
      await task.flush();
      const source = await apiFetch<PreviewContext>("/api/v1/preview/context", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          dataset_id: dataset.id,
          columns: mlColumns(config),
          ml_config: config,
        }),
      });
      saved = (await workspaceApi.save(projectId, {
        kind: "analysis",
        schema_version: 1,
        title: `机器学习 · ${value.analysis_ids.length + 1}`,
        scope: source.scope,
        ml: config,
        ml_editor: value.ml_editor,
        research_question: value.question.slice(0, 1000),
        observation_unit: "",
        measurement_scales: {},
      })) as Analysis;
      const id = saved.id;
      task.update((prior) => ({
        ...prior,
        analysis_ids: [...prior.analysis_ids, id],
      }));
      await task.flush();
      const experiment = await mlApi.createExperiment(dataset.id, config);
      const result = await mlApi.runExperiment(experiment.id);
      if (getActorEpoch() !== actor) return;
      saved = (await workspaceApi.save(
        projectId,
        {
          ...saved.payload,
          scope: {
            ...saved.payload.scope,
            ml_config: config,
            ml_run_id: result.id,
          },
        },
        saved,
      )) as Analysis;
      if (result.status === "completed") {
        task.update((prior) => ({
          ...prior,
          adopted_id: prior.adopted_id || saved!.id,
        }));
        await task.flush();
      }
      await client.invalidateQueries({
        queryKey: ["task-analyses", initial.id],
      });
      if (result.status === "completed" && view.current.active && view.current.epoch === startedView) {
        setSelected(saved.id); setCompare(""); setAnalysisOpen(false); setTab("results");
      }
      onStatus(
        initial.id,
        result.status === "completed" ? "有新结果" : "需处理",
      );
      toast(
        `${value.title}：模型运行${result.status === "completed" ? "已完成" : "已返回，请查看结果"}`,
      );
    } catch (reason) {
      if (getActorEpoch() === actor) {
        setError(failure(reason));
        onStatus(initial.id, "需处理");
      }
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  async function attachML(runId: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await mlApi.getRun(runId);
      const experiment = await mlApi.getExperiment(result.experiment_id);
      const source = datasets.find((item) => item.id === experiment.dataset_id);
      if (
        !source ||
        source.sha256 !== experiment.dataset_sha256 ||
        !experiment.config
      )
        throw new Error("模型运行的数据或参数不可用");
      const config = {
        ...experiment.config,
        name: experiment.name,
        task_type: experiment.task_type,
        target: experiment.target_variable,
        features: experiment.feature_variables,
        categorical_features: experiment.categorical_variables,
      } as MLExperimentCreateRequest;
      const ctx = await apiFetch<PreviewContext>("/api/v1/preview/context", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          dataset_id: source.id,
          columns: mlColumns(config),
          ml_config: config,
        }),
      });
      const row = (await workspaceApi.save(projectId, {
        kind: "analysis",
        schema_version: 1,
        title: experiment.name.slice(0, 160),
        scope: { ...ctx.scope, ml_run_id: result.id, ml_config: config },
        ml: config,
        research_question: value.question.slice(0, 1000),
        observation_unit: "",
        measurement_scales: {},
      })) as Analysis;
      task.update((prior) => ({
        ...prior,
        analysis_ids: [...prior.analysis_ids, row.id],
        adopted_id:
          result.status === "completed"
            ? prior.adopted_id || row.id
            : prior.adopted_id,
      }));
      await task.flush();
      setSelected(row.id);
      setShowHistory(false);
    } catch (reason) {
      setError(failure(reason));
    } finally {
      setBusy(false);
    }
  }
  async function exportResult(format: "html" | "csv" | "record", signal: AbortSignal) {
    if (!current || !run.data?.result) throw new Error("请先选择已完成的结果");
    const fresh = await workspaceApi.read(current.id, signal, current.revision);
    const result = await statisticsApi.getRun(run.data.id, signal);
    if (fresh.payload.kind !== "analysis" || getActorEpoch() !== actor)
      throw new Error("来源已改变，请重新打开结果");
    const contents =
      format === "html"
        ? analysisHtml(fresh as Analysis, result, null, false, locale)
        : format === "csv" ? statisticsTableCsv(result, locale) : analysisCsv(fresh as Analysis, result);
    return saveGeneratedFile(
      new Blob([contents], {
        type:
          format === "html"
            ? "text/html;charset=utf-8"
            : "text/csv;charset=utf-8",
      }),
      `${value.title}-${current.payload.title}${format === "record" ? "-完整记录" : ""}.${format === "html" ? "html" : "csv"}`,
      signal,
      async () => {
        const latest = await workspaceApi.read(
          current.id,
          signal,
          current.revision,
        );
        const latestRun = await statisticsApi.getRun(result.id, signal);
        if (
          JSON.stringify(latest.payload) !== JSON.stringify(fresh.payload) ||
          JSON.stringify(latestRun) !== JSON.stringify(result)
        )
          throw new Error("导出来源已改变，请重新打开结果");
      },
      locale,
    );
  }
  const chooseData = (id: string) => {
    if (id === task.value.dataset_id) return;
    task.update((prior) => ({
      ...prior,
      dataset_id: id,
      dataset_ids: [...new Set([...(prior.dataset_ids ?? []), ...(prior.dataset_id ? [prior.dataset_id] : []), ...(id ? [id] : [])])],
      statistics_draft: null,
      ml_draft: null,
      ml_editor: null,
    }));
    setProposal(null);
  };
  const refreshTask = task.refresh;
  useEffect(() => {
    if (!navigation) return;
    if (navigation.datasetId) { previewHistory.reset(); chooseData(navigation.datasetId); }
    if (navigation.tab) setTab(navigation.tab);
    if (navigation.analysis) { setFamily(navigation.analysis); setTab("settings"); setAnalysisOpen(true); }
    if (navigation.statisticsProposal) {
      task.update({ statistics_draft: navigation.statisticsProposal });
      setProposal({ id: navigation.nonce, config: navigation.statisticsProposal });
      setFamily("statistics"); setTab("settings"); setAnalysisOpen(true);
    }
    if (navigation.analysisId) {
      setSelected(navigation.analysisId); setTab("results");
      void workspaceApi.read(navigation.analysisId).then(row => {
        if (row.project_id !== projectId || row.payload.kind !== "analysis" || row.payload.scope.statistical_run_id || row.payload.scope.ml_run_id) return;
        const config = row.payload;
        if (config.ml) { task.update({dataset_id: config.scope.dataset_id ?? value.dataset_id, ml_draft: config.ml, ml_editor: config.ml_editor ?? null}); setFamily("ml"); setMlSeed(n => n + 1); }
        else if (config.statistics) { task.update({dataset_id: config.scope.dataset_id ?? value.dataset_id, statistics_draft: config.statistics}); setProposal({id: Date.now(), config: config.statistics}); setFamily("statistics"); }
        setTab("settings"); setAnalysisOpen(true);
      }).catch(reason => setError(reason instanceof Error ? reason.message : "无法读取分析参数"));
    }
    if (navigation.plotDraftId) { setStatFiguresOpen(false); setPlotDraftId(navigation.plotDraftId); setSelectedPlot(""); setTab("figures"); setPlotEditorOpen(true); }
    if (navigation.plotRunId) { setStatFiguresOpen(false); setPlotDraftId(""); setSelectedPlot(navigation.plotRunId); setPlotOpenRequest(n => n + 1); setTab("figures"); setPlotEditorOpen(true); }
    if (navigation.figureId) { setStatFiguresOpen(true); setPlotEditorOpen(false); setRequestedFigure(navigation.figureId); setTab("figures"); }
    // Navigation is an explicit user action; data refreshes must not steal this view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigation?.nonce]);
  const assistantCompleted = useCallback(async () => {
    await refreshTask();
    for (const key of ["task-analyses", "task-figures", "task-plots"]) await client.invalidateQueries({ queryKey: [key, initial.id] });
    onStatus(initial.id, "有新结果");
  }, [refreshTask, client, initial.id, onStatus]);
  const openAssistantResult = (result: AssistantResult) => {
    if (result.task_id !== initial.id) return false;
    void task.refresh().catch((reason) => setError(failure(reason)));
    if ((result.kind === "statistics" || result.kind === "ml") && result.workspace_record_id) {
      setSelected(result.workspace_record_id);
      setTab("results");
      return true;
    }
    if (result.kind === "figure") {
      setStatFiguresOpen(true);
      setRequestedFigure(result.workspace_record_id || result.id);
      setPlotEditorOpen(false);
      setTab("figures");
      return true;
    }
    if (result.kind === "plot") {
      setStatFiguresOpen(false);
      setPlotDraftId("");
      setSelectedPlot(result.workspace_record_id || result.id);
      setFigureTarget(null);
      setPlotEditorOpen(false);
      setTab("figures");
      return true;
    }
    if (result.kind === "dataset") {
      chooseData(result.id);
      setTab("data");
      return true;
    }
    return false;
  };
  const configurationChanged = (config: StatisticalRunRequest) => {
    if (!sameTaskValue(config, task.value.statistics_draft))
      task.update({ statistics_draft: config });
  };
  const mlChanged = (
    config: MLExperimentCreateRequest,
    _complete: boolean,
    editor?: MLEditorState,
  ) => {
    if (
      !sameTaskValue(config, task.value.ml_draft) ||
      !sameTaskValue(editor ?? null, task.value.ml_editor ?? null)
    )
      task.update({ ml_draft: config, ml_editor: editor ?? null });
  };
  const comparison = analyses.data?.find(item => item.id === compare && item.id !== current?.id);
  const savedSourceId = run.data?.dataset_id ?? current?.payload.scope.dataset_id;
  const resultSource = datasets.find(item => item.id === savedSourceId);
  const resultNumber = current ? value.analysis_ids.indexOf(current.id) + 1 : 0;
  const label = current?.payload.statistics ? methodName(current.payload.statistics.method_id) : "";
  const resultHeading = label && current?.payload.title === `${label} · ${resultNumber}`
    ? label : current?.payload.title ?? "";
  function addAnalysis() {
    setShowHistory(false);
    setTab("settings");
    setAnalysisOpen(true);
  }
  function editResult() {
    if (!current) return;
    const saved = current.payload;
    const datasetId = saved.scope.dataset_id ?? value.dataset_id;
    if (saved.ml) {
      task.update({dataset_id: datasetId, ml_draft: saved.ml, ml_editor: saved.ml_editor ?? null});
      setFamily("ml");
      setMlSeed(old => old + 1);
    } else if (saved.statistics) {
      task.update({dataset_id: datasetId, statistics_draft: saved.statistics});
      setProposal({id: Date.now(), config: saved.statistics});
      setFamily("statistics");
    } else return;
    addAnalysis();
  }
  const versionPicker = (
    <label className={dataset?.media_case ? "rw-media-version" : undefined}>
      当前数据版本{" "}
      <SearchSelect
        aria-label="当前任务数据版本"
        value={value.dataset_id ?? ""}
        disabled={busy}
        onChange={(event) => { previewHistory.reset(); chooseData(event.target.value); }}
      >
        <option value="">选择数据</option>
        {!!taskDatasetIds.size && <optgroup label="本任务的数据版本">
            {tableChoices.filter((item) => item.upload_status !== "deleted" && taskDatasetIds.has(item.id)).map((item) => <option key={item.id} value={item.id}>{tableTitle(item)}</option>)}
        </optgroup>}
        <optgroup label="其他项目数据">
          {tableChoices
          .filter((item) => (item.upload_status === "ready" || item.raw_file) && !taskDatasetIds.has(item.id))
          .map((item) => (
            <option key={item.id} value={item.id}>
                {tableTitle(item)}
              {item.preparation_steps
                ? ` · 处理 ${item.preparation_steps}`
                : ""}
              {item.sheet_name ? ` · ${item.sheet_name}` : ""}
            </option>
          ))}
        </optgroup>
      </SearchSelect>
    </label>
  );
  return (
    <div className={`rw-task-layout ${agentOpen ? "with-agent" : ""}`}>
      <section className="rw-task-main">
        <header className="rw-page-heading">
          <div>
            <input
              className="rw-task-title"
              aria-label="研究问题名称"
              maxLength={160}
              value={value.title}
              onChange={(event) =>
                task.update({ title: event.target.value || "未命名研究问题",
                  ...(value.question === value.title ? { question: event.target.value || "未命名研究问题" } : {}) })
              }
            />
            <p>
              {dataset
                ? datasetTitle(dataset, datasets)
                : "尚未选择数据"}{" "}
              <span className="rw-muted">
                ·{" "}
                {task.error
                  ? "未保存"
                  : task.saving
                    ? "保存中…"
                    : "已保存到本机"}
              </span>
            </p>
          </div>
          <button
            onClick={() => void task.flush()}
            disabled={busy || task.saving}
          >
            保存
          </button>
        </header>
        <div className="rw-tabs" role="tablist" aria-label="研究内容">
          {(
            [
              ["data", "数据"],
              ["settings", "分析"],
              ["figures", "图表"],
              ["results", "结果"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              id={`tab-${initial.id}-${key}`}
              aria-selected={tab === key}
              aria-controls={`panel-${initial.id}-${key}`}
              onClick={() => setTab(key)}
            >
              {label}
              {key === "figures" && figureCount ? (
                <small>{figureCount}</small>
              ) : key === "results" && value.analysis_ids.length ? (
                <small>{value.analysis_ids.length}</small>
              ) : null}
            </button>
          ))}
          <span className="rw-tab-spacer" />
          {busy && <span role="status">正在运行，可继续浏览项目</span>}
        </div>
        <div className="rw-task-scroll" data-task-scroll={initial.id}>
          {(error || task.error) && (
            <div role="alert" className="rw-error">
              {error || task.error}
              {task.error && (
                <button
                  onClick={() => void task.flush().catch(() => undefined)}
                >
                  重试保存
                </button>
              )}
            </div>
          )}
          <section
            role="tabpanel"
            id={`panel-${initial.id}-data`}
            aria-labelledby={`tab-${initial.id}-data`}
            hidden={tab !== "data"}
          >
            {!dataset?.media_case && <div className="rw-content-tools">{versionPicker}</div>}
            {previewHistory.canBack && <button className="rw-preview-back" onClick={() => previewHistory.back(chooseData)}>{backLabel(datasets.find(d=>d.id===previewHistory.previousId))}</button>}
            {dataset ? (
              <AdviceHeaderSlot.Provider value={active && tab === "data" ? adviceHost ?? null : null}>
              <WorkspaceDataTable versionPicker={versionPicker} revision={initial.revision}
                taskId={initial.id}
                onSaved={assistantCompleted}
                key={dataset.id}
                dataset={dataset}
                datasets={datasets}
                onChoose={(id) => { if (id !== value.dataset_id) { previewHistory.push(); chooseData(id); } }}
                preparation
              />
              </AdviceHeaderSlot.Provider>
            ) : (
              <p className="rw-empty">
                选择当前问题使用的数据。
              </p>
            )}
          </section>
          <section
            role="tabpanel"
            id={`panel-${initial.id}-settings`}
            aria-labelledby={`tab-${initial.id}-settings`}
            hidden={tab !== "settings"}
          >
            <div className="rw-content-tools">
              <h2>分析</h2>
              <button className="rw-primary" onClick={() => setAnalysisOpen(true)}><PlusIcon size={15} />添加分析</button>
            </div>
            <div className="rw-analysis-list">
              {analyses.data?.slice().reverse().map(item => <div key={item.id} className="rw-analysis-entry">
                <div className="rw-analysis-description"><strong>{item.payload.title}</strong><small>{item.payload.scope.statistical_run_id || item.payload.scope.ml_run_id ? "已运行" : "已保存配置"} · {datasets.find(d => d.id === item.payload.scope.dataset_id)?.original_filename || "已保存的数据版本"}</small></div>
                <div className="rw-analysis-actions">
                <button onClick={() => { setSelected(item.id); setTab("results"); }}>{item.payload.scope.statistical_run_id || item.payload.scope.ml_run_id ? "查看结果" : "查看配置记录"}</button>
                <button onClick={() => {
                  if (item.payload.ml) { task.update({ ml_draft: item.payload.ml, ml_editor: item.payload.ml_editor ?? null, dataset_id: item.payload.scope.dataset_id ?? value.dataset_id }); setFamily("ml"); setMlSeed(n => n + 1); }
                  else if (item.payload.statistics) { task.update({ statistics_draft: item.payload.statistics, dataset_id: item.payload.scope.dataset_id ?? value.dataset_id }); setProposal({id:Date.now(),config:item.payload.statistics}); setFamily("statistics"); }
                  setAnalysisOpen(true);
                }}>调整参数</button>
                </div>
              </div>)}
              {!analyses.data?.length && <p className="rw-empty">选择一种分析开始。</p>}
            </div>
            <Dialog open={analysisOpen && active} onOpenChange={setAnalysisOpen}>
              <DialogContent className="rw-shell rw-analysis-dialog">
                <DialogTitle>添加或调整分析</DialogTitle>
                <DialogDescription className="sr-only">选择分析数据、方法和变量</DialogDescription>
                {error && <p role="alert" className="rw-error">{error}</p>}
                <div className="rw-analysis-dialog-scroll">
            <div className="rw-family-choice">
              <button
                type="button"
                className={family === "statistics" ? "active" : ""}
                aria-pressed={family === "statistics"}
                onClick={() => {
                  setProposal(
                    value.statistics_draft
                      ? { id: Date.now(), config: value.statistics_draft }
                      : null,
                  );
                  setFamily("statistics");
                }}
              >
                统计分析
              </button>
              <button
                type="button"
                className={family === "ml" ? "active" : ""}
                aria-pressed={family === "ml"}
                onClick={() => setFamily("ml")}
              >
                机器学习
              </button>
            </div>
            <label className="rw-analysis-source">分析数据
              <SearchSelect aria-label="分析数据" value={usingAnalysisTable ? dataset.id : ""} disabled={busy || !analysisTables.length}
                onChange={event => { previewHistory.reset(); chooseData(event.target.value); }}>
                <option value="" disabled>选择分析用表</option>
                {analysisTables.map(table => <option key={table.id} value={table.id}>{tableTitle(table)}</option>)}
              </SearchSelect>
            </label>
            {!usingAnalysisTable && !analysisTables.length && <div className="rw-analysis-recover"><p>当前项目还没有可分析的表格。</p><button onClick={() => { setAnalysisOpen(false); setTab("data"); }}>打开数据</button></div>}
            {usingAnalysisTable && (preview.isError || (family === "statistics" && methods.isError)) &&
              <div className="rw-analysis-recover" role="alert"><span>{preview.isError ? "未能读取此表的列。" : "未能读取统计方法。"}</span>
                <button onClick={() => { if (preview.isError) void preview.refetch(); if (family === "statistics" && methods.isError) void methods.refetch(); }}>重试</button></div>}
            {usingAnalysisTable && (preview.isLoading || (family === "statistics" && methods.isLoading)) && <p role="status">正在读取分析设置…</p>}
            {usingAnalysisTable && dataset && preview.data && family === "ml" && (
              <WorkspaceTaskML
                key={`${dataset.id}:${mlSeed}`}
                dataset={dataset}
                columns={preview.data.columns}
                isRunning={busy}
                initial={value.ml_draft ?? null}
                editor={value.ml_editor ?? null}
                onChange={mlChanged}
                onSubmit={(config) => void executeML(config)}
              />
            )}
            {family === "statistics" && usingAnalysisTable && dataset && preview.data && methods.data && (
                <StatisticsConfigForm
                  key={dataset.id}
                  methods={methods.data.methods}
                  columns={preview.data.columns}
                  isRunning={busy}
                  onSubmit={(config) => void execute(config)}
                  onConfigChange={configurationChanged}
                  proposal={proposal}
                  compact
                />
            )}
                </div>
                <div className="rw-inline-actions"><button onClick={() => setAnalysisOpen(false)}>返回研究问题</button>{busy && <span role="status">运行中</span>}</div>
              </DialogContent>
            </Dialog>
          </section>
          <section
            role="tabpanel"
            id={`panel-${initial.id}-figures`}
            className={readingPlots?"qp-reading-panel":undefined}
            aria-labelledby={`tab-${initial.id}-figures`}
            hidden={tab !== "figures"}
          >
            <div hidden={plotEditorOpen || readingPlots}>
            {hasPlotRuns && <button className="rw-preview-back" onClick={()=>setStatFiguresOpen(false)}>返回已保存的绘图</button>}
            {!!analyses.data?.length && <label className="rw-figure-source">绘图结果 <SearchSelect aria-label="绘图结果" value={current?.id ?? ""} onChange={event => setSelected(event.target.value)}>
              {analyses.data.filter(item => item.payload.scope.statistical_run_id).map(item => <option key={item.id} value={item.id}>{item.payload.title}</option>)}
            </SearchSelect></label>}
            <WorkspaceTaskFigures
              onAdjust={askAboutFigure}
              detachFigure={id=>setFigureTarget(target=>target?.id===id?null:target)}
              hasPlots={figureCount > value.figure_ids.length}
              openPlot={() => setPlotEditorOpen(true)}
              task={initial.id}
              projectId={projectId}
              title={value.title}
              ids={value.figure_ids}
              requestedId={requestedFigure}
              analysis={current}
              run={run.data ?? null}
              busy={busy}
              added={async (figure) => {
                task.update((prior) => ({
                  ...prior,
                  figure_ids: [...prior.figure_ids, figure.id],
                }));
                await task.flush();
              }}
            />
            </div>
            <div className="qp-plot-area" hidden={hasPlotRuns && statFiguresOpen && !plotEditorOpen}>
            <WorkspaceTaskPlots
              open={plotEditorOpen}
              visible={active && tab === "figures"}
              onOpen={() => setPlotEditorOpen(true)}
              onClose={() => {setPlotEditorOpen(false);setStatFiguresOpen(false);}}
              onStyle={(row,name)=>{setSelectedPlot(row.id);askAboutFigure(row,name);}}
              initialDatasetId={value.dataset_id}
              initialDraftId={plotDraftId}
              openRequest={plotOpenRequest}
              projectId={projectId}
              taskId={initial.id}
              ids={value.plot_run_ids ?? []}
              versionOrder={value.plot_version_order}
              onRemove={async (id, group, order) => {
                await task.flush();
                await task.removePlotVersion(id, group, order);
                setFigureTarget(target => target?.id === id ? null : target);
              }}
              selectedId={selectedPlot}
              onSelect={id=>{setSelectedPlot(id);setFigureTarget(target=>target?.id===id?target:null);}}
              onAdded={linkPlot}
            />
            </div>
            {readingPlots && <div className="qp-other-figures"><button onClick={()=>setStatFiguresOpen(true)}>{value.figure_ids.length?`统计结果图 · ${value.figure_ids.length}`:"从分析结果绘图"}</button></div>}
            {!!value.archived_figure_ids?.length && (
              <ArchivedTaskLinks
                projectId={projectId}
                kind="figure"
                ids={value.archived_figure_ids}
              />
            )}
          </section>
          <section
            role="tabpanel"
            id={`panel-${initial.id}-results`}
            aria-labelledby={`tab-${initial.id}-results`}
            hidden={tab !== "results"}
          >
            {current && analyses.data ? (
              <TaskResultToolbar key={current.id} rows={analyses.data} current={current} heading={resultHeading}
                adoptedId={value.adopted_id} comparison={comparison}
                onChoose={id => { setSelected(id); if (id === compare) setCompare(""); }}
                onCompare={setCompare}
                canEdit={!busy && !!(current.payload.statistics || current.payload.ml)} onEdit={editResult}
                canAdopt={!busy && (!!run.data?.result || mlRun.data?.status === "completed")}
                onAdopt={() => { task.update({ adopted_id: current.id }); void task.flush().catch(() => undefined); }}
                canPlot={!!run.data?.result}
                onPlot={() => { setStatFiguresOpen(true); setPlotEditorOpen(false); setTab("figures"); }}
                onDownload={exportResult}
                onAdd={addAnalysis} onReference={() => setShowHistory(true)} canReference={!!dataset && !busy}>
                <ResultSource datasetId={savedSourceId} name={resultSource && tableTitle(resultSource)}
                  createdAt={run.data?.created_at ?? mlRun.data?.created_at}/>
              </TaskResultToolbar>
            ) : (
              <div className="rw-content-tools"><h2>{locale === "zh-CN" ? "分析与结果" : "Results"}</h2>
                <div className="rw-inline-actions">
                  <button disabled={!dataset || busy} onClick={() => setShowHistory(true)}>引用已有运行</button>
                  <button onClick={addAnalysis}><PlusIcon size={15}/>添加分析</button>
                </div>
              </div>
            )}
            {showHistory && (
              <div className="rw-existing-results">
                <div className="rw-content-tools"><h3>引用已有运行</h3><button aria-label="关闭已有运行" onClick={() => setShowHistory(false)}><XIcon size={16}/></button></div>
                <p className="rw-muted">
                  将本数据集的真实运行加入当前研究问题，也可用于引用 Agent
                  生成的结果。
                </p>
                {history.isPending ? (
                  <p>读取统计记录…</p>
                ) : history.isError ? (
                  <p role="alert">{history.error.message}</p>
                ) : (
                  history.data?.runs.map((item) => (
                    <button
                      key={item.id}
                      disabled={busy}
                      onClick={() => void attachRun(item.id)}
                    >
                      {methodName(item.method)} · {item.status} ·{" "}
                      {new Date(item.created_at).toLocaleString()}
                    </button>
                  ))
                )}
                {modelHistory.isPending ? (
                  <p>读取模型记录…</p>
                ) : modelHistory.isError ? (
                  <p role="alert">{modelHistory.error.message}</p>
                ) : (
                  modelHistory.data?.items
                    .filter(
                      (item) =>
                        item.dataset_id === dataset?.id && !item.archived,
                    )
                    .map((item) => (
                      <button
                        key={item.id}
                        disabled={busy}
                        onClick={() => void attachML(item.id)}
                      >
                        机器学习 · {item.title} · {item.status}
                      </button>
                    ))
                )}
                {!history.isPending &&
                  !modelHistory.isPending &&
                  !history.data?.runs.length &&
                  !modelHistory.data?.items.some(
                    (item) => item.dataset_id === dataset?.id && !item.archived,
                  ) && <p>此数据集暂无可引用的运行记录。</p>}
              </div>
            )}
            {analyses.isError && (
              <p role="alert">
                {analyses.error.message}
                <button onClick={() => void analyses.refetch()}>
                  重新读取
                </button>
              </p>
            )}
            {current ? (
              <div className={comparison ? "rw-results-compare qr-comparing" : ""}>
                <SavedTaskResult key={current.id} analysis={current} datasets={datasets} showHeading={!!comparison}/>
                {comparison && <SavedTaskResult key={comparison.id} analysis={comparison} datasets={datasets} showHeading showSource/>}
              </div>
            ) : analyses.isPending ? (
              <p role="status">正在读取结果…</p>
            ) : !analyses.isError && (
              <p className="rw-empty">尚无分析结果。</p>
            )}
            {!!value.archived_analysis_ids?.length && (
              <ArchivedTaskLinks
                projectId={projectId}
                kind="analysis"
                ids={value.archived_analysis_ids}
              />
            )}
          </section>
        </div>
      </section>
      <aside
        className="rw-agent"
        hidden={!agentOpen}
        aria-label="当前研究问题的 Agent"
      >
        <ResizeHandle label="调整 Agent 宽度" value={rightWidth} onChange={onRightWidthChange} direction={-1} min={280} max={rightMax} />
        <div className="rw-agent-heading">
          <strong>
            <MessageSquareIcon size={16} />
            Agent
          </strong>
          <button onClick={onCloseAgent} aria-label="收起 Agent">
            <XIcon size={18} />
          </button>
        </div>
        <div className="qp-agent-chat">
        <ProjectAssistant
          figureTarget={figureTarget}
          onDetachFigure={()=>setFigureTarget(null)}
          projectId={projectId}
          contextKey={initial.id}
          datasetId={value.dataset_id}
          contextRevision={JSON.stringify([value.dataset_id, value.question, value.exploration_context, value.statistics_draft, value.ml_draft, value.ml_editor])}
          beforeSend={async () => ({ datasetId: (await task.flush()).payload.dataset_id })}
          onCompleted={assistantCompleted}
          onOpenResult={openAssistantResult}
          initialQuestion={`${value.question || value.title}${value.exploration_context ? `\n\n项目探索上下文（用户带入）：\n${value.exploration_context}` : ""}`}
          initialDatasetId={dataset?.id}
          onApplyStatistics={(config) => {
            task.update({ statistics_draft: config });
            setProposal({ id: Date.now(), config });
            setFamily("statistics");
            setTab("settings");
            setAnalysisOpen(true);
          }}
          manualSource={
            dataset
              ? { datasetId: dataset.id, sha256: dataset.sha256 }
              : undefined
          }
        />
        </div>
      </aside>
    </div>
  );
}

function ArchivedTaskLinks({
  projectId,
  kind,
  ids,
}: {
  projectId: string;
  kind: "analysis" | "figure";
  ids: string[];
}) {
  return (
    <section className="rw-source-details">
      <h3>项目文件中保留的{kind === "analysis" ? "分析与结果" : "图表"}</h3>
      <p>原结果作为只读历史保存，可查看来源、导出或从历史继续分析。</p>
      <div className="rw-inline-actions">
        {ids.map((id, index) => (
          <DesktopLink
            key={id}
            href={`/projects/${projectId}/history?kind=${kind}&sourceId=${id}`}
          >
            打开{kind === "analysis" ? "分析" : "图表"} {index + 1}
          </DesktopLink>
        ))}
      </div>
    </section>
  );
}

function SavedTaskResult({ analysis, datasets, showHeading, showSource }: {
  analysis: Analysis; datasets: Dataset[]; showHeading?: boolean; showSource?: boolean;
}) {
  const runId = analysis.payload.scope.statistical_run_id;
  const query = useQuery({
    queryKey: ["statistics-run", runId],
    queryFn: ({ signal }) => statisticsApi.getRun(runId!, signal),
    enabled: !!runId,
  });
  const sourceId = query.data?.dataset_id ?? analysis.payload.scope.dataset_id;
  const source = datasets.find(item => item.id === sourceId);
  const location = showSource && <ResultSource datasetId={sourceId}
    name={source && datasetTitle(source, datasets)} createdAt={query.data?.created_at}/>;
  if (analysis.payload.scope.ml_run_id)
    return (
      <section className="rw-saved-result">
        {showHeading && <h3>{analysis.payload.title}</h3>}
        {location}
        <WorkspaceMLResult runId={analysis.payload.scope.ml_run_id} />
      </section>
    );
  return (
    <section className="rw-saved-result">
      {showHeading && <h3>{analysis.payload.title}</h3>}
      {location}
      {!runId ? (
        <p className="rw-muted">
          本次配置已保留，尚无绑定结果。若运行中断，可从“引用已有运行”恢复。
        </p>
      ) : query.isPending ? (
        <p role="status">读取结果…</p>
      ) : query.isError ? (
        <p role="alert">{query.error.message}</p>
      ) : query.data.result ? (
        <>
          <StatisticsResultView
            workspaceReader
            result={query.data.result}
            inputConfig={query.data.input_config}
            missingPolicy={query.data.missing_value_policy}
          />
        </>
      ) : (
        <p role="alert">
          {query.data.error_message || `运行状态：${query.data.status}`}
        </p>
      )}
    </section>
  );
}

function WorkspaceTaskFigures({
  onAdjust,
  detachFigure,
  hasPlots,
  openPlot,
  task,
  projectId,
  title,
  ids,
  requestedId,
  analysis,
  run,
  added,
  busy,
}: {
  onAdjust: (row: Figure) => void;
  detachFigure: (id: string) => void;
  hasPlots: boolean;
  openPlot: () => void;
  task: string;
  projectId: string;
  title: string;
  ids: string[];
  requestedId?: string;
  analysis?: Analysis;
  run: StatisticalRun | null;
  added: (figure: Figure) => Promise<void>;
  busy: boolean;
}) {
  const [error, setError] = useState(""),
    [creating, setCreating] = useState(false),
    [field, setField] = useState("");
  const [chosen, setChosen] = useState("");
  const [compared, setCompared] = useState("");
  const client = useQueryClient();
  const figures = useQuery({
    queryKey: ["task-figures", task, ids],
    queryFn: ({ signal }) =>
      Promise.all(
        ids.map((id) =>
          workspaceApi.read(id, signal).then((row) => {
            if (row.payload.kind !== "figure" || row.project_id !== projectId)
              throw new Error("图表来源不可用");
            return row as Figure;
          }),
        ),
      ),
  });
  const selected =
    figures.data?.find((row) => row.id === chosen) ?? figures.data?.[0];
  const comparison = figures.data?.find((row) => row.id === compared && row.id !== selected?.id);
  useEffect(() => { if (requestedId) setChosen(requestedId); }, [requestedId]);
  useEffect(() => { setField(""); setError(""); }, [run?.id]);
  useEffect(() => { if (!chosen && figures.data?.length) setChosen(figures.data[0].id); }, [chosen, figures.data]);
  const supported =
    !!run?.result &&
    run.id === analysis?.payload.scope.statistical_run_id &&
    run.status === "completed" &&
    [
      "descriptive",
      "pearson",
      "spearman",
      "linear_regression",
      "welch_t",
    ].includes(run.method);
  async function create() {
    if (!analysis || !run?.result || !supported || creating) return;
    setCreating(true);
    setError("");
    try {
      let spec: FigureSpec;
      if (run.method === "welch_t") {
        const ci =
          run.result.confidence_interval?.estimand === "mean_difference";
        spec = {
          kind: "figure",
          schema_version: 1,
          title: `${title} · ${ids.length + 1}`.slice(0, 160),
          scope: { ...analysis.payload.scope, statistical_config: null },
          chart_type: "welch_groups_difference",
          renderer_version: "workspace-figure-v1",
          fields: (run.input_config?.variables ?? []) as string[],
          layers: ci ? ["group_means", "difference_ci"] : ["group_means"],
          interval: "mean_difference_ci",
          group_order: Object.keys(run.result.descriptive_statistics),
          unit: "",
          theme: "paper",
          width: 1200,
          height: 800,
          dpi: 300,
          font_size: 16,
          caption: "",
        };
      } else {
        const chart =
          run.method === "descriptive"
            ? "distribution"
            : run.method === "linear_regression"
              ? "ols_coefficients"
              : "correlation_scatter";
        const fields =
          chart === "distribution"
            ? [field || String((run.input_config?.variables as string[])[0])]
            : chart === "ols_coefficients"
              ? [String(run.input_config?.target)]
              : (run.input_config?.variables as string[]);
        const projection = await workspaceApi.generateFigure(
          {
            ...analysis.payload.scope,
            statistical_config: null,
            ml_config: null,
            ml_run_id: null,
            session_id: null,
            session_revision: null,
          },
          chart,
          fields,
          12,
          crypto.randomUUID(),
        );
        const layers: FigureSpec["layers"] =
          chart === "distribution"
            ? (Array.isArray(projection.payload.data.groups) ? ["box"] : ["histogram", "box"])
            : chart === "correlation_scatter"
              ? ["points"]
              : projection.payload.data.ci_available
                ? ["coefficients", "coefficient_ci"]
                : ["coefficients"];
        spec = {
          kind: "figure",
          schema_version: 2,
          title: (chart==="distribution" ? (projection.payload.data.group_variable ? `${fields[0]} 按 ${projection.payload.data.group_variable} 的分布` : `${fields[0]} 的分布`) : `${analysis.payload.title} · ${fields.join("、")}`).slice(0, 160),
          scope: projection.payload.scope,
          chart_type: chart,
          renderer_version: "workspace-figure-v2",
          fields,
          layers,
          interval: layers.includes("coefficient_ci")
            ? "coefficient_ci"
            : "none",
          snapshot_id: projection.id,
          content_sha256: projection.payload.content_sha256,
          group_order: [],
          unit: "",
          theme: "paper",
          width: 1200,
          height: Array.isArray(projection.payload.data.groups) ? Math.min(3000, Math.max(800, projection.payload.data.groups.length * 70 + 320)) : 800,
          dpi: 300,
          font_size: 10,
          caption: "",
        };
      }
      const saved = (await workspaceApi.save(projectId, spec)) as Figure;
      await added(saved);
      setChosen(saved.id);
      await client.invalidateQueries({ queryKey: ["task-figures", task] });
    } catch (reason) {
      setError(figureFailure(reason));
    } finally {
      setCreating(false);
    }
  }
  return (
    <>
      <div className="rw-content-tools">
        <div>
          <h2>图表作品</h2>
        </div>
        <div className="rw-inline-actions">
          {run?.method === "descriptive" && (
            <SearchSelect
              aria-label="绘图变量"
              value={field}
              onChange={(event) => setField(event.target.value)}
            >
              <option value="">首个分析变量</option>
              {((run.input_config?.variables ?? []) as string[]).map((name) => (
                <option key={name}>{name}</option>
              ))}
            </SearchSelect>
          )}
          <button
            disabled={!supported || busy || creating}
            onClick={() => void create()}
          >
            <PlusIcon size={16} />
            {creating ? "生成中…" : "从当前结果生成"}
          </button>
          <button onClick={openPlot}>绘图工作台</button>
        </div>
      </div>
      {error && (
        <p role="alert" className="rw-error">
          {error}
        </p>
      )}
      {figures.isError && <p role="alert">{figures.error.message}</p>}
      {figures.data?.length ? (
        <>
          <div className="rw-analysis-switcher">
            {figures.data.map((row) => (
              <button
                key={row.id}
                className={selected?.id === row.id ? "active" : ""}
                onClick={() => setChosen(row.id)}
              >
                {row.payload.title}
              </button>
            ))}
          </div>
          <label className="rw-figure-compare">并排比较 <SearchSelect value={compared} onChange={(event) => setCompared(event.target.value)}>
            <option value="">不比较</option>
            {figures.data.filter((row) => row.id !== selected?.id).map((row) => <option key={row.id} value={row.id}>{row.payload.title}</option>)}
          </SearchSelect></label>
          <div className={comparison ? "rw-results-compare" : ""}>
          {selected && (
            <SavedFigure
              key={`${selected.id}:${selected.revision}`}
              record={selected}
              onAdjust={onAdjust}
              detachFigure={detachFigure}
              onSaved={async () => {
                await client.invalidateQueries({
                  queryKey: ["task-figures", task],
                });
              }}
            />
          )}
          {comparison && <SavedFigure key={`${comparison.id}:${comparison.revision}`} record={comparison} onAdjust={onAdjust} detachFigure={detachFigure} onSaved={async () => {
            await client.invalidateQueries({ queryKey: ["task-figures", task] });
          }} />}
          </div>
        </>
      ) : !hasPlots ? (
        <p className="rw-empty">暂无图表</p>
      ) : null}
    </>
  );
}

export function SavedFigure({
  record,
  onSaved,
  onAdjust,
  detachFigure,
}: {
  record: Figure;
  onSaved: () => Promise<void>;
  onAdjust: (row: Figure) => void;
  detachFigure: (id: string) => void;
}) {
  const detach = useRef(detachFigure);
  useEffect(() => () => detach.current(record.id), [record.id, record.revision]);
  const { locale } = useLanguage();
  const [spec, setSpec] = useState(record.payload),
    [styling, setStyling] = useState(false),
    [error, setError] = useState("");
  const run = useQuery({
    queryKey: ["statistics-run", spec.scope.statistical_run_id],
    queryFn: ({ signal }) =>
      statisticsApi.getRun(spec.scope.statistical_run_id!, signal),
  });
  const snapshot = useQuery({
    queryKey: ["figure-data", spec.snapshot_id],
    queryFn: ({ signal }) =>
      workspaceApi.read(spec.snapshot_id!, signal).then((row) => {
        if (row.payload.kind !== "figure_data")
          throw new Error("图形快照不可用");
        return row as WorkspaceRecord<FigureData>;
      }),
    enabled: spec.schema_version === 2 && !!spec.snapshot_id,
  });
  const dirty = JSON.stringify(spec) !== JSON.stringify(record.payload);
  async function save() {
    try {
      setError("");
      await workspaceApi.save(record.project_id, spec, record);
      await onSaved();
    } catch (reason) {
      setError(failure(reason));
    }
  }
  const flushStyle = useRef<() => Promise<void>>(async () => undefined);
  flushStyle.current = async () => {
    if (!dirty) return;
    try {
      await workspaceApi.save(record.project_id, spec, record);
      await onSaved();
    } catch (reason) {
      setError(failure(reason));
      throw reason;
    }
  };
  useEffect(
    () => registerProjectEditor(record.project_id, () => flushStyle.current()),
    [record.project_id],
  );
  useEffect(() => {
    if (!dirty) return;
    const leave = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", leave);
    return () => window.removeEventListener("beforeunload", leave);
  }, [dirty]);
  async function download(format: "png" | "svg", signal: AbortSignal) {
    if (dirty || !run.data) throw new Error("先保存图表样式再导出");
    const fresh = await workspaceApi.read(record.id, signal, record.revision);
    if (fresh.payload.kind !== "figure") throw new Error("图表不可用");
    const result = await statisticsApi.getRun(run.data.id, signal);
    const projection = spec.snapshot_id
      ? ((await workspaceApi.read(
          spec.snapshot_id,
          signal,
        )) as WorkspaceRecord<FigureData>)
      : null;
    return saveGeneratedFile(
      await figureBlob(
        figureSvg(fresh.payload, result, projection, locale),
        fresh.payload,
        format,
      ),
      `${spec.title}.${format}`,
      signal,
      async () => {
        const latest = await workspaceApi.read(
          record.id,
          signal,
          record.revision,
        );
        const latestRun = await statisticsApi.getRun(result.id, signal);
        if (
          JSON.stringify(latest.payload) !== JSON.stringify(fresh.payload) ||
          JSON.stringify(latestRun) !== JSON.stringify(result)
        )
          throw new Error("图表来源已改变，请重新打开图表");
      },
      locale,
    );
  }
  return (
    <section className="rw-figure-work">
      <AnalysisOwnership title={spec.title} scope={spec.scope} view="图表" revision={record.revision} recordId={record.id}/>
      <div className="rw-content-tools">
        <h3>{spec.title}</h3>
        <div className="rw-inline-actions">
          <button onClick={() => setStyling(!styling)} aria-expanded={styling}>
            图表样式
          </button>
          <button disabled={dirty} title={dirty ? "请先保存当前修改" : undefined} onClick={()=>onAdjust(record)}>AI 调图</button>
          <DownloadButton
            identity={`${record.id}:png`}
            disabled={dirty || !run.data}
            download={(signal) => download("png", signal)}
          >
            PNG
          </DownloadButton>
          <DownloadButton
            identity={`${record.id}:svg`}
            disabled={dirty || !run.data}
            download={(signal) => download("svg", signal)}
          >
            SVG
          </DownloadButton>
        </div>
      </div>
      {styling && (
        <div className="rw-figure-style">
          <label>
            标题
            <input
              maxLength={160}
              value={spec.title}
              onChange={(event) =>
                setSpec({ ...spec, title: event.target.value })
              }
            />
          </label>
          <label>
            主题
            <SearchSelect
              value={spec.theme}
              onChange={(event) =>
                setSpec({
                  ...spec,
                  theme: event.target.value as FigureSpec["theme"],
                })
              }
            >
              <option value="paper">论文</option>
              <option value="research">研究</option>
            </SearchSelect>
          </label>
          <label>
            字号
            <input
              type="number"
              min={8}
              max={32}
              value={spec.font_size}
              onChange={(event) =>
                setSpec({ ...spec, font_size: Number(event.target.value) })
              }
            />
          </label>
          <label>
            宽
            <input
              type="number"
              min={600}
              max={4000}
              value={spec.width}
              onChange={(event) =>
                setSpec({ ...spec, width: Number(event.target.value) })
              }
            />
          </label>
          <label>
            高
            <input
              type="number"
              min={400}
              max={3000}
              value={spec.height}
              onChange={(event) =>
                setSpec({ ...spec, height: Number(event.target.value) })
              }
            />
          </label>
          <label>
            图注
            <input
              maxLength={1000}
              value={spec.caption}
              onChange={(event) =>
                setSpec({ ...spec, caption: event.target.value })
              }
            />
          </label>
          <button
            disabled={!dirty || !spec.title.trim()}
            onClick={() => void save()}
          >
            保存新版本
          </button>
          {dirty && <button onClick={()=>setSpec(record.payload)}>撤销预览</button>}
        </div>
      )}
      {error && (
        <p role="alert" className="rw-error">
          {error}
        </p>
      )}
      {run.isError || snapshot.isError ? (
        <p role="alert">{run.error?.message || snapshot.error?.message}</p>
      ) : run.data && (spec.schema_version === 1 || snapshot.data) ? (
        <WorkspaceFigure
          spec={spec}
          run={run.data}
          snapshot={snapshot.data ?? null}
        />
      ) : (
        <p role="status">正在读取保存的图表…</p>
      )}
      {dirty && (
        <p role="status" className="rw-muted">
          样式修改尚未保存
        </p>
      )}
    </section>
  );
}
