"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRightIcon,
  FileIcon,
  FileTextIcon,
  FolderIcon,
  ImageIcon,
  MusicIcon,
  Table2Icon,
} from "lucide-react";
import { datasetsApi } from "@/lib/api-endpoints";
import type { Dataset, DatasetPreview } from "@/lib/types";
import type { Material } from "@/lib/material-types";
export { WorkspaceMaterial } from "./workspace-material-view";
import { DataPreparation } from "./data-preparation";
import { SourceFileUpdate } from "./source-file-update";
import { DownloadButton } from "./download-button";
import { downloadApiFile } from "@/lib/api-download";
import { PlotWorkbench } from "./plot-workbench";
import { MediaOwnership } from "./workspace-result-navigation";
import { MaterialSuggestions } from "./material-suggestions";
import { WorkspaceVisualSamples } from "./workspace-visual-samples";
import { WorkspaceTranscription } from "./workspace-transcription";
import { WorkspaceSourceFile } from "./workspace-source-file";
import {WorkspacePitch} from "./workspace-pitch";
import { WorkspaceSignals } from "./workspace-signals";
import { WorkspaceMediaSamples } from "./workspace-media-samples";
import type { CaseSourceContext } from "./workspace-cases";
import { MediaProcessingLauncher } from "./workspace-media-launcher";
import { MediaResultView, MediaRowLink } from "./workspace-media-results";
import { datasetOrigin, datasetTitle, soundMeasures, type WorkspaceInventory } from "@/lib/workspace-files";
import { analysisTableVersions, versionTitle } from "@/lib/analysis-tables";
import { apiFetch } from "@/lib/api-client";
import { CodingMatrixView } from "./coding-matrix";
import { CodingAgreementView } from "./coding-agreement";
import {SurveyImport} from "./survey-import";

