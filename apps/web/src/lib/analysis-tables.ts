import type { Dataset } from "./types";

export type AnalysisTableVersion = {
  family: string;
  title: string;
  number: number;
  total: number;
  change: string;
  copies: Dataset[];
};

export function tableDimensions(data: Dataset) {
  return [data.row_count == null ? "" : data.row_count.toLocaleString("zh-CN") + " 行",
    data.column_count == null ? "" : data.column_count + " 列"].filter(Boolean).join(" · ");
}

export function versionTitle(version: AnalysisTableVersion) {
  return version.total === 1 ? version.title : `${version.title} · 版本 ${version.number}`;
}

function describeChange(data: Dataset, prior?: Dataset) {
  const details: string[] = [];
  if (data.preparation_change) details.push(data.preparation_change);
  if (data.sheet_name) details.push("工作表 " + data.sheet_name);
  if (prior) {
    if (data.csv_encoding && data.csv_encoding !== prior.csv_encoding) details.push("编码 " + data.csv_encoding);
    if (data.column_count != null && prior.column_count != null) {
      const n = data.column_count - prior.column_count;
      if (n) details.push(`${n > 0 ? "增加" : "减少"} ${Math.abs(n)} 列`);
    }
    if (data.row_count != null && prior.row_count != null) {
      const n = data.row_count - prior.row_count;
      if (n) details.push(`${n > 0 ? "增加" : "减少"} ${Math.abs(n)} 行`);
    }
    if (!details.length) details.push(data.prepared_source_sha256 !== prior.prepared_source_sha256 ? "原文件已更新" : "表格内容已更新");
  }
  return details.join(" · ");
}

/** Index every original ID so old task links still select their exact saved record. */
export function analysisTableVersions(datasets: Dataset[]): Map<string, AnalysisTableVersion> {
  const byId = new Map(datasets.map(d => [d.id, d]));
  const result = new Map<string, AnalysisTableVersion>();
  const families = new Map<string, Map<string, AnalysisTableVersion>>();
  const sorted = datasets.filter(d => d.upload_status === "ready")
    .sort((a,b) => a.created_at.localeCompare(b.created_at)
      || (a.preparation_steps ?? 0) - (b.preparation_steps ?? 0) || a.id.localeCompare(b.id));
  for (const data of sorted) {
    let prepared = data;
    const seen = new Set<string>();
    while (prepared.preparation_parent_id && prepared.preparation_operation !== "join" && !seen.has(prepared.id)) {
      seen.add(prepared.id);
      const parent = byId.get(prepared.preparation_parent_id);
      if (!parent) break;
      prepared = parent;
    }
    const source = byId.get(prepared.source_dataset_id ?? "");
    if (prepared.preparation_parent_id || !prepared.prepared_source_sha256 || !source?.raw_file) continue;
    const family = source.source_root_id || source.id;
    let versions = families.get(family);
    if (!versions) families.set(family, versions = new Map());
    // Workbook bytes cover all sheets. Distinguish the sheet and the actual CSV decoding.
    const key = JSON.stringify([prepared.prepared_source_sha256, prepared.sheet_name ?? null,
      prepared.csv_encoding ?? null, data.sha256 || data.id]);
    let v = versions.get(key);
    if (!v) {
      const prior = byId.get(data.preparation_parent_id || "") ?? Array.from(versions.values()).at(-1)?.copies[0];
      const title = (byId.get(family) ?? source).original_filename.replace(/\.[^.]+$/, "") + " · 分析表";
      v = {family, title, number: versions.size + 1, total: 0, change: describeChange(data, prior), copies: []};
      versions.set(key, v);
    }
    v.copies.unshift(data);
    result.set(data.id, v);
  }
  for (const family of families.values()) for (const version of family.values()) version.total = family.size;
  return result;
}

/** Prefer the selected or task-linked copy in selectors, newest otherwise. */
export function analysisTableChoices(datasets: Dataset[], versions: Map<string, AnalysisTableVersion>, preferred: string[] = []) {
  const included = new Set(datasets.map(data => data.id));
  const keep = new Map<AnalysisTableVersion, string>();
  for (const id of preferred) {
    const version = versions.get(id);
    if (version && included.has(id) && !keep.has(version)) keep.set(version, id);
  }
  return datasets.filter(data => {
    const version = versions.get(data.id);
    if (!version) return true;
    if (!keep.has(version)) keep.set(version, version.copies.find(copy => included.has(copy.id))!.id);
    return data.id === keep.get(version);
  });
}
