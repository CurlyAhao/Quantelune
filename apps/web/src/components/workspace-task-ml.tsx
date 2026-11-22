"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { mlApi } from "@/lib/api-endpoints";
import { MLBuilder } from "@/app/projects/[projectId]/machine-learning/ml-builder";
import { MLResultsView } from "@/app/projects/[projectId]/machine-learning/ml-results";
import type {
  ColumnProfile,
  Dataset,
  MLEditorState,
  MLExperimentCreateRequest,
} from "@/lib/types";

type Preflight = {
  target: string;
  taskType: string;
  splitStrategy: string;
  groupVariable: string | null;
  timeVariable: string | null;
  nSplits: number;
  testSize: number;
  randomSeed: number;
};
export function WorkspaceTaskML({
  dataset,
  columns,
  isRunning,
  initial,
  editor,
  onChange,
  onSubmit,
}: {
  dataset: Dataset;
  columns: ColumnProfile[];
  isRunning: boolean;
  initial: MLExperimentCreateRequest | null;
  editor: MLEditorState | null;
  onChange: (
    config: MLExperimentCreateRequest,
    complete: boolean,
    editor?: MLEditorState,
  ) => void;
  onSubmit: (config: MLExperimentCreateRequest) => void;
}) {
  const metadata = useQuery({
    queryKey: ["ml", "metadata"],
    queryFn: ({ signal }) => mlApi.metadata(signal),
  });
  const [query, setQuery] = useState<Preflight | null>(null);
  const [proposal, setProposal] = useState(() => {
    if (initial) return { id: 0, config: initial };
    const source = dataset.multimodal;
    if (!source) return null;
    const features = source.modalities.flatMap((modality) => modality.columns);
    const regression = source.task === "regression";
    const config: MLExperimentCreateRequest = {
      target: source.label_column,
      features: features.length <= 128 ? features : [],
      task_type: regression
        ? "regression"
        : source.label_count > 2
          ? "multiclass_classification"
          : "binary_classification",
      model_ids: [regression ? "ridge" : "logistic_regression"],
      split_strategy: "train_test",
      test_size: 0.2,
      random_seed: 42,
      n_splits: 2,
      standardize: true,
    };
    return { id: 0, config };
  });
  const currentConfig = useRef(proposal?.config ?? null);
  const [modalityError, setModalityError] = useState("");
  const [initialEditor] = useState(editor);
  const stats = useQuery({
    queryKey: ["workspace-ml-preflight", dataset.id, query],
    queryFn: ({ signal }) =>
      mlApi.preflightStats({ datasetId: dataset.id, ...query! }, signal),
    enabled: !!query?.target,
    retry: false,
  });
  if (metadata.isPending) return <p role="status">读取模型目录…</p>;
  if (metadata.isError) return <p role="alert">{metadata.error.message}</p>;
  return (
    <>
      {dataset.multimodal && (
        <section className="rw-multimodal-summary" aria-label="多模态分析设置">
          <h3>按模态选择特征</h3>
          <p className="rw-muted">
            标签和特征已按导入映射填入，可继续调整模型。只使用当前节点，在此子集内重新划分；不是官方测试集评估。
          </p>
          <div className="rw-inline-actions">
            {dataset.multimodal.modalities.map((modality) => (
              <button
                type="button"
                key={modality.name}
                disabled={isRunning || modality.columns.length > 128}
                onClick={() => {
                  const config = currentConfig.current;
                  if (!config) return;
                  const selected = modality.columns.every((column) =>
                    config.features.includes(column),
                  );
                  const features = selected
                    ? config.features.filter(
                        (column) => !modality.columns.includes(column),
                      )
                    : [...new Set([...config.features, ...modality.columns])];
                  if (features.length > 128) {
                    setModalityError(
                      "合计超过 128 个特征，请先移除其他模态，或在下方选择部分维度。",
                    );
                    return;
                  }
                  setModalityError("");
                  currentConfig.current = { ...config, features };
                  setProposal({
                    id: Date.now(),
                    config: currentConfig.current,
                  });
                }}
              >
                {modality.name} · {modality.columns.length} 维：加入／移除
              </button>
            ))}
          </div>
          {dataset.multimodal.modalities.reduce(
            (sum, modality) => sum + modality.columns.length,
            0,
          ) > 128 && (
            <p className="rw-muted">
              完整特征超过现有 128
              维运行上限。请按模态或在下方选择维度，或重新导入并选择更小的固定投影。
            </p>
          )}
          {modalityError && (
            <p role="alert" className="rw-error">
              {modalityError}
            </p>
          )}
        </section>
      )}
      <MLBuilder
        compact
        columns={columns}
        metadata={metadata.data}
        isRunning={isRunning}
        onSubmit={onSubmit}
        onConfigChange={(config, complete, state) => {
          currentConfig.current = config;
          onChange(config, complete, state);
        }}
        proposal={proposal}
        editor={initialEditor}
        datasetSha256={dataset.sha256}
        preflightStats={stats.data ?? null}
        onPreflightQueryChange={setQuery}
      />
      {stats.isError && (
        <p className="rw-error" role="alert">
          {stats.error.message}
        </p>
      )}
    </>
  );
}
export function WorkspaceMLResult({ runId }: { runId: string }) {
  const run = useQuery({
    queryKey: ["ml-run", runId],
    queryFn: ({ signal }) => mlApi.getRun(runId, signal),
  });
  if (run.isPending) return <p role="status">读取模型结果…</p>;
  if (run.isError) return <p role="alert">{run.error.message}</p>;
  return <MLResultsView run={run.data} />;
}
