"use client";

import { SearchSelect } from "@/components/ui/search-select";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  PlayIcon,
  SquareIcon,
  PlusIcon,
  SaveIcon,
  CodeIcon,
  ExternalLinkIcon,
  RefreshCwIcon,
} from "lucide-react";
import { ApiError, apiFetch } from "@/lib/api-client";
import { datasetsApi } from "@/lib/api-endpoints";
import { downloadApiFile, readApiFileBlob } from "@/lib/api-download";
import { usePlotDraft } from "@/lib/use-plot-draft";
import { samePlotInput } from "@/lib/plot-input";
import {
  plotStarter,
  plotStarters,
  type PlotStarterKind,
  type PlotStarterSelection,
} from "@/lib/plot-starters";
import { registerProjectEditor } from "@/lib/project-save";
import {
  plotsApi,
  plotActive,
  plotCodeApi,
  type PlotCodeFile,
  type PlotStylePatch,
} from "@/lib/plots-api";
import { useLanguage } from "@/providers/language-provider";
import type { PreviewContext } from "@/lib/preview-types";
import type { PlotRun, WorkspaceRecord } from "@/lib/workspace-api";
import { usePreviewPublication } from "./preview-workspace";
import { ResearchFailure } from "./formal-research-common";
import { DownloadButton } from "./download-button";
import { useWorkspacePreferences } from "@/providers/workspace-preferences-provider";
import { DesktopLink } from "./desktop-link";
import { Button } from "./ui/button";
import controls from "./plot-controls.module.css";
import { PlotAgentRequest } from "./plot-agent-request";
import { plotCodeKind } from "@/lib/task-plot-groups";

