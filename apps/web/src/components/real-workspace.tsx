"use client";

import Image from "next/image";

import { projectOptionTitle } from "@/components/project-disambiguation";

import { ResultNavigation, ProjectResultSearch, openMaterialResults, type ResultTarget } from "./workspace-result-navigation";
import { WorkspaceOutputTree } from "./workspace-output-tree";
import { analysisTableChoices, analysisTableVersions, versionTitle } from "@/lib/analysis-tables";
import { WorkspaceCases, WorkspaceSelectionActions, workspaceCaseEntries, type CaseSourceContext } from "./workspace-cases";
import { SearchSelect } from "@/components/ui/search-select";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  ChartNoAxesColumnIncreasingIcon,
  ChevronDownIcon,
  FolderIcon,
  FileTextIcon,
  FolderOpenIcon,
  HistoryIcon,
  MenuIcon,
  MessageSquareIcon,
  MoonIcon,
  PlusIcon,
  SearchIcon,
  SettingsIcon,
  SunIcon,
  Table2Icon,
  XIcon,
} from "lucide-react";
import { AuthGuard } from "./auth-guard";
import { DesktopLink } from "./desktop-link";
import { WorkspaceRunNotice } from "./workspace-run-notice";
import { WorkspaceAnnotationProvider, WorkspaceAnnotationPanel } from "./workspace-annotations";
import { CodingRelationsView } from "./coding-relations";
import { CodingAgreementView } from "./coding-agreement";
import { CodingMatrixView } from "./coding-matrix";
import { ResearchJournals } from "./research-journals";
import { WorkspaceHistory, type HistoryTarget } from "./workspace-history";
import { apiFetch } from "@/lib/api-client";
import { datasetTitle, workspaceOutputCategory, type WorkspaceInventory } from "@/lib/workspace-files";
import { ProjectAssistant } from "./project-assistant";
import { AdviceHeaderSlot } from "./material-suggestions";
import { WorkspaceExploration } from "./workspace-exploration";
import { ProjectFileControls } from "./project-file-controls";
import { ModelSettingsLink } from "./model-settings-link";
import {
  WorkspaceFileTree,
  WorkspaceDataTable,
  WorkspaceMaterial,
  workspaceFiles,
  FileTypeIcon,
  type WorkspaceFile,
} from "./workspace-data-browser";
import { WorkspaceImport } from "./workspace-import";
import { WorkspaceTaskEditor, type WorkspaceTaskNavigation } from "./workspace-task-editor";
import { projectsApi, datasetsApi } from "@/lib/api-endpoints";
import { workspaceApi } from "@/lib/workspace-api";
import { materialApi } from "@/lib/material-api";
import type { TaskRecord } from "@/lib/use-workspace-task";
import { useAuthStore } from "@/stores/auth-store";
import { useResultProjectId } from "./result-source";
import { ResizeHandle, usePreviewHistory, backLabel } from "./workspace-layout-controls";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { workspaceRouteTarget } from "@/lib/workspace-route";

type Location =
  | { kind: "data" }
  | { kind: "file"; id: string }
  | { kind: "explore" }
  | { kind: "coding-relations" }
  | { kind: "coding-agreement" }
  | { kind: "coding-matrix" }
  | { kind: "journals" }
  | { kind: "task"; id: string }
  | { kind: "create" };
export function RealWorkspace({ projectId }: { projectId: string }) {
  const resolved = useResultProjectId(projectId);
  const epoch = useAuthStore((state) => state.actorEpoch);
  return (
    <AuthGuard>
      {resolved ? (
        <ProjectWorkspace key={`${epoch}:${resolved}`} projectId={resolved} />
      ) : (
        <p role="alert">项目不存在，请从项目列表重新打开。</p>
      )}
    </AuthGuard>
  );
}

