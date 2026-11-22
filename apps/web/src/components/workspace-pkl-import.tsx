"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useRef, useState } from "react";
import { apiFetch, apiUpload } from "@/lib/api-client";

type Field = {
  path: string[];
  name: string;
  rows: number;
  shape: number[];
  dtype: string;
  numeric: boolean;
  scalar: boolean;
  sample: string[];
};
type Group = { path: string[]; name: string; fields: Field[] };
type Inspection = {
  groups: Group[];
  tree: {
    path: string[];
    kind: string;
    shape: number[] | null;
    keys: number | null;
  }[];
  sha256: string;
  structure_truncated: boolean;
};
export type PklMapping = {
  node: string[];
  label: string[];
  identifier: string[] | null;
  modalities: {
    path: string[];
    name: string;
    pooling: "mean" | "max" | "flatten";
    dimensions: 0 | 16 | 32 | 64;
    lengths: string[] | null;
  }[];
  task: "classification" | "regression";
  expected_sha256: string;
};
const key = (path: string[]) => JSON.stringify(path);
const shape = (field: Field) => `[${field.shape.join(" × ")}]`;

function outputCount(mapping: PklMapping, group?: Group) {
  return mapping.modalities.reduce((sum, modality) => {
    const field = group?.fields.find(
      (item) => key(item.path) === key(modality.path),
    );
    const raw =
      modality.pooling === "flatten"
        ? (field?.shape.slice(1).reduce((a, b) => a * b, 1) ?? 0)
        : (field?.shape.at(-1) ?? 0);
    return (
      sum + (modality.dimensions ? Math.min(raw, modality.dimensions) : raw)
    );
  }, 0);
}

function ready(mapping: PklMapping, inspection: Inspection | null) {
  const group = inspection?.groups.find(
    (item) => key(item.path) === key(mapping.node),
  );
  const label = group?.fields.find(
    (field) => key(field.path) === key(mapping.label),
  );
  return (
    !!label &&
    mapping.modalities.length > 0 &&
    outputCount(mapping, group) <= 498 &&
    mapping.modalities.every(
      (modality) =>
        modality.name.trim() &&
        key(modality.path) !== key(mapping.label) &&
        key(modality.path) !== key(mapping.identifier ?? []) &&
        group?.fields.find((field) => key(field.path) === key(modality.path))
          ?.rows === label.rows,
    )
  );
}