type Run = WorkspaceRecord<PlotRun>;
function timeColumn(columns: string[]): string {
  return columns.find((name) => /^(start|time|timestamp|time_seconds|elapsed_seconds)$/i.test(name)) ?? columns[0] ?? "";
}
function trendColumns(columns: string[], time: string): string[] {
  const means = columns.filter((name) => name !== time && /_mean$/i.test(name));
  return (means.length ? means : columns.filter((name) => name !== time && !/^(index|end|duration)|samples|missing|_count$/i.test(name))).slice(0, 2);
}
function belongsToPlot(row: Run, draft: string | undefined, rows: Run[]): boolean {
  const visited = new Set<string>();
  let current: Run | undefined = row;
  while (current && !visited.has(current.id)) {
    if (current.payload.draft_id === draft) return true;
    visited.add(current.id);
    const parent: unknown = current.payload.metadata.revision_parent_id ?? current.payload.metadata.style_parent_id;
    current = typeof parent === "string" ? rows.find((item) => item.id === parent) : undefined;
  }
  return false;
}
export function PlotWorkbench({
  projectId,
  taskId,
  initialId,
  initialRunId,
  initialDatasetId,
  embedded = false,
  onRunCreated,
}: {
  projectId: string;
  taskId?: string;
  initialId?: string | null;
  initialRunId?: string | null;
  initialDatasetId?: string;
  embedded?: boolean;
  onRunCreated?: (run: WorkspaceRecord<PlotRun>) => Promise<void>;
}) {
  const editor = <PlotEditor key={`${projectId}:${initialId ?? "latest"}:${initialRunId ?? "latest"}`}
    projectId={projectId} taskId={taskId} initialId={initialId} initialRunId={initialRunId} initialDatasetId={initialDatasetId}
    embedded={embedded} onRunCreated={onRunCreated} />;
  if (embedded) return editor;
  return (
    <div className="plot-dedicated-workspace">
      <header className="plot-workspace-heading">
        <DesktopLink href={`/projects/${projectId}/workspace`}>返回项目</DesktopLink>
        <h1>绘图工作台</h1>
      </header>
      {editor}
    </div>
  );
}
function PlotEditor({
  projectId,
  taskId,
  initialId,
  initialRunId,
  initialDatasetId,
  embedded,
  onRunCreated,
}: {
  projectId: string;
  taskId?: string;
  initialId?: string | null;
  initialRunId?: string | null;
  initialDatasetId?: string;
  embedded?: boolean;
  onRunCreated?: (run: WorkspaceRecord<PlotRun>) => Promise<void>;
}) {
  const { locale } = useLanguage();
  const preferences = useWorkspacePreferences();
  const copy = useCallback(
    (zh: string, en: string) => (locale === "zh-CN" ? zh : en),
    [locale],
  );
  const draft = usePlotDraft(projectId, initialId);
  const savedPlotId = draft.saved?.id;
  useEffect(() => {
    if (embedded || !draft.ready || draft.dirty || !savedPlotId) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("plotId") !== savedPlotId) {
      url.searchParams.set("plotId", savedPlotId);
      window.history.replaceState(null, "", url.href);
    }
  }, [draft.ready, draft.dirty, savedPlotId, embedded]);
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [editor, setEditor] = useState("");
  const [fileState, setFileState] = useState<PlotCodeFile | null>(null);
  const editors = useQuery({
    queryKey: ["plot-editors", projectId],
    queryFn: ({ signal }) => plotCodeApi.editors(projectId, signal),
    retry: false,
  });
  const codeFile = useQuery({
    queryKey: [
      "plot-code-file",
      projectId,
      draft.saved?.id,
      draft.saved?.revision,
    ],
    queryFn: ({ signal }) =>
      plotCodeApi.read(projectId, draft.saved!.id, signal),
    enabled: !!draft.saved && !draft.dirty,
    retry: false,
  });
  useEffect(() => {
    setFileState(codeFile.data ?? null);
  }, [codeFile.data, draft.saved?.id]);
  const selectedEditor =
    editor ||
    (preferences.value.editor === "auto"
      ? editors.data?.default_editor
      : preferences.value.editor) ||
    "";
  const editorAvailable = !!editors.data?.editors.some(
    (item) => item.id === selectedEditor,
  );
  const [view, setView] = useState<"both" | "preview" | "code">("preview");
  const [selectedRun, setSelectedRun] = useState<string | null>(initialRunId ?? null);
  const [image, setImage] = useState<{ run: string; url: string } | null>(null);
  const [dpiInput, setDpiInput] = useState("150");
  const [starterPending, setStarterPending] = useState(false);
  const [starterKind, setStarterKind] = useState<PlotStarterKind>("trend");
  const [starterX, setStarterX] = useState("");
  const [starterY, setStarterY] = useState("");
  const [starterGroup, setStarterGroup] = useState("");
  const [trendValues, setTrendValues] = useState<string[] | null>(null);
  const [colorIndex,setColorIndex]=useState(0);
  const [colorChanges, setColorChanges] = useState<Record<string, string>>({});
  const [styleInput, setStyleInput] = useState({ font: "", width: "", height: "", line: "", grid: "" });
  const dpiValid =
    /^\d+$/.test(dpiInput) && Number(dpiInput) >= 72 && Number(dpiInput) <= 600;
  useEffect(() => {
    setDpiInput(String(draft.value.dpi));
  }, [draft.value.dpi, draft.saved?.id]);
  const request = useRef<{
    draft: string;
    revision: number;
    id: string;
  } | null>(null);
  const datasets = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });
  const datasetId = draft.value.scope.dataset_id;
  const dataset = datasets.data?.find((item) => item.id === datasetId);
  const preview = useQuery({
    queryKey: ["plot-data", projectId, datasetId],
    queryFn: ({ signal }) => datasetsApi.preview(datasetId!, 5, signal),
    enabled: !!datasetId,
    retry: false,
  });
  const runs = useQuery({
    queryKey: ["plot-runs", projectId, draft.saved?.id],
    queryFn: ({ signal }) => plotsApi.list(projectId, undefined, signal),
    enabled: !!draft.saved,
    refetchInterval: (query) =>
      query.state.data?.runs.some((item) => plotActive(item.payload.status) && belongsToPlot(item, draft.saved?.id, query.state.data!.runs))
        ? 700
        : false,
    retry: false,
  });
  const allRuns = runs.data?.runs ?? [];
  const items = allRuns.filter((item) => belongsToPlot(item, draft.saved?.id, allRuns));
  const numericColumns = (preview.data?.columns ?? [])
    .filter((column) =>
      ["integer", "float", "numeric", "decimal"].includes(column.inferred_type),
    )
    .map((column) => column.name);
  const x = numericColumns.includes(starterX)
    ? starterX
    : timeColumn(numericColumns);
  const y = numericColumns.includes(starterY)
    ? starterY
    : (numericColumns.find((name) => name !== x) ?? "");
  const fields = (preview.data?.columns ?? []).map((column) => column.name);
  const group = fields.includes(starterGroup) ? starterGroup : "";
  const trendNumeric = (trendValues ?? trendColumns(numericColumns, x)).filter((name) => name !== x && numericColumns.includes(name));
  const selectedNumeric = numericColumns.filter((name) =>
    draft.value.scope.columns?.includes(name),
  );
  const restoredControls = useRef("");
  useEffect(() => {
    if (!draft.ready || !draft.saved?.id || restoredControls.current === draft.saved.id) return;
    restoredControls.current = draft.saved.id;
    const code=draft.value.code;
    const kinds:Record<string,PlotStarterKind>={"热图":"heatmap","直方图":"hist","箱线图":"box","小提琴图":"violin","均值图":"mean","散点图":"scatter","折线图":"trend"};
    const kind=kinds[plotCodeKind(code) || ""];
    if (!kind) return;
    setStarterKind(kind); setStarterPending(false);
    const field=(key:string)=>{const match=code.match(new RegExp('\\b'+key+'\\s*=\\s*("(?:[^"\\\\]|\\\\.)*")'));if(!match)return "";try{return String(JSON.parse(match[1]));}catch{return "";}};
    const grouped=["box","violin","mean"].includes(kind);
    setStarterX(grouped?field("y"):field("x"));setStarterY(field("y"));setStarterGroup(grouped?field("x"):field("hue"));
  }, [draft.ready,draft.saved?.id,draft.value.code]);
  const pairedStarter =
    starterKind === "scatter" || starterKind === "dashboard";
  const starterValid =
    datasetId &&
    (starterKind === "trend"
      ? !!x && trendNumeric.length > 0
      : starterKind === "heatmap"
      ? selectedNumeric.length >= 2
      : !!x && (!pairedStarter || (!!y && x !== y)));
  const latest = items[0];
  const active = items.find((item) => plotActive(item.payload.status));
  const chosen = selectedRun
    ? items.find((item) => item.id === selectedRun)
    : items.find((item) => item.payload.status === "completed");
  const shown = chosen?.payload.status === "completed" ? chosen : undefined;
  const savedColors = shown?.payload.metadata.colors;
  const colors = (Array.isArray(savedColors) ? savedColors : [])
    .filter((value): value is string => typeof value === "string" && /^#[a-f0-9]{6}([a-f0-9]{2})?$/i.test(value))
    .filter((value) => !/00$/i.test(value.slice(7)) && !/^#([a-f0-9]{2})\1\1/i.test(value));
  const styleChanged = Object.keys(colorChanges).length > 0 || Object.values(styleInput).some(Boolean);
  useEffect(() => {
    setColorChanges({});
    setStyleInput({ font: "", width: "", height: "", line: "", grid: "" });
  }, [shown?.id]);
  useEffect(() => {
    setStarterX("");
    setStarterY("");
    setTrendValues(null);
    setStarterGroup("");
  }, [datasetId]);
  usePreviewPublication({
    scope: draft.value.scope,
    page: "Python 图形",
    draft: draft.value.title,
    adviceKind: "plot",
    adviceDraftId: draft.saved?.id,
    adviceQuestion: draft.value.ai_question ?? "",
    saveAdviceQuestion: async (question) => {
      draft.update({ ...draft.peek(), ai_question: question });
      await draft.flush();
    },
    prepareAdvice: async () => {
      const record = await draft.flush();
      if (!record)
        throw new Error(
          copy(
            "请先选择数据并保存代码。",
            "Select data and save the code first.",
          ),
        );
      return { kind: "plot", draft_id: record.id, revision: record.revision };
    },
    adviceApplied: async () => {
      if (draft.saved) await draft.choose(draft.saved.id);
    },
  });
  useEffect(() => {
    setSelectedRun(draft.saved?.id === initialId ? initialRunId ?? null : null);
  }, [draft.saved?.id, initialRunId, initialId]);
  const shownId = shown?.id;
  useEffect(() => {
    const control = new AbortController();
    let url: string | undefined;
    setImage(null);
    if (shownId)
      void readApiFileBlob(
        plotsApi.file(projectId, shownId, "png"),
        control.signal,
      )
        .then((blob) => {
          if (control.signal.aborted) return;
          url = URL.createObjectURL(blob);
          setImage({ run: shownId, url });
        })
        .catch((failure) => {
          if (!control.signal.aborted) setError(failure);
        });
    return () => {
      control.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [projectId, shownId]);
  async function perform(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  async function openEditor(asNew = false) {
    const saved = await draft.flush();
    if (!saved) return;
    const value = await plotCodeApi.open(
      projectId,
      saved.id,
      saved.revision,
      selectedEditor,
      asNew,
    );
    setFileState(value);
    await codeFile.refetch();
  }
  async function readLocal(replace = false) {
    const saved = await draft.flush();
    if (!saved) return;
    const value = await plotCodeApi.read(projectId, saved.id);
    setFileState(value);
    if (
      !value.available ||
      !value.external_changed ||
      (value.conflict && !replace)
    )
      return;
    await plotCodeApi.import(
      projectId,
      saved.id,
      saved.revision,
      value.file_sha256,
      replace,
    );
    await draft.choose(saved.id);
    setFileState(await plotCodeApi.read(projectId, saved.id));
  }
  const editorCurrent = useRef({ draft, busy });
  editorCurrent.current = { draft, busy };
  useEffect(
    () =>
      registerProjectEditor(projectId, async () => {
        const latest = editorCurrent.current;
        if (latest.busy)
          throw new Error(
            copy(
              "代码文件正在保存，请稍后重试。",
              "The code file is being saved. Try again shortly.",
            ),
          );
        setBusy(true);
        try {
          const saved = await latest.draft.flush();
          if (!saved) return;
          const value = await plotCodeApi.read(projectId, saved.id);
          if (!value.available || !value.external_changed) return;
          setFileState(value);
          if (value.conflict)
            throw new Error(
              copy(
                "应用与本地文件都有修改，请先选择使用哪份代码。",
                "The app and local file both changed. Choose which code to use first.",
              ),
            );
          await plotCodeApi.import(
            projectId,
            saved.id,
            saved.revision,
            value.file_sha256,
            false,
          );
          await latest.draft.choose(saved.id);
        } finally {
          setBusy(false);
        }
      }),
    [projectId, copy],
  );
  async function bind(id: string, selection?: PlotStarterSelection) {
    const detail = await datasetsApi.preview(id, 5);
    const available = detail.columns.filter((column) => ["integer", "float", "numeric", "decimal"].includes(column.inferred_type)).map((column) => column.name);
    const time = available.find((name) => /^(start|time|timestamp|time_seconds|elapsed_seconds)$/i.test(name));
    const values = time ? trendColumns(available, time) : [];
    const inferred: PlotStarterSelection | undefined = time && values.length
      ? { kind: "trend", x: time, y: "", group: "", numeric: values }
      : undefined;
    const chosenStarter = selection ?? inferred;
    const generated = chosenStarter ? plotStarter(chosenStarter, locale) : null;
    const fields =
      generated?.columns ??
      detail.columns.slice(0, 50).map((column) => column.name);
    const context = await apiFetch<PreviewContext>("/api/v1/preview/context", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        dataset_id: id,
        columns: fields,
      }),
    });
    const numeric = detail.columns.find((column) =>
      ["integer", "float", "numeric", "decimal"].includes(column.inferred_type),
    );
    const field = numeric?.name ?? fields[0];
    await draft.choose("");
    const code =
      generated?.code ??
      (numeric
        ? `import matplotlib.pyplot as plt\nimport seaborn as sns\n\nfig, ax = plt.subplots(figsize=(8, 5), layout="constrained")\nsns.histplot(data=df, x=${JSON.stringify(field)}, ax=ax)\nax.set(xlabel=${JSON.stringify(field)}, ylabel=${JSON.stringify(copy("频数", "Count"))})\n`
        : `import matplotlib.pyplot as plt\nimport seaborn as sns\n\nfig, ax = plt.subplots(figsize=(8, 5), layout="constrained")\nsns.countplot(data=df, x=${JSON.stringify(field)}, ax=ax)\n`);
    draft.update({
      kind: "plot",
      schema_version: 1,
      title:
        `${generated?.title ?? (numeric ? copy("直方图", "Histogram") : copy("类别计数图", "Category counts"))} · ${detail.original_filename}`.slice(
          0,
          160,
        ),
      scope: context.scope,
      code,
      dpi: preferences.value.plotDpi,
    });
    await draft.flush();
    setSelectedRun(null);
    setStarterPending(false);
    if (chosenStarter) {
      setStarterKind(chosenStarter.kind);
      setStarterX(chosenStarter.x);
      setStarterY(chosenStarter.y);
      setStarterGroup(chosenStarter.group);
      setTrendValues(chosenStarter.kind === "trend" ? chosenStarter.numeric : null);
    }
  }
  const initialBinding = useRef(false);
  const initializePlot = useRef<() => void>(() => undefined);
  initializePlot.current = () => {
    if (initialDatasetId) void perform(() => bind(initialDatasetId));
  };
  useEffect(() => {
    if (!draft.ready || initialBinding.current || !initialDatasetId) return;
    initialBinding.current = true;
    if (!draft.saved && !draft.value.scope.dataset_id) initializePlot.current();
  }, [draft.ready, draft.saved, draft.value.scope.dataset_id, initialDatasetId]);
  async function run() {
    if (!dpiValid)
      throw new Error(
        copy(
          "DPI 请输入 72–600 的整数。",
          "Enter an integer DPI from 72 to 600.",
        ),
      );
    if (starterPending && datasetId) {
      await bind(datasetId, { kind: starterKind, x, y, group, numeric: starterKind === "trend" ? trendNumeric : selectedNumeric });
    }
    const saved = await draft.flush();
    if (!saved) throw new Error(copy("请先选择数据。", "Choose data first."));
    let pending = request.current;
    if (
      !pending ||
      pending.draft !== saved.id ||
      pending.revision !== saved.revision
    ) {
      pending = {
        draft: saved.id,
        revision: saved.revision,
        id: crypto.randomUUID(),
      };
      request.current = pending;
    }
    const run = await plotsApi.start(
      projectId,
      saved.id,
      saved.revision,
      pending.id,
    );
    request.current = null;
    client.setQueryData<{ runs: Run[] }>(
      ["plot-runs", projectId, saved.id],
      (old) => ({
        runs: [run, ...(old?.runs ?? []).filter((item) => item.id !== run.id)],
      }),
    );
    setSelectedRun(null);
    await client.invalidateQueries({
      queryKey: ["plot-runs", projectId, saved.id],
    });
    await onRunCreated?.(run);
  }
  async function splitAcousticMeasures() {
    if (!chosen || !draft.saved || draft.dirty) return;
    const saved = draft.saved;
    const run = await apiFetch<Run>(`/api/v1/projects/${projectId}/plots/runs/${chosen.id}/acoustic-overview`, {
      method: "POST", body: JSON.stringify({ expected_revision: saved.revision, request_id: crypto.randomUUID() }),
    });
    await draft.choose(saved.id);
    await runs.refetch();
    setSelectedRun(run.id);
    await onRunCreated?.(run);
  }
  const styleRequest = useRef<{ source: string; patch: string; id: string } | null>(null);
  async function applyStyle() {
    if (!shown) return;
    const patch: PlotStylePatch = { colors: colorChanges };
    for (const [field, key, minimum, maximum] of [
      ["font", "font_size", 6, 48], ["width", "width_inches", 2, 24],
      ["height", "height_inches", 2, 24], ["line", "line_width", 0.2, 12],
    ] as const) {
      if (!styleInput[field]) continue;
      const value = Number(styleInput[field]);
      if (!Number.isFinite(value) || value < minimum || value > maximum) throw new Error(copy(`样式数值应在 ${minimum}–${maximum} 之间。`, `Enter a style value between ${minimum} and ${maximum}.`));
      patch[key] = value;
    }
    if (styleInput.grid) patch.grid = styleInput.grid === "on";
    const serialized = JSON.stringify(patch);
    if (!styleRequest.current || styleRequest.current.source !== shown.id || styleRequest.current.patch !== serialized) {
      styleRequest.current = { source: shown.id, patch: serialized, id: crypto.randomUUID() };
    }
    const styled = await plotsApi.style(projectId, shown.id, patch, styleRequest.current.id);
    styleRequest.current = null;
    client.setQueryData<{ runs: Run[] }>(["plot-runs", projectId, draft.saved?.id], (prior) => ({ runs: [styled, ...(prior?.runs ?? []).filter((item) => item.id !== styled.id)] }));
    setSelectedRun(null);
    await onRunCreated?.(styled);
    await runs.refetch();
  }
  const status = (value: string) =>
    ({
      queued: copy("等待运行", "Queued"),
      running: copy("正在绘图", "Rendering"),
      cancel_requested: copy("正在取消", "Cancelling"),
      cancelled: copy("已取消", "Cancelled"),
      failed: copy("绘图失败", "Failed"),
      interrupted: copy("已中断", "Interrupted"),
      completed: copy("已完成", "Completed"),
    })[value] ?? value;
  const saveState = draft.dirty
    ? copy("未保存", "Unsaved")
    : draft.saved
      ? copy("已保存", "Saved")
      : "";
  return (
    <section
      className="plot-workbench"
      aria-label={copy("图形与代码", "Figure and code")}
    >
      <div className="plot-settings-column">
      <h3>绘图设置</h3>
      <header className="plot-toolbar">
        <div className="plot-title-group">
          <textarea rows={2}
            aria-label={copy("图形标题", "Figure title")}
            maxLength={160}
            disabled={busy || !draft.ready || !datasetId}
            value={draft.value.title}
            onChange={(event) =>
              draft.update({ ...draft.peek(), title: event.target.value })
            }
          />
          <span role="status">{saveState}</span>
        </div>
        <div className="plot-actions">
          <Button
            variant="ghost"
            disabled={busy || !draft.ready}
            onClick={() =>
              void perform(async () => {
                const source = initialDatasetId || datasetId;
                if (source) await bind(source);
                else await draft.choose("");
              })
            }
          >
            <PlusIcon className="size-4" />
            {copy("新图形", "New figure")}
          </Button>
          <Button
            variant="outline"
            disabled={busy || !datasetId || !draft.dirty}
            onClick={() =>
              void perform(async () => {
                await draft.flush();
              })
            }
          >
            <SaveIcon className="size-4" />
            {copy("保存", "Save")}
          </Button>
          {active ? (
            <Button
              variant="outline"
              disabled={busy || active.payload.status === "cancel_requested"}
              onClick={() =>
                void perform(async () => {
                  await plotsApi.cancel(projectId, active.id);
                  await runs.refetch();
                })
              }
            >
              <SquareIcon className="size-4" />
              {copy("取消运行", "Cancel")}
            </Button>
          ) : (
            <Button
              disabled={
                busy ||
                !draft.ready ||
                (starterPending && !starterValid) ||
                !datasetId ||
                (!starterPending && !draft.value.code.trim()) ||
                !dpiValid ||
                !draft.value.scope.columns?.length
              }
              onClick={() => void perform(run)}
            >
              <PlayIcon className="size-4" />
              {copy("生成图形", "Render figure")}
            </Button>
          )}
        </div>
      </header>
      <div className="plot-source-bar">
        <label>
          {copy("数据", "Data")}
          <SearchSelect
            aria-label={copy("绘图数据", "Plot data")}
            disabled={busy || !draft.ready}
            value={datasetId ?? ""}
            onChange={(event) => {
              if (event.target.value)
                void perform(() => bind(event.target.value));
            }}
          >
            <option value="">
              {copy("选择数据，新建图形", "Choose data for a new figure")}
            </option>
            {datasets.data
              ?.filter((item) => item.upload_status === "ready")
              .map((item) => (
                <option value={item.id} key={item.id}>
                  {item.original_filename}
                </option>
              ))}
          </SearchSelect>
        </label>
        <label>
          {copy("图形", "Figure")}
          <SearchSelect
            aria-label={copy("已保存图形", "Saved figures")}
            disabled={busy || !draft.ready}
            value={draft.saved?.id ?? ""}
            onChange={(event) =>
              void perform(async () => {
                await draft.choose(event.target.value);
                setStarterPending(false);
              })
            }
          >
            <option value="">{copy("新图形", "New figure")}</option>
            {draft.choices.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </SearchSelect>
        </label>
        {dataset && (
          <span>
            <span className="plot-source-name">{dataset.original_filename}</span>{dataset.row_count ?? "—"} {copy("行", "rows")} ·{" "}
            {dataset.column_count ?? "—"} {copy("列", "columns")}
          </span>
        )}
        {datasetId && !embedded && (
          <DesktopLink
            href={`/projects/${projectId}/data?datasetId=${datasetId}`}
          >
            {copy("查看数据", "View data")}
          </DesktopLink>
        )}
      </div>
      {datasetId && (
        <details className="plot-starters" open>
          <summary>{copy("图形与字段", "Figure and fields")}</summary>
          <div className="plot-starter-fields">
            <label>
              {copy("图形类型", "Figure type")}
              <SearchSelect
                aria-label={copy("起始图形类型", "Starter figure type")}
                value={starterKind}
                disabled={busy}
                onChange={(event) =>
                  (setStarterKind(event.target.value as PlotStarterKind), setStarterPending(true))
                }
              >
                {Object.entries(plotStarters).map(([key, labels]) => (
                  <option key={key} value={key}>
                    {labels[locale === "zh-CN" ? 0 : 1]}
                  </option>
                ))}
              </SearchSelect>
            </label>
            {starterKind !== "heatmap" && (
              <label>
                {starterKind === "trend" ? copy("时间", "Time") : pairedStarter ? "X" : copy("数值", "Value")}
                <SearchSelect
                  aria-label={copy("起始图形数值字段", "Starter value column")}
                  value={x}
                  disabled={busy}
                  onChange={(event) => { setStarterX(event.target.value); setStarterPending(true); }}
                >
                  {!numericColumns.length && (
                    <option value="">
                      {copy("没有数值字段", "No numeric columns")}
                    </option>
                  )}
                  {numericColumns.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </SearchSelect>
              </label>
            )}
            {pairedStarter && (
              <label>
                Y
                <SearchSelect
                  aria-label={copy("起始图形 Y 字段", "Starter Y column")}
                  value={y}
                  disabled={busy}
                  onChange={(event) => { setStarterY(event.target.value); setStarterPending(true); }}
                >
                  <option value="">
                    {copy("选择字段", "Choose a column")}
                  </option>
                  {numericColumns.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </SearchSelect>
              </label>
            )}
            {starterKind !== "heatmap" && starterKind !== "trend" && (
              <label>
                {copy("分组", "Group")}
                <SearchSelect
                  aria-label={copy("起始图形分组字段", "Starter group column")}
                  value={group}
                  disabled={busy}
                  onChange={(event) => { setStarterGroup(event.target.value); setStarterPending(true); }}
                >
                  <option value="">{copy("不分组", "No grouping")}</option>
                  {fields.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </SearchSelect>
              </label>
            )}
            {starterKind === "heatmap" && (
              <fieldset className={controls.values}><legend>{copy("数值字段", "Value columns")}</legend>
                {numericColumns.map(name => <label key={name}><input type="checkbox" disabled={busy} checked={selectedNumeric.includes(name)} onChange={event=>{
                  const columns=draft.peek().scope.columns ?? [];
                  draft.update({...draft.peek(),scope:{...draft.peek().scope,columns:event.target.checked?[...columns,name]:columns.filter(item=>item!==name)}});
                  setStarterPending(true);
                }}/><span>{name}</span></label>)}
              </fieldset>
            )}
            {starterKind === "trend" && <fieldset className={controls.values}>
              <legend>{copy("数值字段", "Value columns")}</legend>
              {numericColumns.filter((name) => name !== x).map((name) => <label key={name}>
                <input type="checkbox" checked={trendNumeric.includes(name)} disabled={busy} onChange={(event) => { setTrendValues(event.target.checked ? [...trendNumeric, name] : trendNumeric.filter((item) => item !== name)); setStarterPending(true); }} />
                <span>{name}</span>
              </label>)}
            </fieldset>}
            <Button
              variant="outline"
              disabled={busy || !starterValid}
              onClick={() =>
                void perform(() =>
                  bind(datasetId, {
                    kind: starterKind,
                    x,
                    y,
                    group,
                    numeric: starterKind === "trend" ? trendNumeric : selectedNumeric,
                  }),
                )
              }
            >
              {copy("以此新建", "Create figure")}
            </Button>
          </div>
        </details>
      )}
      {(error ||
        draft.error ||
        runs.error ||
        preview.error ||
        datasets.error) && (
        <ResearchFailure
          error={
            error ||
            draft.error ||
            runs.error ||
            preview.error ||
            datasets.error
          }
        />
      )}
      {latest && (
        <div
          className={`plot-run-status is-${latest.payload.status}`}
          role={latest.payload.status === "failed" ? "alert" : "status"}
        >
          <strong>{status(latest.payload.status)}</strong>
          {latest.payload.error_message && (
            <span>{latest.payload.error_message}</span>
          )}
          {latest.payload.status === "failed" && shown && (
            <span>{copy("保留上次图形", "Previous figure retained")}</span>
          )}
        </div>
      )}
      {!dpiValid && (
        <p role="alert">
          {copy(
            "DPI 请输入 72–600 的整数。",
            "Enter an integer DPI from 72 to 600.",
          )}
        </p>
      )}
      {shown && <details className={controls.section}>
        <summary>{copy("样式", "Style")}</summary>
        <div className={controls.fields}>
          <label>{copy("字号", "Font size")}<input type="number" min={6} max={48} step={1} placeholder={copy("保持", "Keep")} value={styleInput.font} disabled={busy || !!active} onChange={(event) => setStyleInput({ ...styleInput, font: event.target.value })} /></label>
          <label>{copy("宽（英寸）", "Width (in)")}<input type="number" min={2} max={24} step={0.5} placeholder={(Number(shown.payload.metadata.width) / shown.payload.dpi || 8).toFixed(1)} value={styleInput.width} disabled={busy || !!active} onChange={(event) => setStyleInput({ ...styleInput, width: event.target.value })} /></label>
          <label>{copy("高（英寸）", "Height (in)")}<input type="number" min={2} max={24} step={0.5} placeholder={(Number(shown.payload.metadata.height) / shown.payload.dpi || 5).toFixed(1)} value={styleInput.height} disabled={busy || !!active} onChange={(event) => setStyleInput({ ...styleInput, height: event.target.value })} /></label>
          <label>{copy("线宽", "Line width")}<input type="number" min={0.2} max={12} step={0.1} placeholder={copy("保持", "Keep")} value={styleInput.line} disabled={busy || !!active} onChange={(event) => setStyleInput({ ...styleInput, line: event.target.value })} /></label>
          <label>{copy("网格", "Grid")}<SearchSelect value={styleInput.grid} disabled={busy || !!active} onChange={(event) => setStyleInput({ ...styleInput, grid: event.target.value })}><option value="">{copy("保持", "Keep")}</option><option value="on">{copy("显示", "Show")}</option><option value="off">{copy("隐藏", "Hide")}</option></SearchSelect></label>
        </div>
        {!!colors.length && <div className="plot-color-picker">
          <label>{copy("调整颜色", "Edit color")}<SearchSelect aria-label={copy("选择图形颜色", "Choose figure color")} value={String(Math.min(colorIndex,colors.length-1))} onChange={event=>setColorIndex(Number(event.target.value))}>{colors.map((color,index)=><option key={color} value={index}>{copy("颜色", "Color")} {index+1} · {color}</option>)}</SearchSelect></label>
          <input type="color" aria-label={copy("所选颜色", "Selected color")} disabled={busy || !!active} value={(colorChanges[colors[Math.min(colorIndex,colors.length-1)]] ?? colors[Math.min(colorIndex,colors.length-1)]).slice(0,7)} onChange={event=>setColorChanges({...colorChanges,[colors[Math.min(colorIndex,colors.length-1)]]:event.target.value})}/>
        </div>}

        <Button variant="outline" disabled={busy || !!active || !styleChanged || !("figure_state" in shown.payload.artifacts)} onClick={() => void perform(applyStyle)}>{copy("应用样式", "Apply style")}</Button>
        {!("figure_state" in shown.payload.artifacts) && <p role="status">{copy("这张旧图未保存渲染状态。重新生成后可直接修改样式。", "This older figure has no saved rendering state. Render it again to enable style edits.")}</p>}
      </details>}
        <div role="group" aria-label={copy("显示方式", "View")}>
          <button type="button" aria-expanded={view !== "preview"} onClick={() => setView(view === "preview" ? "both" : "preview")}>
            <CodeIcon className="size-4" /> {copy("高级：Python", "Advanced: Python")}
          </button>
        </div>
        {view !== "preview" && (
          <section
            className="plot-code-pane"
            aria-label={copy("Python 编辑器", "Python editor")}
          >
            <div className="plot-pane-heading">
              <strong>
                <CodeIcon className="size-4" />
                {shown?.payload.metadata.style_only === true ? copy("创建代码 · Python", "Creation code · Python") : "Python"}
              </strong>
              <label>
                DPI
                <input
                  type="text"
                  inputMode="numeric"
                  aria-label="DPI"
                  aria-invalid={!dpiValid}
                  disabled={busy || !datasetId}
                  value={dpiInput}
                  onChange={(event) => {
                    const value = event.target.value;
                    setDpiInput(value);
                    if (
                      /^\d+$/.test(value) &&
                      Number(value) >= 72 &&
                      Number(value) <= 600
                    )
                      draft.update({ ...draft.peek(), dpi: Number(value) });
                  }}
                />
              </label>
            </div>
            <div className="plot-ide-toolbar">
              {editors.data && (
                <>
                  <SearchSelect
                    aria-label={copy("代码编辑器", "Code editor")}
                    value={selectedEditor}
                    disabled={busy}
                    onChange={(event) => setEditor(event.target.value)}
                  >
                    {!editorAvailable && selectedEditor && (
                      <option value={selectedEditor} disabled>
                        {selectedEditor} · {copy("未找到", "Not found")}
                      </option>
                    )}
                    {editors.data.editors.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </SearchSelect>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={
                      busy || !datasetId || !editorAvailable || !dpiValid
                    }
                    onClick={() => void perform(() => openEditor())}
                  >
                    <ExternalLinkIcon className="size-4" />
                    {copy("打开 IDE", "Open IDE")}
                  </Button>
                </>
              )}
              {fileState?.available && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void perform(() => readLocal())}
                >
                  <RefreshCwIcon className="size-4" />
                  {copy("读取修改", "Read changes")}
                </Button>
              )}
              {((fileState?.available && fileState.external_changed) ||
                codeFile.isError) && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !editorAvailable || !dpiValid}
                  onClick={() => void perform(() => openEditor(true))}
                >
                  {copy("另存应用代码", "Save app code separately")}
                </Button>
              )}
            </div>
            {(codeFile.error || editors.error) && (
              <ResearchFailure error={codeFile.error || editors.error} />
            )}
            {fileState?.available && fileState.external_changed && (
              <div className="plot-code-changes" role="status">
                <p>
                  {fileState.conflict
                    ? copy(
                        "应用与本地文件都有修改。",
                        "The app and local file both changed.",
                      )
                    : copy("本地文件有修改。", "The local file has changed.")}
                </p>
                {fileState.conflict && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void perform(() => readLocal(true))}
                  >
                    {copy("使用本地代码", "Use local code")}
                  </Button>
                )}
                <details>
                  <summary>{copy("查看本地代码", "View local code")}</summary>
                  <pre>{fileState.code}</pre>
                </details>
              </div>
            )}
            <div className="plot-code-editor">
              <pre aria-hidden="true">
                {draft.value.code
                  .split("\n")
                  .map((_, index) => index + 1)
                  .join("\n")}
              </pre>
              <textarea
                aria-label={copy("绘图 Python 代码", "Plot Python code")}
                onScroll={(event) => {
                  const gutter = event.currentTarget.previousElementSibling;
                  if (gutter) gutter.scrollTop = event.currentTarget.scrollTop;
                }}
                spellCheck={false}
                maxLength={32000}
                disabled={busy || !draft.ready || !datasetId}
                value={draft.value.code}
                onChange={(event) => {
                  setStarterPending(false);
                  draft.update({ ...draft.peek(), code: event.target.value });
                }}
              />
            </div>
            {fileState?.available && (
              <details className="plot-code-file-path">
                <summary>{copy("本地代码文件", "Local code file")}</summary>
                <code>{fileState.file_path}</code>
              </details>
            )}
            <details className="plot-fields">
              <summary>
                {copy("可用字段", "Available columns")} ·{" "}
                {draft.value.scope.columns?.length ?? 0}
              </summary>
              <p>
                <code>df</code> · {dataset?.original_filename}
              </p>
              <div>
                {preview.data?.columns.map((column) => (
                  <label key={column.name}>
                    <input
                      type="checkbox"
                      disabled={busy}
                      checked={
                        draft.value.scope.columns?.includes(column.name) ??
                        false
                      }
                      onChange={(event) => {
                        const columns = draft.value.scope.columns ?? [];
                        if (starterKind === "heatmap") setStarterPending(true);
                        draft.update({
                          ...draft.peek(),
                          scope: {
                            ...draft.value.scope,
                            columns: event.target.checked
                              ? [...columns, column.name]
                              : columns.filter((name) => name !== column.name),
                          },
                        });
                      }}
                    />
                    <code>{column.name}</code>
                    <small>{column.inferred_type}</small>
                  </label>
                ))}
              </div>
            </details>
          </section>
        )}
      </div>
      <div className="plot-output-column">
      <div className="plot-view-bar">
        {chosen && ["completed", "failed"].includes(chosen.payload.status) && dataset?.media_result?.kind === "summary_csv" && chosen.payload.draft_revision === draft.saved?.revision &&
          (chosen.payload.scope.columns ?? []).filter(name => /^(mean_frame_rms|pitch_(mean_hz|cv|range_hz)|intensity_energy_mean_db|f[1-4]_mean_hz|speech_rate_per_second)$/.test(name)).length > 1 &&
          <button type="button" disabled={busy || !!active || draft.dirty} onClick={() => void perform(splitAcousticMeasures)}>按指标分图</button>}
        {shown && shown.payload.draft_id !== draft.saved?.id && !shown.archived && (
          <Button variant="outline" disabled={busy || !!active || draft.dirty} onClick={() => void perform(async () => {
            await draft.choose(shown.payload.draft_id);
            setSelectedRun(null);
          })}>{copy("打开此版本编辑", "Edit this version")}</Button>
        )}
        {items.length > 0 && (
          <SearchSelect
            aria-label={copy("绘图运行记录", "Plot runs")}
            value={selectedRun ?? ""}
            onChange={(event) => setSelectedRun(event.target.value || null)}
          >
            <option value="">
              {copy("最近完成的图形", "Latest completed figure")}
            </option>
            {items.map((item) => (
              <option value={item.id} key={item.id}>
                {status(item.payload.status)} ·{" "}
                {item.payload.metadata.style_only === true ? `${copy("样式版本", "Style version")} · ` : ""}
                {item.payload.started_at
                  ? new Date(item.payload.started_at).toLocaleString(locale)
                  : item.id.slice(0, 8)}
              </option>
            ))}
          </SearchSelect>
        )}
      </div>
      <div className={`plot-editing-grid view-${view}`}>
        {view !== "code" && (
          <section
            className="plot-preview"
            aria-label={copy("图形预览", "Figure preview")}
          >
            <div className="plot-pane-heading">
              <strong>{copy("图形预览", "Figure preview")}</strong>
              {shown && (
                <small>
                  {shown.archived && copy("已导入 · ", "Imported · ")}
                  {shown.payload.title} · {copy("代码版本", "Code revision")}{" "}
                  {shown.payload.draft_revision}
                </small>
              )}
            </div>
            {shown?.payload.status === "completed" && <PlotAgentRequest project={projectId} taskId={taskId} runId={shown.id} revision={shown.revision} datasetId={shown.payload.scope.dataset_id}/>}
            <div className="plot-canvas">
              {shown &&
              image?.run ===
                shown.id /* eslint-disable-next-line @next/next/no-img-element -- Display the saved authenticated artifact without transforming its bytes. */ ? (
                <img src={image.url} alt={shown.payload.title} />
              ) : (
                <p>
                  {shown
                    ? copy("正在读取图形…", "Loading figure…")
                    : chosen
                      ? (chosen.payload.error_message ??
                        status(chosen.payload.status))
                      : datasetId
                        ? copy(
                            "点击“生成图形”查看结果。",
                            "Select Render figure to see the result.",
                          )
                        : copy(
                            "选择一份数据开始绘图。",
                            "Choose data to start plotting.",
                          )}
                </p>
              )}
            </div>
            {shown && (
              <footer className="plot-export-bar">
                <DownloadButton
                  identity={`${shown.id}:preferred:${preferences.value.imageFormat}`}
                  className="plot-export-button plot-export-preferred"
                  download={(signal) =>
                    downloadApiFile(
                      plotsApi.file(
                        projectId,
                        shown.id,
                        preferences.value.imageFormat,
                      ),
                      `${shown.payload.title}.${preferences.value.imageFormat}`,
                      signal,
                    )
                  }
                >
                  {copy("导出", "Export")}{" "}
                  {preferences.value.imageFormat.toUpperCase()}
                </DownloadButton>
                {(["svg", "png", "pdf", "py"] as const)
                  .filter(
                    (extension) => extension !== preferences.value.imageFormat,
                  )
                  .map((extension) => (
                    <DownloadButton
                      key={extension}
                      identity={`${shown.id}:${extension}`}
                      className="plot-export-button"
                      download={(signal) =>
                        downloadApiFile(
                          plotsApi.file(projectId, shown.id, extension),
                          `${shown.payload.title}.${extension}`,
                          signal,
                        )
                      }
                    >
                      {extension === "py" ? "Python" : extension.toUpperCase()}
                    </DownloadButton>
                  ))}
                <DownloadButton
                  identity={`${shown.id}:package`}
                  className="plot-export-button"
                  errorMessage={(error) =>
                    error instanceof ApiError &&
                    (error.errorCode.startsWith("PLOT_") ||
                      error.errorCode.startsWith("ML_"))
                      ? error.detail
                      : null
                  }
                  download={(signal) =>
                    downloadApiFile(
                      plotsApi.package(projectId, shown.id),
                      `${shown.payload.title}.zip`,
                      signal,
                    )
                  }
                >
                  {copy("代码与数据", "Code and data")}
                </DownloadButton>
              </footer>
            )}
            {shown &&
              (!dpiValid ||
                Number(dpiInput) !== shown.payload.dpi ||
                !samePlotInput(shown.payload, draft.value)) && (
                <p className="plot-preview-note">
                  {copy(
                    "当前设置有修改，预览为上次运行。",
                    "Settings have changed; preview shows the previous run.",
                  )}
                </p>
              )}
          </section>
        )}

      </div>
      </div>
    </section>
  );
}
