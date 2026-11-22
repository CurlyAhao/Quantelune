"use client";

import { useState } from "react";
import type { Dataset, StatisticalRunRequest } from "@/lib/types";
import type { TaskRecord } from "@/lib/use-workspace-task";
import { FormalResearch } from "./formal-research";
import { SearchSelect } from "./ui/search-select";

export function WorkspaceExploration({ projectId, tasks, datasets, onApply }: {
  projectId: string; tasks: TaskRecord[]; datasets: Dataset[];
  onApply: (taskId: string, config: StatisticalRunRequest) => void;
}) {
  const [context, setContext] = useState("project");
  const [selectedDataset, setSelectedDataset] = useState("");
  const task = tasks.find(item => item.id === context);
  const datasetId = task ? task.payload.dataset_id : selectedDataset;
  const dataset = datasets.find(item => item.id === datasetId);
  return <div className="rw-exploration">
    <div className="rw-exploration-scope">
      <label>讨论归属<SearchSelect value={context} onChange={event => setContext(event.target.value)}>
        <option value="project">项目探索</option>
        {tasks.map(item => <option key={item.id} value={item.id}>{item.payload.title}</option>)}
      </SearchSelect></label>
      {!task && <label>讨论数据<SearchSelect value={selectedDataset} onChange={event => setSelectedDataset(event.target.value)}>
        <option value="">项目材料</option>
        {datasets.filter(item => item.upload_status === "ready").map(item => <option key={item.id} value={item.id}>{item.original_filename}</option>)}
      </SearchSelect></label>}
      {task && dataset && <span className="rw-exploration-data" title={dataset.original_filename}>{dataset.original_filename}</span>}
    </div>
    <div className="rw-exploration-discussion">
      <FormalResearch key={context} projectId={projectId} contextKey={context}
        initialDatasetId={datasetId || undefined}
        initialQuestion={task ? `${task.payload.question || task.payload.title}${task.payload.exploration_context ? `\n\n${task.payload.exploration_context}` : ""}` : undefined}
        manualSource={dataset ? { datasetId: dataset.id, sha256: dataset.sha256 } : undefined}
        onApplyStatistics={task ? config => onApply(task.id, config) : undefined}/>
    </div>
  </div>;
}