function ProjectWorkspace({ projectId }: { projectId: string }) {
  const router = useRouter(),
    params = useSearchParams(),
    client = useQueryClient();
  const { resolvedTheme, setTheme } = useTheme();
  const user = useAuthStore((state) => state.user);
  const epoch = useAuthStore((state) => state.actorEpoch);
  const [location, setLocation] = useState<Location>(() =>
    params.get("task")
      ? { kind: "task", id: params.get("task")! }
      : params.get("datasetId")
        ? { kind: "file", id: params.get("datasetId")! }
        : { kind: "data" },
  );
  const [agent, setAgent] = useState(false),
    [mobileNav, setMobileNav] = useState(false),
    [importing, setImporting] = useState(false);
  const [dataOpen, setDataOpen] = useState(true),
    [history, setHistory] = useState(false);
  const [annotations, setAnnotations] = useState(false);
  const [adviceHost, setAdviceHost] = useState<HTMLDivElement | null>(null);
  const [explorationOpened, setExplorationOpened] = useState(false);
  const [checkedFiles, setCheckedFiles] = useState<string[] | null>(null);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const selecting = checkedFiles !== null;
  const [caseSource,setCaseSource]=useState<CaseSourceContext>();
  function checkFile(file: WorkspaceFile, checked: boolean) {
    setCheckedFiles(ids => {
      if (ids === null) return null;
      return checked ? [...new Set([...ids, file.id])] : ids.filter(id => id !== file.id);
    });
  }
  useEffect(() => {
    const show = (event: Event) => {
      const detail = (event as CustomEvent<{project:string}>).detail;
      if (detail?.project === projectId) { setAgent(true); setAnnotations(false); }
    };
    window.addEventListener("statforge:show-agent", show);
    return () => window.removeEventListener("statforge:show-agent", show);
  }, [projectId]);
  const [relationsOpened, setRelationsOpened] = useState(false);
  const [relationBack, setRelationBack] = useState<Location|null>(null);
  const [agreementOpened,setAgreementOpened]=useState(false);
  const [matrixOpened,setMatrixOpened]=useState(false);
  const [journalsOpened,setJournalsOpened]=useState(false);
  const [journalMemo,setJournalMemo]=useState(false);
  const [matrixCurrentRequest,setMatrixCurrentRequest]=useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [leftWidth, setLeftWidth] = useState(248);
  const [rightWidth, setRightWidth] = useState(360);
  const [annotationWidth, setAnnotationWidth] = useState(360);
  const [findingFiles, setFindingFiles] = useState(false);
  const [fileTerm, setFileTerm] = useState("");
  const [fileSearch,setFileSearch]=useState(false);
  useEffect(()=>{
    const find=(event:KeyboardEvent)=>{if(event.ctrlKey&&event.key.toLowerCase()==="k"){event.preventDefault();setFindingFiles(true);}};
    window.addEventListener("keydown",find);return()=>window.removeEventListener("keydown",find);
  },[]);

  const [viewportWidth, setViewportWidth] = useState(1200);
  useEffect(() => {
    try {
      const left = Number(localStorage.getItem("statforge.workspace.leftWidth"));
      const right = Number(localStorage.getItem("statforge.workspace.rightWidth"));
      if (left >= 180 && left <= 360) setLeftWidth(left);
      if (right >= 280 && right <= 600) setRightWidth(right);
      const annotation = Number(localStorage.getItem("statforge.workspace.annotationWidth"));
      if (annotation >= 280 && annotation <= 600) setAnnotationWidth(annotation);
      else if (right >= 280 && right <= 600) setAnnotationWidth(right);
    } catch { /* storage can be unavailable */ }
    const update = () => setViewportWidth(window.innerWidth);
    update(); window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  const updateLeft = (width: number) => { setLeftWidth(width); try { localStorage.setItem("statforge.workspace.leftWidth", String(width)); } catch {} };
  const updateRight = (width: number) => {
    if (annotations) setAnnotationWidth(width); else setRightWidth(width);
    try { localStorage.setItem(annotations ? "statforge.workspace.annotationWidth" : "statforge.workspace.rightWidth", String(width)); } catch {}
  };
  const effectiveLeft = Math.min(leftWidth, Math.max(180, viewportWidth - (agent || annotations ? 280 : 0) - 440));
  const effectiveRight = Math.min(annotations ? annotationWidth : rightWidth, Math.max(280, viewportWidth - (sidebarOpen ? effectiveLeft : 0) - 440));
  const fileHistory = usePreviewHistory(location.kind === "file" ? location.id : undefined, ".rw-project-scroll");
  const [historySource, setHistorySource] = useState("");
  const [navigation, setNavigation] = useState<(WorkspaceTaskNavigation & {taskId: string}) | null>(null);
  const navigated = useRef("");
  const [opened, setOpened] = useState<string[]>(() =>
    params.get("task") ? [params.get("task")!] : [],
  );
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [question, setQuestion] = useState(""),
    [newDataset, setNewDataset] = useState(""),
    [exploration, setExploration] = useState("");
  const [creating, setCreating] = useState(false),
    [error, setError] = useState("");
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: ({ signal }) => projectsApi.get(projectId, signal),
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => projectsApi.list(signal),
  });
  const datasets = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });
  const materials = useQuery({
    queryKey: ["workspace-materials", projectId],
    queryFn: ({ signal }) => materialApi.list(projectId, signal),
  });
  const tasks = useQuery({
    queryKey: ["research-tasks", epoch, projectId],
    queryFn: async ({ signal }) => {
      const summaries = await workspaceApi.list(
        projectId,
        "research_task",
        signal,
      );
      return Promise.all(
        summaries
          .filter((item) => item.available)
          .map((item) =>
            workspaceApi.read(item.id, signal).then((row) => {
              if (
                row.payload.kind !== "research_task" ||
                row.project_id !== projectId
              )
                throw new Error("研究任务不可用");
              return row as TaskRecord;
            }),
          ),
      );
    },
  });
  const inventory = useQuery({
    queryKey: ["workspace-files", projectId],
    queryFn: ({signal}) => apiFetch<WorkspaceInventory>(`/api/v1/projects/${projectId}/workspace-files`, {signal}),
    refetchOnWindowFocus: true,
  });
  const tables = useMemo(() => analysisTableVersions(datasets.data ?? []), [datasets.data]);
  const files = useMemo(
    () => workspaceFiles(datasets.data ?? [], materials.data ?? [], tables),
    [datasets.data, materials.data, tables],
  );
  const caseEntries = useMemo(() => workspaceCaseEntries(datasets.data ?? [], inventory.data?.sessions ?? []), [datasets.data, inventory.data?.sessions]);
  const rootFiles: WorkspaceFile[] = (inventory.data?.files ?? []).map(entry => {
    const registered = files.find(file => file.id === entry.dataset_id);
    return {id: entry.dataset_id || `path:${entry.path}`, kind: "data", format: entry.name.split(".").pop()?.toUpperCase() || "文件", raw: true, origin: "workspace", ...registered, name: entry.name, path: entry.path, size: entry.size};
  });
  const savedOutputs = new Set(inventory.data?.generated_ids ?? []);
  const outputIds = new Set((datasets.data || []).filter(data => workspaceOutputCategory(data, savedOutputs)).map(data => data.id));
  const addedFiles = files.filter(file => file.origin === "added" && !savedOutputs.has(file.id) && !outputIds.has(file.id));
  const mediaOutputs = new Set(datasets.data?.filter(item => item.media_result).map(item => item.id));
  const generatedFiles = files.filter(file => !mediaOutputs.has(file.id) && !outputIds.has(file.id) && (file.origin === "generated" || savedOutputs.has(file.id)));
  const groups = [...(inventory.data?.sessions || [])].sort((a,b) => (b.created || "").localeCompare(a.created || ""))
    .filter((group,i,rows) => !group.group_id || rows.findIndex(s => s.group_id === group.group_id) === i)
    .filter(group => !outputIds.has(group.id) && !datasets.data?.find(d=>d.id===group.id && d.media_case?.kind==="media" && !d.media_case.survey));
  const shownFiles = [...rootFiles, ...addedFiles];
  const fileNeedle = fileSearch ? fileTerm.trim().toLocaleLowerCase() : "";
  const nameMatches = (file: WorkspaceFile) => (file.path || file.name).toLocaleLowerCase().includes(fileNeedle);
  const visibleRoot = fileNeedle ? rootFiles.filter(nameMatches) : rootFiles;
  const visibleAdded = fileNeedle ? addedFiles.filter(nameMatches) : addedFiles;
  const currentFile =
    location.kind === "file"
      ? files.find((item) => item.id === location.id)
      : undefined;
  const currentTask =
    location.kind === "task"
      ? tasks.data?.find((item) => item.id === location.id)
      : undefined;
  const go = (next: Location, preserveFileHistory = false) => {
    setRelationBack(null);
    if (!preserveFileHistory) fileHistory.reset();
    setLocation(next);
    setAnnotations(false);
    setMobileNav(false);
    if (next.kind === "task")
      setOpened((old) => (old.includes(next.id) ? old : [...old, next.id]));
    const url = new URL(window.location.href);
    // A project switch can mount before Next commits the new history URL.
    // Build task/file locations from this workspace's identity, never the old URL.
    if (url.pathname.includes("/__desktop_project__/")) url.searchParams.set("projectId", projectId);
    url.searchParams.delete("task");
    for (const key of ["datasetId", "materialId", "analysis", "analysisId", "plotId", "runId", "object", "sourceId", "view", "legacy", "panel"]) url.searchParams.delete(key);
    if (next.kind === "task") url.searchParams.set("task", next.id);
    if (next.kind === "file") url.searchParams.set("datasetId", next.id);
    window.history.replaceState(null, "", url.href);
  };
  const saved = useCallback(
    (row: TaskRecord) =>
      client.setQueryData<TaskRecord[]>(
        ["research-tasks", epoch, projectId],
        (old) => (old ?? []).map((item) => (item.id === row.id ? row : item)),
      ),
    [client, epoch, projectId],
  );
  const status = useCallback(
    (id: string, text: string) =>
      setStatuses((old) => ({ ...old, [id]: text })),
    [],
  );
  async function refreshFiles(pathToOpen?: string) {
    setError("");
    try {
      if (project.data?.workspace_path) await apiFetch("/api/v1/local-workspaces/open", {method: "POST", body: JSON.stringify({path: project.data.workspace_path})});
      const [fresh] = await Promise.all([inventory.refetch(), datasets.refetch(), materials.refetch()]);
      if (pathToOpen) {
        const found = fresh.data?.files.find(row => row.path === pathToOpen);
        if (found?.dataset_id) go({kind: "file", id: found.dataset_id});
        else setError("这个文件暂不支持预览；原文件仍在工作区中。");
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "文件夹读取失败"); }
  }
  function selectFile(file: WorkspaceFile) {
    setCaseSource(undefined);
    if (file.id.startsWith("path:")) void refreshFiles(file.path);
    else go({kind: "file", id: file.id});
  }
  async function openHistoryTarget(target: HistoryTarget) {
    try {
      const taskId = target.taskId || currentTask?.id;
      if (target.datasetId && !taskId) { go({kind: "file", id: target.datasetId}); setHistory(false); return; }
      if (target.materialId) { go({kind: "file", id: target.materialId}); setHistory(false); return; }
      if (!taskId) return;
      if (target.analysisId) {
        const record = await workspaceApi.read(target.analysisId);
        if (record.project_id !== projectId || record.payload.kind !== "analysis") throw new Error("分析不属于当前项目");
        const latest = await workspaceApi.read(taskId) as TaskRecord;
        if (latest.project_id !== projectId || latest.payload.kind !== "research_task") throw new Error("研究问题不可用");
        if (!latest.payload.analysis_ids.includes(record.id)) saved(await workspaceApi.save(projectId, {...latest.payload, analysis_ids: [...latest.payload.analysis_ids, record.id]}, latest) as TaskRecord);
      }
      go({kind: "task", id: taskId});
      setNavigation({nonce: Date.now(), taskId, datasetId: target.analysisId || target.plotRunId || target.kind === "plot" ? undefined : target.datasetId, analysisId: target.analysisId, plotRunId: target.plotRunId, plotDraftId: target.kind === "plot" ? target.sourceId : undefined});
      setHistory(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "暂时无法打开"); }
  }
  useEffect(() => {
    if (!tasks.data || !datasets.data) return;
    const signature = params.toString();
    if (!signature || navigated.current === signature) return;
    navigated.current = signature;
    const target = workspaceRouteTarget(params, tasks.data);
    if (target.kind === "history") { setHistorySource(target.sourceId); setHistory(true); }
    else if (target.kind === "task") {
      go({kind: "task", id: target.taskId});
      setNavigation({nonce: Date.now(), ...target.navigation, taskId: target.taskId});
    } else if (target.kind === "file") go({kind: "file", id: target.id});
    // Route navigation is explicit; background query updates keep the current view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, tasks.data, datasets.data]);
  function openResult(target:ResultTarget) {
    if(target.datasetId) {
      if(target.collectionId && target.runId && target.datasetId===target.collectionId)
        openMaterialResults(target.collectionId,target.runId);
      go({kind:"file",id:target.datasetId});
    } else if(target.taskId && tasks.data?.some(item=>item.id===target.taskId)) {
      go({kind:"task",id:target.taskId});
      setNavigation({nonce:Date.now(),taskId:target.taskId,analysisId:target.analysisId,figureId:target.figureId,plotRunId:target.plotRunId,tab:target.analysisId?"results":target.figureId||target.plotRunId?"figures":undefined});
    }
  }
  function newTask() {
    setQuestion("");
    setExploration("");
    setNewDataset(
      currentFile?.kind === "data" && datasets.data?.some(item => item.id === currentFile.id && item.upload_status === "ready")
        ? currentFile.id
        : currentTask?.payload.dataset_id || "",
    );
    go({ kind: "create" });
  }
  async function createTask() {
    if (!question.trim() || creating) return;
    setCreating(true);
    setError("");
    try {
      const row = (await workspaceApi.save(projectId, {
        kind: "research_task",
        schema_version: 1,
        title: question.trim().slice(0, 160),
        question: question.trim(),
        dataset_id: newDataset || null,
        dataset_ids: newDataset ? [newDataset] : [],
        analysis_ids: [],
        figure_ids: [],
        plot_run_ids: [],
        adopted_id: null,
        statistics_draft: null,
        exploration_context: exploration,
      })) as TaskRecord;
      client.setQueryData<TaskRecord[]>(
        ["research-tasks", epoch, projectId],
        (old) => [...(old ?? []), row],
      );
      go({ kind: "task", id: row.id });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "研究问题未保存，请重试",
      );
    } finally {
      setCreating(false);
    }
  }
  useEffect(() => {
    if (!project.data || !user?.id) return;
    try {
      const key = `statforge.recent-projects.v1:${user.id}`;
      const prior = JSON.parse(localStorage.getItem(key) || "{}");
      localStorage.setItem(
        key,
        JSON.stringify({ ...prior, [projectId]: Date.now() }),
      );
    } catch {
      /* Project contents live in the server store. */
    }
  }, [project.data, user?.id, projectId]);
  if (project.isPending)
    return (
      <p className="rw-starting" role="status">
        正在打开项目…
      </p>
    );
  if (project.isError)
    return (
      <div className="rw-starting" role="alert">
        {project.error.message}
        <button onClick={() => void project.refetch()}>重新打开</button>
        <DesktopLink href="/projects">返回项目</DesktopLink>
      </div>
    );
  const heading =
    currentFile?.name ||
    (location.kind === "explore" ? "自由探索" : location.kind === "coding-relations" ? "共现与关系" : location.kind === "coding-agreement" ? "编码者一致性" : location.kind === "coding-matrix" ? "证据矩阵" : location.kind === "journals" ? journalMemo?"项目备忘":"研究日志" : "项目数据");
  return (
    <ResultNavigation.Provider value={{project:project.data,datasets:datasets.data||[],tasks:tasks.data||[],open:openResult,home:()=>go({kind:"data"})}}>
    <WorkspaceAnnotationProvider openJournals={()=>{setJournalsOpened(true);go({kind:"journals"});}} openMatrix={current=>{if(current)setMatrixCurrentRequest(n=>n+1);setMatrixOpened(true);setAnnotations(false);go({kind:"coding-matrix"});}} openAgreement={()=>{setAgreementOpened(true);go({kind:"coding-agreement"});}} openRelations={()=>{setRelationsOpened(true);go({kind:"coding-relations"});}} openData={id=>{
      if (location.kind === "task") {setNavigation({nonce:Date.now(),taskId:location.id,datasetId:id,tab:"data"});setAnnotations(false);}
      else go({kind:"file",id});
    }} onNavigateSegment={async segment=>{
      const comparisonSource=location.kind==="file"?datasets.data?.find(d=>d.id===location.id):null;
      const returnToRelations=location.kind==="journals"||location.kind==="coding-relations"||location.kind==="coding-agreement"||location.kind==="coding-matrix"||comparisonSource?.coding_matrix||comparisonSource?.coding_agreement?location:null;
      const source=segment.material_id ? await materialApi.get(segment.material_id) : null;
      const linkedSource = source && datasets.data?.find(item => item.material_id === source.id && item.sha256 === source.sha256);
      if(source?.metadata.source_dataset_id&&!linkedSource)throw new Error("当前文件已更新，无法从此入口回查旧版本；已保存的比较和导出仍保留。");
      const id = linkedSource?.id || (!source?segment.dataset_id:undefined);
      if(id&&segment.task_id){go({kind:"task",id:segment.task_id});setNavigation({nonce:Date.now(),taskId:segment.task_id,datasetId:id,tab:"data"});}
      else if(id||source) go({kind:"file",id:id||source!.id});
      else throw new Error("此标注的原材料已不可用。");
      setRelationBack(returnToRelations);
    }} projectId={projectId} activeTaskId={currentTask?.id} researchQuestion={currentTask?.payload.question || ""} open={annotations} onOpenChange={(open) => { setAnnotations(open); if (open) setAgent(false); }}>
    <div className={`rw-shell ${mobileNav ? "nav-open" : ""} ${sidebarOpen ? "" : "sidebar-collapsed"}`} style={{ "--rw-left-width": `${effectiveLeft}px`, "--rw-right-width": `${effectiveRight}px` } as React.CSSProperties}>
      <aside className="rw-sidebar" aria-label="项目导航">
        <DesktopLink href="/dashboard" className="rw-brand">
          <Image src="/brand/quantelune.png" width={28} height={28} alt="" className="shrink-0" />
          <strong>Quantelune</strong>
        </DesktopLink>
        <label className="rw-project-picker">
          <FolderIcon size={16} />
          <SearchSelect
            aria-label="切换项目"
            value={projectId}
            onChange={(event) =>
              navigateToAppRoute(
                router,
                `/projects/${event.target.value}/workspace`,
              )
            }
          >
            {[project.data, ...(projects.data ?? []).filter(item => item.id !== projectId)].map((item) => (
              <option key={item.id} value={item.id}>
                {projectOptionTitle(item, [project.data, ...(projects.data ?? []).filter(p => p.id !== projectId)])}
              </option>
            ))}
          </SearchSelect>
        </label>
        <div className="rw-sidebar-scroll">
          <div className="rw-data-nav">
            <button
              className={
                location.kind === "data" || location.kind === "file"
                  ? "rw-nav-label active-label"
                  : "rw-nav-label"
              }
              onClick={() => go({ kind: "data" })}
            >
              <Table2Icon size={16} />
              工作区文件 <small>{rootFiles.length}</small>
            </button>
            <button type="button" className="rw-select-files" aria-pressed={selecting} disabled={selectionBusy} onClick={() => {
              setCheckedFiles(selecting ? null : []);
              if (!selecting) setDataOpen(true);
            }}>{selecting ? "取消" : "多选"}</button>
            <button aria-label="查找工作区文件" aria-pressed={fileSearch} onClick={() => {
              setFileSearch(!fileSearch); setFileTerm(""); setDataOpen(true);
            }}><SearchIcon size={14} /></button>
            <button
              aria-label={dataOpen ? "收起工作区文件" : "展开工作区文件"}
              aria-expanded={dataOpen}
              onClick={() => setDataOpen(!dataOpen)}
            >
              <ChevronDownIcon
                size={13}
                className={dataOpen ? "" : "rw-rotate"}
              />
            </button>
          </div>
          {dataOpen && (
            <>

              {fileSearch&&<input autoFocus className="rw-file-filter" type="search" aria-label="搜索工作区文件" placeholder="输入文件名" value={fileTerm} onChange={e=>setFileTerm(e.target.value)} onKeyDown={e=>{if(e.key==="Escape"){setFileSearch(false);setFileTerm("");}}}/>}
              <div className="rw-files-scroll">
              {fileNeedle && !visibleRoot.length && !visibleAdded.length && <p className="rw-sidebar-hint">没有匹配的文件</p>}
              <WorkspaceFileTree
                files={visibleRoot}
                folders={fileNeedle ? [] : inventory.data?.folders ?? []}
                selected={currentFile?.id || ""}
                onSelect={selectFile}
                checked={checkedFiles ?? undefined} onCheck={selecting ? checkFile : undefined} checkDisabled={selectionBusy}
              />
              {!!visibleAdded.length && <details className="rw-added-files" open><summary>已添加文件 <small>{addedFiles.length}</small></summary><WorkspaceFileTree files={visibleAdded.map(file => ({...file, path: file.name}))} selected={currentFile?.id || ""} onSelect={selectFile} checked={checkedFiles ?? undefined} onCheck={selecting ? checkFile : undefined} checkDisabled={selectionBusy} /></details>}
              </div>
              {checkedFiles?.length ? <WorkspaceSelectionActions datasets={datasets.data ?? []} cases={caseEntries}
                selectedIds={checkedFiles} busy={selectionBusy} setBusy={setSelectionBusy} onComplete={async id => {
                  await Promise.all([datasets.refetch(), inventory.refetch()]);
                  setCheckedFiles(null);
                  setCaseSource(undefined);
                  go({kind:"file", id});
                }} /> : null}
              {(rootFiles.length>0 || addedFiles.length>0 || outputIds.size>0) && <button
                className="rw-import-entry"
                onClick={() => {
                  setImporting(true);
                  go({ kind: "data" });
                }}
              >
                <PlusIcon size={16} />
                导入数据
              </button>}
            </>
          )}
          <WorkspaceCases datasets={datasets.data ?? []} cases={caseEntries} onOpen={(id,context)=>{setCaseSource(context);go({kind:"file",id});}}/>
          <WorkspaceOutputTree datasets={datasets.data||[]} sessions={inventory.data?.sessions||[]} generatedIds={savedOutputs} tasks={tasks.data||[]} selected={currentFile?.id} onChoose={id=>{
            const item=datasets.data?.find(d=>d.id===id);
            if(item?.transcription){sessionStorage.setItem("statforge:open-transcript:"+item.transcription.source_id,JSON.stringify({runId:item.transcription.run_id,textId:item.id,format:item.file_format}));go({kind:"file",id:item.transcription.source_id});window.dispatchEvent(new Event("statforge:open-transcript-version"));}
            else go({kind:"file",id});
          }} onText={id=>go({kind:"file",id})} onTask={(id,datasetId)=>{go({kind:"task",id});setNavigation({nonce:Date.now(),taskId:id,datasetId,tab:"data"});}}/>
          <button className="rw-nav-item" onClick={()=>{setAnnotations(true);setAgent(false);}}><FileTextIcon size={15}/>主题与标注</button>
          <button
            className={`rw-nav-item ${location.kind === "explore" ? "active" : ""}`}
            onClick={() => {
              setExplorationOpened(true);
              go({ kind: "explore" });
            }}
          >
            <MessageSquareIcon size={15} />
            自由探索
          </button>
          <div className="rw-task-list-heading">
            <span>研究问题</span>
            <button onClick={newTask} aria-label="新建研究问题">
              <PlusIcon size={16} />
            </button>
          </div>
          {tasks.isPending && <p className="rw-sidebar-hint">读取研究问题…</p>}
          {tasks.isError && (
            <p role="alert" className="rw-sidebar-hint">
              {tasks.error.message}
              <button onClick={() => void tasks.refetch()}>重试</button>
            </p>
          )}
          {tasks.data?.map((row) => (
            <button
              className={`rw-task-item ${currentTask?.id === row.id ? "active" : ""}`}
              key={row.id}
              title={row.payload.title}
              onClick={() => go({ kind: "task", id: row.id })}
            >
              <span
                className={`rw-task-dot ${statuses[row.id] === "运行中" ? "working" : ""}`}
              />
              <span>
                {row.payload.title}
                <small>
                  {statuses[row.id] ||
                    (row.payload.analysis_ids.length
                      ? `${row.payload.analysis_ids.length} 项分析`
                      : row.payload.dataset_ids?.some(id => datasets.data?.some(dataset => dataset.id === id && dataset.source_dataset_id))
                        ? "有保存结果"
                      : "尚未运行")}
                </small>
              </span>
            </button>
          ))}
          {tasks.data?.length === 0 && (
            <p className="rw-sidebar-hint">围绕一个完整问题组织多种分析。</p>
          )}
        </div>
        <div className="rw-sidebar-footer">
          <div className="rw-user-row">
            <span className="rw-avatar">
              {user?.display_name?.slice(0, 1) || "研"}
            </span>
            <span>本地工作区</span>
            <button
              aria-label={resolvedTheme === "dark" ? "切换浅色" : "切换深色"}
              onClick={() =>
                setTheme(resolvedTheme === "dark" ? "light" : "dark")
              }
            >
              {resolvedTheme === "dark" ? (
                <SunIcon size={16} />
              ) : (
                <MoonIcon size={16} />
              )}
            </button>
            <ModelSettingsLink projectId={projectId}>
              <span className="sr-only">模型设置</span>
              <SettingsIcon size={16} />
            </ModelSettingsLink>
          </div>
        </div>
      </aside>
      {sidebarOpen && <ResizeHandle label="调整项目导航宽度" value={effectiveLeft} onChange={updateLeft} direction={1} min={180} max={Math.min(360, Math.max(180, viewportWidth - (agent || annotations ? 280 : 0) - 440))} />}
      <div className="rw-workspace">
        <header className="rw-topbar">
          <button
            className="rw-mobile-menu"
            aria-label="打开项目导航"
            onClick={() => setMobileNav(!mobileNav)}
          >
            <MenuIcon size={18} />
          </button>
          <button className="rw-sidebar-toggle" aria-label={sidebarOpen ? "收起项目导航" : "展开项目导航"} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}><MenuIcon size={18} /></button>
          <div className="rw-breadcrumb">
            <button onClick={() => go({ kind: "data" })}>
              {project.data.title}
            </button>
            <span>/</span>
            <span>{currentTask?.payload.title || heading}</span>
          </div>
          <div className="rw-top-actions">
            <button aria-label="查找项目内容" onClick={()=>setFindingFiles(!findingFiles)}><SearchIcon size={17}/></button>
            <ProjectFileControls projectId={projectId} />
            <button onClick={() => { setHistorySource(""); setHistory(true); }} title="历史" aria-label="历史"><HistoryIcon size={17} /></button>
            <div ref={setAdviceHost} className="rw-advice-slot"/>
            <button
              className={agent ? "active" : ""}
              aria-pressed={agent}
              onClick={() => { setAgent(!agent); setAnnotations(false); }}
            >
              <MessageSquareIcon size={16} />
              Agent
            </button>
          </div>
        </header>
        {findingFiles && <ProjectResultSearch files={shownFiles} onFile={selectFile} onClose={()=>setFindingFiles(false)}/>}
        <div className="rw-main-with-annotations"><div className="rw-main-views">
        {error && <p className="rw-error" role="alert">{error}<button onClick={() => setError("")}>关闭</button></p>}
        {opened.map((id) => {
          const row = tasks.data?.find((item) => item.id === id);
          return row ? (
            <div
              key={id}
              className="rw-open-task"
              hidden={location.kind !== "task" || location.id !== id}
            >
              <WorkspaceTaskEditor
                adviceHost={adviceHost}
                active={location.kind === "task" && location.id === id}
                navigation={navigation?.taskId === id ? navigation : undefined}
                initial={row}
                datasets={datasets.data ?? []}
                agentOpen={agent && !annotations}
                onCloseAgent={() => setAgent(false)}
                onSaved={saved}
                onStatus={status}
                rightWidth={effectiveRight}
                onRightWidthChange={updateRight}
                rightMax={Math.min(600, Math.max(280, viewportWidth - (sidebarOpen ? effectiveLeft : 0) - 440))}
              />
            </div>
          ) : null;
        })}
        {location.kind === "task" && !currentTask && !tasks.isPending && (
          <p role="alert" className="rw-starting">
            这个研究问题暂时无法打开，请从左侧重新选择。
          </p>
        )}
        <div
          className={`rw-project-content ${agent ? "with-agent" : ""}`}
          hidden={location.kind === "task"}
        >
          <section className="rw-project-main">
            {location.kind === "create" ? (
              <>
                <header className="rw-page-heading">
                  <div>
                    <h1>新建研究问题</h1>

                  </div>
                </header>
                <form
                  className="rw-new-task"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void createTask();
                  }}
                >
                  <label className="rw-field">
                    研究问题
                    <textarea
                      autoFocus
                      required
                      maxLength={4000}
                      placeholder="例如：不同实验条件下的评分是否有差异？"
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                    />
                  </label>
                  <label className="rw-field">
                    起始数据
                    <SearchSelect
                      value={newDataset}
                      onChange={(event) => setNewDataset(event.target.value)}
                    >
                      <option value="">稍后选择数据</option>
                      {analysisTableChoices(datasets.data ?? [], tables, [newDataset])
                        .filter((item) => item.upload_status === "ready" || (item.raw_file && item.upload_status !== "deleted"))
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {tables.has(item.id) ? versionTitle(tables.get(item.id)!) : datasetTitle(item, datasets.data)}
                          </option>
                        ))}
                    </SearchSelect>
                  </label>
                  <details>
                    <summary>从项目探索带入上下文（可选）</summary>
                    <p className="rw-muted">
                      填写需要带入的信息，原探索对话保持独立。
                    </p>
                    <textarea
                      maxLength={4000}
                      value={exploration}
                      onChange={(event) => setExploration(event.target.value)}
                      placeholder="粘贴或整理已确认的信息…"
                    />
                  </details>
                  {error && (
                    <p role="alert" className="rw-error">
                      {error}
                    </p>
                  )}
                  <div className="rw-inline-actions">
                    <button
                      type="submit"
                      className="rw-primary"
                      disabled={creating || !question.trim()}
                    >
                      {creating ? "创建中…" : "创建研究问题"}
                    </button>
                    <button
                      type="button"
                      disabled={creating}
                      onClick={() => go({ kind: "data" })}
                    >
                      取消
                    </button>
                  </div>
                </form>
              </>
            ) : (
              <>
                <header className={currentFile?.kind === "data" && /^(JSON|CSV|XLSX|PARQUET)( · |$)/.test(currentFile.format)
                  ? "rw-page-heading rw-file-heading" : "rw-page-heading"}>
                  <div>
                    <h1>{heading}</h1>
                    <p>
                      {currentFile ? `${currentFile.format} · ${(currentFile.size / 1024).toFixed(1)} KB` : location.kind === "explore" || location.kind === "coding-relations" || location.kind === "coding-agreement" || location.kind === "coding-matrix" || location.kind === "journals" ? "" : `${rootFiles.length} 个工作区文件 · ${project.data.workspace_path || project.data.title}`}
                    </p>
                  </div>

                  {relationBack&&location.kind==="file"&&<button className="rw-preview-back" onClick={()=>go(relationBack)}>返回{relationBack.kind==="journals"?(journalMemo?"项目备忘":"研究日志"):relationBack.kind==="file" ? (datasets.data?.find(d=>d.id===relationBack.id)?.coding_agreement ? "编码比较" : datasets.data?.find(d=>d.id===relationBack.id)?.coding_matrix ? "证据矩阵" : "上一个数据") : relationBack.kind==="coding-agreement"?"编码者一致性":relationBack.kind==="coding-relations"?"共现与关系":"证据矩阵"}</button>}
                </header>
                <div className="rw-project-scroll">
                  {relationsOpened&&<div hidden={location.kind!=="coding-relations"}><CodingRelationsView projectId={projectId} active={location.kind==="coding-relations"}/></div>}
                  {agreementOpened&&<div hidden={location.kind!=="coding-agreement"}><CodingAgreementView projectId={projectId} active={location.kind==="coding-agreement"}/></div>}
                  {matrixOpened&&<div hidden={location.kind!=="coding-matrix"}><CodingMatrixView key={matrixCurrentRequest} projectId={projectId} active={location.kind==="coding-matrix"} currentOnOpen={matrixCurrentRequest>0}/></div>}
                  {journalsOpened&&<div hidden={location.kind!=="journals"}><ResearchJournals projectId={projectId} active={location.kind==="journals"} onMemoChange={setJournalMemo}/></div>}
                  {importing && (
                    <WorkspaceImport
                      projectId={projectId}
                      onImported={() => { void inventory.refetch(); void datasets.refetch(); void materials.refetch(); }}
                      onClose={() => setImporting(false)}
                    />
                  )}
                  {datasets.isError || materials.isError ? (
                    <p role="alert" className="rw-error">
                      {datasets.error?.message || materials.error?.message}
                      <button
                        onClick={() => {
                          void datasets.refetch();
                          void materials.refetch();
                        }}
                      >
                        重新读取
                      </button>
                    </p>
                  ) : null}
                  {location.kind === "file" && fileHistory.canBack && <button className="rw-preview-back" onClick={() => fileHistory.back(id => go({kind: "file", id}, true))}>{backLabel(datasets.data?.find(d=>d.id===fileHistory.previousId))}</button>}
                  {location.kind === "file" && currentFile?.kind === "data" && (
                    <AdviceHeaderSlot.Provider value={adviceHost}>
                    <WorkspaceDataTable
                      hasFileHeading
                      key={currentFile.id}
                      dataset={datasets.data!.find(
                        (item) => item.id === currentFile.id,
                      )!}
                      datasets={datasets.data ?? []}
                      caseSource={caseSource?.datasetId===currentFile.id ? caseSource : undefined}
                      onChoose={(id) => { if (id !== location.id) { fileHistory.push(); go({ kind: "file", id }, true); } }}
                    />
                    </AdviceHeaderSlot.Provider>
                  )}
                  {location.kind === "file" &&
                    currentFile?.kind === "material" && (
                      <WorkspaceMaterial
                        key={currentFile.id}
                        material={materials.data!.find(
                          (item) => item.id === currentFile.id,
                        )!}
                      />
                    )}
                  {location.kind === "file" &&
                    !currentFile &&
                    !datasets.isPending &&
                    !materials.isPending && (
                      <p role="alert">文件暂时不可用，请从左侧重新选择。</p>
                    )}
                  {explorationOpened && (
                    <div className="rw-exploration-intro" hidden={location.kind !== "explore"}>
                      <WorkspaceExploration projectId={projectId} tasks={tasks.data ?? []} datasets={datasets.data ?? []}
                        onApply={(taskId, config) => { go({kind:"task",id:taskId}); setNavigation({nonce:Date.now(),taskId,statisticsProposal:config}); }}/>
                      <button onClick={() => setAgent(true)}>
                        <MessageSquareIcon size={16} />
                        展开项目探索对话
                      </button>
                      <details><summary>项目文件</summary>
                      {shownFiles.slice(0, 8).map((file) => (
                        <button
                          className="rw-file-list-row"
                          key={file.id}
                          onClick={() => go({ kind: "file", id: file.id })}
                        >
                          <FileTypeIcon file={file} />
                          <span>{file.path}</span>
                          <small>{file.format}</small>
                        </button>
                      ))}</details>
                    </div>
                  )}
                  {location.kind === "data" && <>
                    <div className="rw-content-tools"><h2>工作区文件</h2><div className="rw-inline-actions"><button onClick={() => void refreshFiles()}>刷新</button></div></div>
                    {inventory.isPending && <p role="status">正在读取文件夹…</p>}
                    {inventory.isError && <p role="alert">{inventory.error.message}<button onClick={() => void inventory.refetch()}>重试</button></p>}
                    {[{title: "原文件", rows: rootFiles}, {title: "已添加文件", rows: addedFiles}, {title: "派生数据", rows: generatedFiles}].map(group => {
                      if (!group.rows.length) return null;
                      const fileTable = (<table className="rw-file-list"><thead><tr><th>文件</th><th>格式</th><th>{group.title === "派生数据" ? "研究问题" : "大小"}</th></tr></thead><tbody>{group.rows.map(file => {
                        const owner = tasks.data?.filter(row => row.payload.dataset_id === file.id || row.payload.dataset_ids?.includes(file.id));
                        return <tr key={file.id}><td><button onClick={() => selectFile(file)}><FileTypeIcon file={file} /><span>{group.rows === rootFiles ? file.path : file.name}</span></button></td><td>{file.format}</td><td>{group.title === "派生数据" ? owner?.length ? owner.map(row => <button key={row.id} onClick={() => {go({kind: "task", id: row.id}); setNavigation({nonce: Date.now(), taskId: row.id, datasetId: file.id, tab: "data"});}}>{row.payload.title}</button>) : "未关联" : `${(file.size / 1024).toFixed(1)} KB`}</td></tr>;
                      })}</tbody></table>);
                      if (group.rows === generatedFiles) return <details key={group.title} className="rw-inventory-group" open={!rootFiles.length && !addedFiles.length || undefined}><summary>{group.title} <small>{group.rows.length}</small></summary>{fileTable}</details>;
                      return <section key={group.title}>{group.rows === addedFiles && <h2 className="rw-data-group-title">{group.title}</h2>}{fileTable}</section>;
                    })}
                    {groups.length > 0 && <details className="rw-inventory-group"><summary>材料组与联合查看 <small>{groups.length}</small></summary>
                      {groups.map(session => <button className="rw-session-entry" key={session.id} onClick={() => go({kind: "file", id: session.id})}><ChartNoAxesColumnIncreasingIcon size={18} />{session.title}{session.kind === "media" && <small> · {session.files.find(path=>/\.(mp4|wav)$/i.test(path))?.split("/").pop()}</small>}</button>)}
                    </details>}
                    {!rootFiles.length && !addedFiles.length && !outputIds.size && !inventory.isPending && !datasets.isPending && <div className="rw-empty-project"><FolderOpenIcon size={38}/><button onClick={() => setImporting(true)}>添加文件</button></div>}
                  </>}
                </div>
              </>
            )}
          </section>
          <aside
            className="rw-agent"
            hidden={!agent}
            aria-label="项目探索 Agent"
          >
            <ResizeHandle label="调整 Agent 宽度" value={effectiveRight} onChange={updateRight} direction={-1} min={280} max={Math.min(600, Math.max(280, viewportWidth - (sidebarOpen ? effectiveLeft : 0) - 440))} />
            <div className="rw-agent-heading">
              <strong>
                <MessageSquareIcon size={16} />
                Agent
              </strong>
              <button onClick={() => setAgent(false)} aria-label="收起 Agent">
                <XIcon size={18} />
              </button>
            </div>
            <ProjectAssistant
              projectId={projectId}
              contextKey="project"
              onOpenResult={result => {
                if (result.kind !== "dataset") return false;
                void Promise.all([datasets.refetch(), inventory.refetch()]).then(() => go({kind:"file",id:result.id}));
                return true;
              }}
              initialDatasetId={
                currentFile?.kind === "data" ? currentFile.id : undefined
              }
            />
          </aside>
        </div>
        </div>{annotations && <div className="rw-annotation-wrap"><ResizeHandle label="调整标注面板宽度" value={effectiveRight} onChange={updateRight} direction={-1} min={280} max={Math.min(600, Math.max(280, viewportWidth - (sidebarOpen ? effectiveLeft : 0) - 440))} /><WorkspaceAnnotationPanel /></div>}</div>
      </div>
      <WorkspaceRunNotice project={projectId} open={(id,task)=>{if(task){go({kind:"task",id:task});setNavigation({nonce:Date.now(),taskId:task,tab:"data",datasetId:id});}else go({kind:"file",id});}}/>
      {history && <WorkspaceHistory projectId={projectId} tasks={tasks.data ?? []} datasets={datasets.data ?? []} currentTaskId={currentTask?.id} initialSourceId={historySource} onClose={() => setHistory(false)} onOpen={(target) => void openHistoryTarget(target)} />}
    </div>
    </WorkspaceAnnotationProvider>
    </ResultNavigation.Provider>
  );
}
