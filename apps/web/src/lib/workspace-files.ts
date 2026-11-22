import type { Dataset } from "./types";

export type WorkspaceInventory = {
  root: string | null;
  generated_ids?: string[];
  files: { path: string; name: string; size: number; dataset_id: string | null }[];
  folders: string[];
  sessions: { id: string; title: string; files: string[]; kind?:string; group_id?:string; created?:string }[];
};

export function datasetOrigin(data: Dataset): "workspace" | "added" | "generated" | "session" {
  if (data.media_case || (data.signal_session && /^(bidmc|physiocgm|hapt)$/.test(data.file_format))) return "session";
  if (data.raw_file && data.local_file) return "workspace";
  if (data.source_dataset_id || data.preparation_parent_id || data.visual_result || data.coding_summary || data.coding_matrix || data.coding_agreement || data.media_result) return "generated";
  return "added";
}

export function workspaceOutputCategory(data: Dataset, generatedIds: ReadonlySet<string> = new Set()): string | null {
  if (data.upload_status === "deleted") return null;
  if (data.media_case?.kind === "media" && !data.media_case.survey && (data.media_case.purpose === "temporary" || data.media_case.purpose === "case" || data.media_case.samples?.some(sample => sample.case_title || sample.case_deleted))) return null;
  if (data.media_case) return "材料组";
  if (data.transcription) return "转录与校对";
  if (data.preparation_operation === "join") return "关联表";
  if (data.preparation_parent_id) return "处理版本";
  if (data.media_result?.kind === "summary_csv") return "特征摘要";
  if (data.media_result?.kind === "arrays_npz") return "完整特征";
  if (data.media_result) return "派生音轨与视频帧";
  if (data.signal_session) return "信号数据";
  if (data.coding_agreement) return "编码比较";
  if (data.coding_summary || data.coding_matrix) return "主题汇总";
  if (data.visual_result) return "图像特征";
  if (data.source_dataset_id || generatedIds.has(data.id)) return "处理版本";
  return null;
}

export function datasetTitle(data: Dataset, datasets: Dataset[] = [], visited = new Set<string>()): string {
  if (data.media_case?.purpose === "case") {
    const names = data.media_case.samples?.filter(sample => !sample.case_deleted && sample.case_title).map(sample => sample.case_title);
    if (names?.length) return names.join("、");
  }
  if (data.media_case?.purpose === "temporary") return `一起分析 · ${data.media_case.samples?.length || 0} 份材料`;
  if (data.display_name) return data.display_name;
  if (visited.has(data.id)) return data.original_filename;
  visited.add(data.id);
  if (datasetOrigin(data) === "session") return data.original_filename.replace(/\.(bidmc|physiocgm|hapt)$/i, "") + " · 联合信号";
  const parent = datasets.find(row => row.id === (data.preparation_parent_id || data.source_dataset_id));
  if (parent && data.preparation_parent_id) return `${datasetTitle(parent, datasets, visited)} · 处理版本`;
  if (parent && /\.windows\.(csv|pkl)$/i.test(data.original_filename)) {
    const date = new Date(data.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    return `${data.file_format === "pkl" ? "分段 PKL" : "窗口摘要"} · ${date}`;
  }
  return data.original_filename;
}

export const soundMeasures:Record<string,string> = {
  mean_frame_rms:"平均短时RMS", whole_signal_rms:"整段RMS", pitch_mean_hz:"平均基频（Hz）",
  pitch_cv:"基频变异系数",pitch_range_hz:"基频极差（Hz）",pitch_valid_frames:"有效基频点数",
  intensity_energy_mean_db:"Praat 强度（dB，按能量平均）", speech_rate_per_second:"语速（计数/秒）",
  b1_mean_hz:"平均 B1 带宽（Hz）", b2_mean_hz:"平均 B2 带宽（Hz）", b3_mean_hz:"平均 B3 带宽（Hz）", b4_mean_hz:"平均 B4 带宽（Hz）",
  b1_valid_frames:"B1 有效帧", b2_valid_frames:"B2 有效帧", b3_valid_frames:"B3 有效帧", b4_valid_frames:"B4 有效帧", formant_total_frames:"共振峰总帧数",
  f1_mean_hz:"平均 F1（Hz）", f2_mean_hz:"平均 F2（Hz）", f3_mean_hz:"平均 F3（Hz）", f4_mean_hz:"平均 F4（Hz）",
  speech_count:"词数／字数", speech_count_unit:"计数单位", duration_seconds:"声音时长（秒）",
};