export type WorkspaceFile = {
  id: string;
  kind: "data" | "material";
  name: string;
  path: string;
  format: string;
  size: number;
  raw?: boolean;
  origin: "workspace" | "added" | "generated" | "session";
};
type Tree = {
  name: string;
  path: string;
  children: Map<string, Tree>;
  files: WorkspaceFile[];
};
export function workspaceFiles(
  datasets: Dataset[],
  materials: Material[],
  tables = analysisTableVersions(datasets),
): WorkspaceFile[] {
  return [
    ...datasets.map((d) => ({
      id: d.id,
      kind: "data" as const,
      name: tables.has(d.id) ? versionTitle(tables.get(d.id)!) : datasetTitle(d, datasets),
      path: d.source_path || d.original_filename,
      format:
        d.file_format.toUpperCase() +
        (d.sheet_name ? ` · ${d.sheet_name}` : ""),
      size: d.file_size,
      raw: d.raw_file,
      origin: datasetOrigin(d),
    })),
    ...materials.filter((m) => !datasets.some((d) => m.metadata.source_dataset_id === d.id || d.material_id === m.id || (d.raw_file && d.sha256 === m.sha256 &&
      (d.source_path || d.original_filename) === (m.metadata.source_path || m.filename)))).map((m) => ({
      id: m.id,
      kind: "material" as const,
      name: m.filename,
      path: m.metadata.source_path || m.filename,
      format: m.filename.split(".").pop()?.toUpperCase() || "文件",
      size: m.file_size,
      origin: "added" as const,
    })),
  ];
}
export function FileTypeIcon({ file }: { file: WorkspaceFile }) {
  const Icon =
    file.kind === "data" && !file.raw
      ? Table2Icon
      : /png|jpg|jpeg|gif|webp|svg/i.test(file.format)
        ? ImageIcon
        : /wav|mp3|mp4/i.test(file.format)
          ? MusicIcon
          : /txt|md|pdf/i.test(file.format)
            ? FileTextIcon
            : FileIcon;
  return <Icon size={15} aria-hidden />;
}
export function WorkspaceFileTree({
  files,
  selected,
  onSelect,
  folders = [],
  checked = [], onCheck, checkDisabled = false,
}: {
  files: WorkspaceFile[];
  selected: string;
  onSelect: (file: WorkspaceFile) => void;
  folders?: string[];
  checked?: string[]; checkDisabled?: boolean; onCheck?: (file: WorkspaceFile, checked: boolean) => void;
}) {
  const root = useMemo(() => {
    const tree: Tree = { name: "", path: "", children: new Map(), files: [] };
    for (const folder of folders) {
      let current = tree;
      for (const name of folder.split("/")) {
        const path = `${current.path}/${name}`;
        if (!current.children.has(name)) current.children.set(name, { name, path, children: new Map(), files: [] });
        current = current.children.get(name)!;
      }
    }
    for (const file of files) {
      const parts = file.path.replaceAll("\\", "/").split("/").filter(Boolean);
      let current = tree;
      for (const name of parts.slice(0, -1)) {
        const path = `${current.path}/${name}`;
        if (!current.children.has(name))
          current.children.set(name, {
            name,
            path,
            children: new Map(),
            files: [],
          });
        current = current.children.get(name)!;
      }
      current.files.push(file);
    }
    return tree;
  }, [files, folders]);
  function branch(tree: Tree, depth: number): React.ReactNode {
    return (
      <>
        {[...tree.children.values()].map((folder) => (
          <details key={folder.path} open className="rw-tree-folder">
            <summary style={{ paddingLeft: 12 + depth * 13 }}>
              <ChevronRightIcon size={12} />
              <FolderIcon size={14} />
              <span>{folder.name}</span>
            </summary>
            {branch(folder, depth + 1)}
          </details>
        ))}
        {tree.files.map((file) => (
          <div key={file.id} style={{display:"flex",alignItems:"center"}}>
          {onCheck && <input type="checkbox" aria-label={`选择 ${file.name}`} disabled={checkDisabled || file.kind!=="data" || file.id.startsWith("path:")} checked={checked.includes(file.id)} onChange={event=>onCheck(file,event.target.checked)}/>}
          <button
            type="button"
            className={`rw-tree-file ${selected === file.id ? "active" : ""}`}
            style={{ paddingLeft: 25 + depth * 13 }}
            title={file.path}
            aria-current={selected === file.id ? "true" : undefined}
            onClick={() => onSelect(file)}
          >
            <FileTypeIcon file={file} />
            <span>
              {file.path.split(/[\\/]/).pop()}
              {file.format.includes(" · ")
                ? ` · ${file.format.split(" · ")[1]}`
                : ""}
            </span>
          </button></div>
        ))}
      </>
    );
  }
  return <div className="rw-file-tree">{branch(root, 0)}</div>;
}
export function WorkspaceDataTable({
  dataset,
  datasets,
  onChoose,
  preparation = false,
  revision,
  taskId,
  onSaved,
  caseSource,
  hasFileHeading,
  versionPicker,
}: {
  dataset: Dataset;
  datasets: Dataset[];
  onChoose: (id: string) => void;
  preparation?: boolean;
  revision?: number;
  taskId?: string;
  onSaved?: () => Promise<void>;
  caseSource?: CaseSourceContext;
  hasFileHeading?: boolean;
  versionPicker?: React.ReactNode;
}) {
  const [plot, setPlot] = useState<{ source: string; draft: string; run: string } | null>(null);
  const source = [dataset.project_id, taskId, dataset.id, dataset.sha256].join(":");
  if (dataset.coding_matrix) return <CodingMatrixView projectId={dataset.project_id} active snapshotId={dataset.id}/>;
  if (dataset.coding_agreement) return <CodingAgreementView projectId={dataset.project_id} active snapshotId={dataset.id}/>;
  if (plot?.source === source) return <>
    <button type="button" className="rw-preview-back" onClick={() => setPlot(null)}>返回材料</button>
    <PlotWorkbench projectId={dataset.project_id} initialId={plot.draft} initialRunId={plot.run} embedded />
  </>;
  const suggestions=<MaterialSuggestions key={source} dataset={dataset} taskId={taskId} onChoose={onChoose}
    version={String(revision||0)}
    onPlot={(draft,run)=>setPlot({source,draft,run})}/>;
  return <>
    {/^(csv|xlsx)$/.test(dataset.file_format)&&<SurveyImport key={dataset.id} dataset={dataset} onChoose={onChoose}/>}
    {dataset.media_result && <MediaOwnership {...{dataset,datasets,onChoose,taskId}}/>}
    {!dataset.raw_file && !dataset.media_case && !(dataset.media_result && dataset.file_format==="npz") && suggestions}
    <WorkspaceDataContent {...{dataset,datasets,onChoose,preparation,taskId,onSaved,caseSource,hasFileHeading,versionPicker}} suggestions={suggestions}/>
  </>;
}
function WorkspaceDataContent({dataset,datasets,onChoose,preparation,taskId,onSaved,suggestions,caseSource,hasFileHeading,versionPicker}:Parameters<typeof WorkspaceDataTable>[0]&{suggestions:React.ReactNode}) {
  if (dataset.file_format === "pitch") return <WorkspacePitch key={dataset.id} dataset={dataset} datasets={datasets} onChoose={onChoose} taskId={taskId}/>;
  if (dataset.media_case) return <WorkspaceMediaSamples versionPicker={versionPicker} key={dataset.id} dataset={dataset} datasets={datasets} onChoose={onChoose} taskId={taskId} onSaved={onSaved} suggestions={suggestions} initialSampleId={caseSource?.sampleId}/>;
  if (dataset.media_result && dataset.file_format === "npz") return <MediaResultView dataset={dataset} datasets={datasets} onChoose={onChoose} taskId={taskId} />;
  if (dataset.signal_session) return <WorkspaceSignals key={dataset.id} dataset={dataset} datasets={datasets} onChoose={onChoose} taskId={taskId} onSaved={onSaved} />;
  if (dataset.raw_file) return <LinkedSourceFile suggestions={suggestions} onSaved={onSaved} key={dataset.id} dataset={dataset} datasets={datasets} onChoose={onChoose} taskId={taskId} caseSource={caseSource} hasFileHeading={hasFileHeading} />;
  return <PreparedDataTable key={dataset.id} dataset={dataset} datasets={datasets} onChoose={onChoose} preparation={preparation} taskId={taskId} />;
}
function LinkedSourceFile({ dataset, datasets, onChoose, taskId, onSaved, suggestions, caseSource, hasFileHeading }: { suggestions:React.ReactNode; onSaved?:()=>Promise<void>;
  dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; taskId?: string; caseSource?:CaseSourceContext; hasFileHeading?: boolean;
}) {
  const [linkError, setLinkError] = useState("");
  const [pickedCase,setPickedCase]=useState<CaseSourceContext>();
  const currentCase=caseSource || pickedCase;
  const [range,setRange]=useState<{start:number;end:number}>();
  useEffect(()=>{if(caseSource?.start!=null && caseSource.end!=null)setRange({start:caseSource.start,end:caseSource.end});},[caseSource?.collectionId,caseSource?.sampleId,caseSource?.start,caseSource?.end]);
  function viewRange(start:number,end:number) {
    setRange(old=>old?.start===start&&old.end===end?old:{start,end});
  }
  const client = useQueryClient();
  const inventory = useQuery({ queryKey: ["workspace-files", dataset.project_id],
    queryFn: ({ signal }) => apiFetch<WorkspaceInventory>(`/api/v1/projects/${dataset.project_id}/workspace-files`, { signal }) });
  const links: {id:string; title:string; sampleId?:string}[] = [];
  const targets = new Set<string>();
  for (const session of [...(inventory.data?.sessions || [])].sort((a,b)=>(b.created || "").localeCompare(a.created || ""))) {
    const collection = datasets.find(item => item.id === session.id)?.media_case;
    if (collection?.purpose === "temporary") continue;
    const matches = (collection?.samples || []).filter(sample => !sample.case_deleted && sample.case_title &&
      ([sample.primary_id, sample.video_id, sample.audio_id, sample.text_id, sample.pitch_id].includes(dataset.id) ||
       sample.members?.some(member => member.id === dataset.id && member.sha256 === dataset.sha256)));
    if (!matches.length && (!dataset.local_file || !session.files.includes(dataset.source_path || ""))) continue;
    const options = matches.length ? matches.map(s=>({sampleId:s.sample_id, title:s.case_title!})) : [{sampleId:undefined,title:session.title}];
    for (const option of options) {
      const key = (session.group_id || session.id) + ":" + (option.sampleId || "");
      if (targets.has(key)) continue;
      targets.add(key);
      if (session.id === currentCase?.collectionId && option.sampleId === currentCase.sampleId) continue;
      links.push({id:session.id,...option});
    }
  }
  async function openSession(id: string, sampleId?:string) {
    setLinkError("");
    try {
      if (datasets.find(item => item.id === id)?.media_case) {
        const result = await apiFetch<{ selection?: {sample_id?:string}; samples: { sample_id: string; video_id?: string; audio_id?: string; text_id?: string; image_dataset_id?: string }[] }>(`/api/v1/datasets/${id}/samples${taskId ? "?task_id="+encodeURIComponent(taskId) : ""}`);
        const sample = result.samples.find(item => sampleId ? item.sample_id === sampleId : [item.video_id, item.audio_id, item.text_id, item.image_dataset_id].includes(dataset.id));
        if (!sample) throw new Error("未找到此文件对应的样本，请刷新文件夹后重试。");
        if (result.selection?.sample_id !== sample.sample_id) await apiFetch(`/api/v1/datasets/${id}/sample-selection`, { method: "POST", body: JSON.stringify({ task_id: taskId, sample_id: sample.sample_id, start: null, end: null }) });
        client.removeQueries({ queryKey: ["media-samples", id, taskId] });
        sessionStorage.removeItem("statforge:material-results:"+id+":"+(taskId || "project"));
      }
      onChoose(id);
    } catch (reason) { setLinkError(reason instanceof Error ? reason.message : "无法打开相关样本。"); }
  }
  const pair = dataset.transcript_pair;
  async function returnToPair() {
    if (!pair) return;
    setLinkError("");
    try {
      const members = datasets.find(item => item.id === pair.video_id)?.sha256 === pair.video_sha256
        && datasets.find(item => item.id === pair.audio_id)?.sha256 === pair.audio_sha256;
      if (!members) throw new Error("原视频或独立 WAV 已变化，请在项目中核对原文件。");
      await apiFetch(`/api/v1/datasets/${pair.collection_id}/sample-selection`, { method:"POST",
        body:JSON.stringify({task_id:taskId, sample_id:pair.sample_id, start:pair.video_start, end:pair.video_end, audio_source:"independent"}) });
      client.removeQueries({queryKey:["media-samples",pair.collection_id,taskId]});
      sessionStorage.removeItem("statforge:material-results:"+pair.collection_id+":"+(taskId || "project"));
      onChoose(pair.collection_id);
    } catch (reason) { setLinkError(reason instanceof Error ? reason.message : "无法打开配对片段。"); }
  }
  return <>
    {currentCase && <button type="button" className="rw-preview-back" onClick={()=>onChoose(currentCase.collectionId)}>返回案例</button>}
    {pair && <button type="button" onClick={() => void returnToPair()}>回看配对片段 · 视频 {Number(pair.video_start.toFixed(3))}–{Number(pair.video_end.toFixed(3))} 秒</button>}
    {["wav","mp4"].includes(dataset.file_format) && <div className="rw-material-actions rw-source-actions">
      <WorkspaceTranscription dataset={dataset} taskId={taskId} onChoose={onChoose} initialRange={range}/>
      {(!dataset.media_result||dataset.file_format==="wav")&&<MediaProcessingLauncher key={`${dataset.id}:${currentCase?.collectionId || ""}:${currentCase?.sampleId || ""}`} dataset={dataset} datasets={datasets} taskId={taskId} onChoose={onChoose} range={range} onSaved={onSaved} caseSource={currentCase}/>}
    </div>}
    {suggestions}
    {["jpg","jpeg","gif","png","webp"].includes(dataset.file_format) && <WorkspaceVisualSamples onSaved={onSaved} dataset={dataset} datasets={datasets} taskId={taskId} onChoose={onChoose}/>}
    {linkError && <p className="rw-error" role="alert">{linkError}</p>}
    {!!links.length && <div className="rw-content-tools rw-source-sessions"><div className="rw-inline-actions">
      {links.map(link => <button className="rw-primary" key={link.id+":"+(link.sampleId || "")} onClick={() => void openSession(link.id,link.sampleId)}>{link.sampleId ? "查看案例" : "联合查看"} · {link.title}</button>)}
    </div></div>}
    <WorkspaceSourceFile dataset={dataset} datasets={datasets} onChoose={onChoose} taskId={taskId} sampleId={caseSource?.sampleId} sampleCollectionId={caseSource?.collectionId} caseSource={caseSource} onCaseChange={setPickedCase} onViewRange={viewRange} hasFileHeading={hasFileHeading}/>
  </>;
}
function PreparedDataTable({ dataset, datasets, onChoose, preparation, taskId }: {
  dataset: Dataset; datasets: Dataset[]; onChoose: (id: string) => void; preparation?: boolean; taskId?: string;
}) {
  const [search, setSearch] = useState("");
  const [prepare, setPrepare] = useState(false);
  const [sourceLocation,setSourceLocation]=useState<{row:number;column:string;sha256:string;answer_id?:string}|null>(()=>{try{return JSON.parse(sessionStorage.getItem("statforge:table-location:"+dataset.id)||"null");}catch{return null;}});
  const preview = useQuery({
    queryKey: ["dataset-preview", dataset.id, 100, sourceLocation],
    queryFn: ({ signal }) => sourceLocation?apiFetch<DatasetPreview>(`/api/v1/projects/${dataset.project_id}/coding/survey/${dataset.id}/row/${sourceLocation.row}?sha256=${sourceLocation.sha256}`,{signal}):datasetsApi.preview(dataset.id, 100, signal),
  });
  const data = preview.data;
  const columns = (data?.columns ?? []).filter(column => {
    if (dataset.coding_summary) return !["case_key", "sample_id", "sample_collection_id"].includes(column.name);
    if (dataset.media_result && column.name === "sample_id" && data?.columns.some(item => item.name === "title")) {
      return !data.rows.every(row => typeof row.sample_id === "string" && /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(row.sample_id));
    }
    return true;
  });
  const rows =
    data?.rows.map((row, originalIndex) => ({ row, originalIndex })).filter(
      ({ row }) =>
        !search ||
        Object.values(row).some((value) =>
          String(value ?? "")
            .toLocaleLowerCase()
            .includes(search.toLocaleLowerCase()),
        ),
    ) ?? [];
  return (
    <div className="rw-data-view">
      {sourceLocation&&<p>原表第 {sourceLocation.row} 行 · {sourceLocation.column} {sourceLocation.answer_id&&<button onClick={()=>onChoose(sourceLocation.answer_id!)}>返回回答</button>} <button onClick={()=>{sessionStorage.removeItem("statforge:table-location:"+dataset.id);setSourceLocation(null);}}>查看全部预览</button></p>}
      <div className="rw-content-tools rw-record-tools">
        <span>
          {dataset.row_count?.toLocaleString() ?? "—"} 行 ·{" "}
          {dataset.column_count ?? "—"} 个变量
          {dataset.preparation_steps ? " · 处理版本" : ""}
          {search && data && <small role="status"> · 匹配 {rows.length.toLocaleString()} 行</small>}
        </span>
        <div className="rw-inline-actions">
          <input
            aria-label="搜索预览行"
            placeholder={data && data.showing_rows < data.total_rows && !sourceLocation ? `搜索前 ${data.showing_rows} 行` : "搜索预览行"}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {(preparation || datasets.some(d=>d.id!==dataset.id && d.upload_status==="ready")) && (
            <button
              type="button"
              onClick={() => setPrepare(!prepare)}
              aria-expanded={prepare}
            >
              处理与关联表格
            </button>
          )}
          <DownloadButton
            identity={dataset.id}
            download={(signal) =>
              datasetsApi.download(
                dataset.id,
                dataset.original_filename,
                signal,
              )
            }
          >
            导出数据
          </DownloadButton>
        </div>
      </div>
      {dataset.multimodal && (
        <section className="rw-multimodal-summary" aria-label="多模态特征映射">
          <div className="rw-section-heading">
            <h3>多模态特征</h3>
            <DownloadButton
              identity={`${dataset.id}:pkl`}
              download={(signal) =>
                downloadApiFile(
                  `/api/v1/datasets/${dataset.id}/multimodal/source`,
                  dataset.multimodal!.source_filename,
                  signal,
                )
              }
            >
              导出原 PKL
            </DownloadButton>
          </div>
          <p>
            来源 {dataset.multimodal.source_filename} · 节点{" "}
            {dataset.multimodal.node.join("/") || "根节点"} · 标签{" "}
            {dataset.multimodal.label_column}（
            {dataset.multimodal.task === "regression"
              ? "回归"
              : `${dataset.multimodal.label_count} 类`}
            ）
          </p>
          <div className="rw-table-scroll">
            <table className="rw-data-table">
              <thead>
                <tr>
                  <th>模态</th>
                  <th>原样本形状</th>
                  <th>序列处理</th>
                  <th>输出特征</th>
                </tr>
              </thead>
              <tbody>
                {dataset.multimodal.modalities.map((modality) => (
                  <tr key={modality.name}>
                    <td>{modality.name}</td>
                    <td>[{modality.sample_shape.join(" × ")}]</td>
                    <td>
                      {{
                        mean: "均值聚合",
                        max: "最大值聚合",
                        flatten: "全部展开",
                      }[modality.pooling] ?? modality.pooling}
                    </td>
                    <td>
                      {modality.input_dimensions} → {modality.output_dimensions}{" "}
                      维{modality.projection ? " · 固定投影" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="rw-muted">
            当前节点单独保存。后续机器学习在此子集内重新划分，不代表使用数据集官方测试集评估。
          </p>
        </section>
      )}
      {preview.isPending && <p role="status">正在读取数据…</p>}
      {preview.isError && (
        <p role="alert">
          {preview.error.message}
          <button onClick={() => void preview.refetch()}>重新读取</button>
        </p>
      )}
      {prepare && data && (
        <DataPreparation
          dataset={dataset}
          datasets={datasets}
          columns={data.columns}
          onChoose={onChoose}
        />
      )}
      {data && (
        <>
          <div className="rw-table-scroll rw-record-table" tabIndex={0} role="region" aria-label="数据预览表格">
            <table className="rw-data-table">
              <caption className="sr-only">
                {dataset.original_filename} 的前 {data.showing_rows} 行
              </caption>
              <thead>
                <tr>
                  <th scope="col">行</th>
                  {columns.map((column) => (
                    <th scope="col" key={column.name} title={column.name}>
                      {(dataset.media_result && soundMeasures[column.name]) || column.name}
                      <small>{dataset.media_result && soundMeasures[column.name] ? column.name : column.inferred_type}</small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(({ row, originalIndex }) => (
                  <tr key={originalIndex}>
                    <th scope="row">{(dataset.coding_summary || dataset.visual_result || dataset.media_result || (dataset.source_dataset_id && row.Index != null)) ? <MediaRowLink dataset={dataset} row={row} datasets={datasets} onChoose={onChoose} taskId={taskId} /> : sourceLocation?.row ?? originalIndex + 1}</th>
                    {columns.map((column) => (
                      <td key={column.name} style={sourceLocation?.column===column.name?{background:"var(--rw-active)",fontWeight:600}:undefined}>
                        {row[column.name] == null ? (
                          <span className="rw-muted">—</span>
                        ) : typeof row[column.name] === "object" ? (
                          JSON.stringify(row[column.name])
                        ) : (
                          String(row[column.name])
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.showing_rows < data.total_rows && !sourceLocation && (
            <p className="rw-muted rw-footnote">
              预览前 {data.showing_rows.toLocaleString()} 行 · 计算使用完整数据
            </p>
          )}
        </>
      )}
      {!preparation && (
        <details className="rw-source-details">
          <summary>文件来源与版本</summary>
          <p>{dataset.source_path || dataset.original_filename}</p>
          <SourceFileUpdate dataset={dataset} onSelect={onChoose} />
        </details>
      )}
    </div>
  );
}