export function WorkspacePklImport({
  file,
  datasetId,
  projectId,
  resolveProjectId,
  disabled,
  onChange,
}: {
  file?: File;
  datasetId?: string;
  projectId?: string;
  resolveProjectId?: () => Promise<string>;
  disabled: boolean;
  onChange: (mapping: PklMapping | null) => void;
}) {
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [mapping, setMapping] = useState<PklMapping | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const inspectedFile = useRef<File | string | null>(null);
  const inspectAction = useRef<() => Promise<void>>(async () => {});
  const group = inspection?.groups.find(
    (item) => key(item.path) === key(mapping?.node ?? []),
  );
  const label = group?.fields.find(
    (field) => key(field.path) === key(mapping?.label ?? []),
  );
  const valid = !!mapping && ready(mapping, inspection);
  function update(next: PklMapping, source = inspection) {
    setMapping(next);
    onChange(ready(next, source) ? next : null);
  }
  function selectGroup(next: Group, digest: string, source = inspection) {
    const targets = next.fields.filter((field) => field.scalar);
    const target = targets.find((field) =>
      /(^|\/)(labels?|y|target|regression_labels|classification_labels)$/i.test(
        field.name,
      ),
    );
    const identifier = targets.find((field) =>
      /(^|\/)(ids?|sample_id|video_id)$/i.test(field.name),
    );
    const modalities = next.fields
      .filter(
        (field) =>
          field.numeric &&
          !field.scalar &&
          /(^|\/)(text|audio|vision|visual|video|image|language|features?|x)$/i.test(
            field.name,
          ),
      )
      .slice(0, 4);
    const nextMapping: PklMapping = {
      node: next.path,
      label: target?.path ?? [],
      identifier: identifier?.path ?? null,
      modalities: modalities.map((field) => ({
        path: field.path,
        name: field.path.at(-1)!,
        pooling: "mean",
        dimensions: 32,
        lengths: null,
      })),
      task: target?.name.endsWith("regression_labels")
        ? "regression"
        : "classification",
      expected_sha256: digest,
    };
    update(nextMapping, source);
    return ready(nextMapping, source);
  }
  async function inspect() {
    setLoading(true);
    setUploadProgress(0);
    setError("");
    try {
      let result: Inspection;
      if (datasetId) {
        setUploadProgress(100);
        result = await apiFetch<Inspection>(`/api/v1/datasets/${datasetId}/source-structure`);
      } else {
        if (!file) throw new Error("请选择项目中的 PKL 文件");
        const form = new FormData();
        form.append("file", file);
        const targetProjectId = projectId ?? (await resolveProjectId?.());
        if (!targetProjectId) throw new Error("请先选择项目后再导入 PKL");
        result = await apiUpload<Inspection>(`/api/v1/projects/${targetProjectId}/imports/pkl/inspect`, form, { onProgress: setUploadProgress });
      }
      if (!result.groups.length) throw new Error("没有识别到可用的样本节点。");
      setInspection(result);
      const identified = selectGroup(
        result.groups.find(
          (item) => item.path.at(-1)?.toLowerCase() === "train",
        ) ?? result.groups[0],
        result.sha256,
        result,
      );
      setSettingsOpen(!identified);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 PKL 结构");
    } finally {
      setLoading(false);
    }
  }
  inspectAction.current = inspect;
  useEffect(() => {
    const identity = datasetId ?? file;
    if (disabled || !identity || inspectedFile.current === identity) return;
    inspectedFile.current = identity;
    void inspectAction.current();
  }, [file, datasetId, disabled]);
  if (!inspection || !mapping)
    return (
      <div className="rw-pkl-import">
        <p role="status" className="rw-muted">
          {loading
            ? uploadProgress < 100
              ? `正在传入本机服务… ${uploadProgress}%`
              : "正在读取数组结构…"
            : error
              ? "PKL 识别未完成"
              : "正在识别 PKL…"}
        </p>
        {error && (
          <button disabled={disabled || loading} onClick={() => void inspect()}>
            重新识别
          </button>
        )}
        {error && (
          <p role="alert" className="rw-error">
            {error}
          </p>
        )}
      </div>
    );
  const outputDimensions = outputCount(mapping, group);
  return (
    <div className="rw-pkl-import">
      <p role="status" className="rw-muted">
        {valid ? "已识别，可生成分析数据" : "需要补充字段设置"} · {group?.name} ·{" "}
        {label?.rows.toLocaleString() ?? "—"} 个样本
        {valid &&
          ` · ${mapping.modalities.map((item) => item.name).join(" / ")} · ${mapping.task === "regression" ? "回归" : "分类"}`}
      </p>
      {valid && (
        <p className="rw-muted">
          标签：{label?.name}
          。默认对全部位置取均值，按所选维度投影；可在字段设置中调整。
        </p>
      )}
      <details
        open={settingsOpen}
        onToggle={(event) => setSettingsOpen(event.currentTarget.open)}
      >
        <summary>字段设置（{valid ? "可选" : "请选择标签与模态"}）</summary>
        <details>
          <summary>查看文件结构 · {inspection.tree.length} 个节点</summary>
          <div className="rw-pkl-structure">
            {inspection.tree.map((node) => (
              <div
                key={key(node.path)}
                style={{ paddingLeft: node.path.length * 14 }}
              >
                <code>{node.path.at(-1) ?? "根节点"}</code>
                <span>
                  {node.kind}
                  {node.shape
                    ? ` [${node.shape.join(" × ")}]`
                    : node.keys != null
                      ? ` · ${node.keys} 个字段`
                      : ""}
                </span>
              </div>
            ))}
          </div>
          {inspection.structure_truncated && (
            <p>结构预览已限制为 300 个节点。</p>
          )}
        </details>
        <div className="rw-pkl-fields">
          <label>
            样本所在节点
            <SearchSelect
              disabled={disabled}
              value={key(mapping.node)}
              onChange={(event) =>
                selectGroup(
                  inspection.groups.find(
                    (item) => key(item.path) === event.target.value,
                  )!,
                  inspection.sha256,
                )
              }
            >
              {inspection.groups.map((item) => (
                <option key={key(item.path)} value={key(item.path)}>
                  {item.name}
                </option>
              ))}
            </SearchSelect>
          </label>
          <label>
            标签字段
            <SearchSelect
              disabled={disabled}
              value={key(mapping.label)}
              onChange={(event) =>
                update({
                  ...mapping,
                  label: JSON.parse(event.target.value),
                  task: event.target.value.includes("regression_labels")
                    ? "regression"
                    : event.target.value.includes("classification_labels")
                      ? "classification"
                      : mapping.task,
                  modalities: mapping.modalities.filter(
                    (item) => key(item.path) !== event.target.value,
                  ),
                })
              }
            >
              <option value="[]">请选择标签</option>
              {group?.fields
                .filter((field) => field.scalar)
                .map((field) => (
                  <option key={key(field.path)} value={key(field.path)}>
                    {field.name} {shape(field)}
                  </option>
                ))}
            </SearchSelect>
          </label>
          <label>
            标签用途
            <SearchSelect
              disabled={disabled}
              value={mapping.task}
              onChange={(event) => {
                const task = event.target.value as PklMapping["task"];
                const target = group?.fields.find(
                  (field) => field.path.at(-1) === `${task}_labels`,
                );
                update({
                  ...mapping,
                  task,
                  label: target?.path ?? mapping.label,
                  modalities: mapping.modalities.filter(
                    (item) => !target || key(item.path) !== key(target.path),
                  ),
                });
              }}
            >
              <option value="classification">分类</option>
              <option value="regression">回归</option>
            </SearchSelect>
          </label>
          <label>
            样本 ID
            <SearchSelect
              disabled={disabled}
              value={key(mapping.identifier ?? [])}
              onChange={(event) =>
                update({
                  ...mapping,
                  identifier:
                    event.target.value === "[]"
                      ? null
                      : JSON.parse(event.target.value),
                  modalities: mapping.modalities.filter(
                    (item) => key(item.path) !== event.target.value,
                  ),
                })
              }
            >
              <option value="[]">按原始行序生成</option>
              {group?.fields
                .filter(
                  (field) =>
                    field.scalar && key(field.path) !== key(mapping.label),
                )
                .map((field) => (
                  <option key={key(field.path)} value={key(field.path)}>
                    {field.name}
                  </option>
                ))}
            </SearchSelect>
          </label>
        </div>
        {label && (
          <p className="rw-muted">
            {label.rows.toLocaleString()} 个样本 · 标签示例：
            {label.sample.join("、")}
          </p>
        )}
        <fieldset disabled={disabled}>
          <legend>选择模态特征</legend>
          <div className="rw-pkl-modalities">
            {group?.fields
              .filter(
                (field) =>
                  field.numeric &&
                  key(field.path) !== key(mapping.label) &&
                  key(field.path) !== key(mapping.identifier ?? []),
              )
              .map((field) => {
                const modality = mapping.modalities.find(
                  (item) => key(item.path) === key(field.path),
                );
                return (
                  <div className="rw-pkl-modality" key={key(field.path)}>
                    <label className="rw-pkl-check">
                      <input
                        type="checkbox"
                        checked={!!modality}
                        onChange={(event) =>
                          update({
                            ...mapping,
                            modalities: event.target.checked
                              ? [
                                  ...mapping.modalities,
                                  {
                                    path: field.path,
                                    name: field.path.at(-1)!,
                                    pooling: "mean",
                                    dimensions: 32,
                                    lengths: null,
                                  },
                                ]
                              : mapping.modalities.filter(
                                  (item) => key(item.path) !== key(field.path),
                                ),
                          })
                        }
                      />
                      <span>
                        {field.name}{" "}
                        <small>
                          {shape(field)} · {field.dtype}
                        </small>
                      </span>
                    </label>
                    {modality && (
                      <div className="rw-pkl-fields">
                        <label>
                          模态名称
                          <input
                            value={modality.name}
                            maxLength={40}
                            onChange={(event) =>
                              update({
                                ...mapping,
                                modalities: mapping.modalities.map((item) =>
                                  item === modality
                                    ? { ...item, name: event.target.value }
                                    : item,
                                ),
                              })
                            }
                          />
                        </label>
                        <label>
                          序列处理
                          <SearchSelect
                            value={modality.pooling}
                            onChange={(event) =>
                              update({
                                ...mapping,
                                modalities: mapping.modalities.map((item) =>
                                  item === modality
                                    ? {
                                        ...item,
                                        pooling: event.target
                                          .value as typeof item.pooling,
                                      }
                                    : item,
                                ),
                              })
                            }
                          >
                            <option value="mean">时间／空间均值聚合</option>
                            <option value="max">时间／空间最大值聚合</option>
                            <option value="flatten">保留并展开所有位置</option>
                          </SearchSelect>
                        </label>
                        <label>
                          输出维度
                          <SearchSelect
                            value={modality.dimensions}
                            onChange={(event) =>
                              update({
                                ...mapping,
                                modalities: mapping.modalities.map((item) =>
                                  item === modality
                                    ? {
                                        ...item,
                                        dimensions: Number(
                                          event.target.value,
                                        ) as typeof item.dimensions,
                                      }
                                    : item,
                                ),
                              })
                            }
                          >
                            <option value={0}>保留原维度</option>
                            <option value={16}>最多 16 维固定投影</option>
                            <option value={32}>最多 32 维固定投影</option>
                            <option value={64}>最多 64 维固定投影</option>
                          </SearchSelect>
                        </label>
                        {field.shape.length > 2 && (
                          <label>
                            有效序列长度
                            <SearchSelect
                              value={key(modality.lengths ?? [])}
                              onChange={(event) =>
                                update({
                                  ...mapping,
                                  modalities: mapping.modalities.map((item) =>
                                    item === modality
                                      ? {
                                          ...item,
                                          lengths:
                                            event.target.value === "[]"
                                              ? null
                                              : JSON.parse(event.target.value),
                                        }
                                      : item,
                                  ),
                                })
                              }
                            >
                              <option value="[]">使用全部位置（含填充）</option>
                              {group?.fields
                                .filter(
                                  (candidate) =>
                                    candidate.numeric &&
                                    candidate.scalar &&
                                    candidate.rows === field.rows &&
                                    key(candidate.path) !== key(mapping.label),
                                )
                                .map((candidate) => (
                                  <option
                                    key={key(candidate.path)}
                                    value={key(candidate.path)}
                                  >
                                    {candidate.name}
                                  </option>
                                ))}
                            </SearchSelect>
                          </label>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
          </div>
        </fieldset>
        <p className="rw-muted">
          预计生成 {outputDimensions}{" "}
          个特征。多位置数组先聚合，再按所选维度固定投影（种子
          42，不根据标签拟合）；原 PKL 完整保留。现有机器学习每次最多选择 128
          个特征。
        </p>
        <p className="rw-muted">
          只导入当前节点，不拼接其他训练／验证／测试节点。后续机器学习在此节点内重新划分，属于子集内部评估。
        </p>
        {!valid && (
          <p className="rw-muted">请选择标签和至少一个样本数一致的数值模态。</p>
        )}
        {outputDimensions > 498 && (
          <p className="rw-error">超过 500 列导入上限，请减小输出维度。</p>
        )}
      </details>
    </div>
  );
}
