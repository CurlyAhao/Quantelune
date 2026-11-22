import type { AppLocale } from "./i18n";
import { createAppTranslator } from "./i18n-messages";
import { formatLocaleNumber } from "./i18n-core";
import type { StatisticalRun } from "./types";
import type { FigureData, FigureSpec, WorkspaceRecord } from "./workspace-api";
import { object } from "./research-results";

export const escapeMarkup = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const finite = (v: unknown): number => {
  if (typeof v !== "number" || !Number.isFinite(v))
    throw new Error("保存的图形值缺失或非有限。");
  return v;
};
function wrap(value: string, width: number, font: number): string[] {
  const lines: string[] = [];
  let line = "",
    used = 0;
  for (const char of value) {
    const size = font * (char.charCodeAt(0) > 255 ? 1 : 0.58);
    if (used + size > width && line) {
      lines.push(line);
      line = "";
      used = 0;
    }
    line += char;
    used += size;
  }
  if (line) lines.push(line);
  return lines;
}
export function assertFigureSource(
  spec: FigureSpec,
  run: StatisticalRun,
  snapshot?: WorkspaceRecord<FigureData> | null,
) {
  if (
    spec.schema_version !== 2 ||
    spec.renderer_version !== "workspace-figure-v2" ||
    !snapshot ||
    snapshot.id !== spec.snapshot_id ||
    snapshot.payload.kind !== "figure_data" ||
    snapshot.project_id !== run.project_id ||
    snapshot.payload.content_sha256 !== spec.content_sha256 ||
    snapshot.payload.chart_type !== spec.chart_type ||
    !{
      distribution: ["descriptive"],
      correlation_scatter: ["pearson", "spearman"],
      ols_coefficients: ["linear_regression"],
      welch_groups_difference: [],
    }[spec.chart_type].includes(run.method) ||
    JSON.stringify(snapshot.payload.fields) !== JSON.stringify(spec.fields) ||
    run.id !== spec.scope.statistical_run_id ||
    run.dataset_id !== spec.scope.dataset_id ||
    run.dataset_sha256 !== spec.scope.dataset_sha256 ||
    run.project_id !== spec.scope.project_id ||
    snapshot.payload.scope.statistical_run_id !== run.id ||
    snapshot.payload.scope.project_id !== run.project_id ||
    snapshot.payload.scope.dataset_id !== run.dataset_id ||
    snapshot.payload.scope.dataset_sha256 !== run.dataset_sha256 ||
    snapshot.payload.scope.schema_sha256 !== spec.scope.schema_sha256 ||
    run.status !== "completed" ||
    !run.result ||
    run.result.status !== "completed"
  )
    throw new Error("科研图形来源失效，请从保存的 Run 重新打开。");
  return snapshot.payload;
}

/** Only deterministic geometry; all numerical summaries and intervals are saved upstream. */
export function researchFigureSvg(
  spec: FigureSpec,
  run: StatisticalRun,
  snapshot?: WorkspaceRecord<FigureData> | null,
  options: { omitOutliers?: boolean; locale?: AppLocale } = {},
): string {
  const locale = options.locale ?? "zh-CN",
    t = createAppTranslator(locale);
  const pretty = (value: number) =>
    formatLocaleNumber(value, locale, spec.tick_digits == null ? { maximumSignificantDigits: 4 } : {minimumFractionDigits:spec.tick_digits,maximumFractionDigits:spec.tick_digits});
  const saved = assertFigureSource(spec, run, snapshot),
    d = saved.data;
  const w = spec.width,
    h = spec.height,
    f = (spec.font_size * spec.dpi) / 72;
  const small = Math.max((8 * spec.dpi) / 72, f * 0.8);
  if (f < 8 || f > h / 12 || w < 600 || h < 400)
    throw new Error("图面无法容纳字号。请增加像素尺寸或降低字号/DPI。");
  const ink = "#172337",
    muted = "#526078",
    accent = /^#[0-9a-f]{6}$/i.test(spec.accent_color || "") ? spec.accent_color! : spec.theme === "paper" ? "#335f72" : "#267361";
  const title = wrap(spec.title, w - f * 3, f * 1.1),
    caption = wrap(spec.caption, w - f * 3, small);
  if (title.length > 2 || caption.length > 3)
    throw new Error("标题或图注过长；请缩短文字或增加图面，避免裁切。");
  const text = (
    x: number,
    y: number,
    value: unknown,
    size = f,
    anchor = "start",
    color = ink,
  ) =>
    `<text x="${x}" y="${y}" font-size="${size}" text-anchor="${anchor}" fill="${color}">${escapeMarkup(value)}</text>`;
  const line = (
    x1: number,
    y1: number,
    x2: number,
    y2: number,
    color = muted,
    dash = "",
  ) =>
    `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${Math.max(1.25, f / 20)*(spec.line_weight ?? 1)}"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
  const circle = (x: number, y: number, radius = f * 0.09) =>
    `<circle cx="${x}" cy="${y}" r="${radius}" fill="${accent}" fill-opacity="0.72"/>`;
  const range = (values: number[]): [number, number] => {
    const min = Math.min(...values),
      max = Math.max(...values),
      pad = (max - min || Math.abs(min) * 0.1 || 1) * 0.07;
    return [min - pad, max + pad];
  };
  let left = f * 2.5,
    right = w - f * 1.5 * (spec.margin_scale ?? 1);
  function sampleNote(): string {
    if (spec.chart_type === "distribution") {
      if (Array.isArray(d.groups)) return locale === "zh-CN"
        ? `${d.groups.length} 组 · 有效观测 ${saved.n}`
        : `${d.groups.length} groups · n = ${saved.n}`;
      const histogram = object(d.histogram), box = object(d.box);
      return t("research.figure.distribution", { n: saved.n, bins: String(histogram.bins),
        displayed: options.omitOutliers ? 0 : String(box.outliers_displayed), total: String(box.outlier_count) });
    }
    if (spec.chart_type === "correlation_scatter") return t("research.figure.scatter", {
      displayed: saved.displayed_n, total: saved.n, seed: saved.seed, method: run.method });
    return t("research.figure.ols", { n: saved.n, interval: spec.layers.includes("coefficient_ci")
      ? t("research.figure.savedCi", {level: finite(d.confidence_level) * 100})
      : t("research.figure.pointsOnly") });
  }
  const footer = sampleNote(), footerLines = wrap(footer, w - f * 3, small);
  const top = f * (title.length * 1.25 + 2.05),
    bottom = h - f * (caption.length * 0.95 + Math.max(0, footerLines.length - 1) + 3.2);
  if (right - left < f * 8 || bottom - top < f * 4)
    throw new Error("图面空间不足，无法无裁切导出。");
  let content = title
    .map((value, i) => text(f * 1.5, f * (1.6 + i * 1.25), value, f * 1.1))
    .join("");
  const xAxis = (lo: number, hi: number, y: number, label: string) => {
    const scale = (value: number) =>
      left + ((value - lo) / (hi - lo)) * (right - left);
    content += line(left, y, right, y);
    const axisDigits = Math.min(12, Math.max(4, Math.ceil(-Math.log10((hi-lo) / Math.max(Math.abs(lo), Math.abs(hi), Number.MIN_VALUE))) + 2));
    const tickText = (value:number) => spec.tick_digits == null
      ? formatLocaleNumber(value, locale, {maximumSignificantDigits:axisDigits}) : pretty(value);
    const labelWidth = Math.max(...[lo, (lo+hi)/2, hi].map(value=>tickText(value).length)) * small * .62;
    const intervals = Math.max(1, Math.min(4, Math.floor((right-left) / (labelWidth * 1.5 + small))));
    for (let i = 0; i <= intervals; i++) {
      const value = lo + ((hi - lo) * i) / intervals,
        x = scale(value);
      content +=
        line(x, y, x, y + f * 0.12) +
        text(x, y + small, tickText(value), small, i === 0 ? "start" : i === intervals ? "end" : "middle");
    }
    const labels = wrap(spec.axis_label || label, right - left, small);
    if (labels.length > 1)
      throw new Error("轴名称过长，请增加宽度或缩短单位文字。");
    content += text((left + right) / 2, y + f * 1.55, spec.axis_label || label, small, "middle");
    return scale;
  };
  if (spec.chart_type === "distribution" && Array.isArray(d.groups)) {
    const groups = d.groups.map(object);
    if (!groups.length || groups.reduce((n, g) => n + finite(g.n), 0) !== saved.n)
      throw new Error("保存的分组样本数与统计结果不符，请重新生成图形。");
    left = Math.max(w * 0.32, f * 7.8);
    const rowHeight = (bottom - top) / groups.length;
    if (rowHeight < small * 1.65) throw new Error("分组较多，请增加图表高度或减小字号。");
    const values = groups.filter(g => finite(g.n) > 0).flatMap(g => {
      const box = object(g.box);
      return [finite(box.lower_whisker), finite(box.upper_whisker), ...(options.omitOutliers ? [] : (box.outliers as unknown[]).map(finite))];
    });
    const [lo, hi] = range(values);
    const scale = xAxis(lo, hi, bottom, `${spec.fields[0]}${spec.unit ? ` (${spec.unit})` : ""}`);
    content += text(left - f * .35, top - small * .4, String(d.group_variable), small, "end");
    groups.forEach((g, i) => {
      const y = top + rowHeight * (i + .5), n = finite(g.n);
      const label = `${g.label} (n=${n})`;
      if (wrap(label, left - f * 1.7, small).length > 1) throw new Error("分组名称较长，请增加图表宽度或减小字号。");
      content += text(left - f * .35, y + small * .32, label, small, "end");
      if (!n) { content += text(left + f * .2, y + small * .32, "无有效值", small); return; }
      const box = object(g.box), bh = Math.min(rowHeight * .45, f * .7);
      const q1 = scale(finite(box.q1)), q3 = scale(finite(box.q3)), median = scale(finite(box.median));
      const lower = scale(finite(box.lower_whisker)), upper = scale(finite(box.upper_whisker));
      content += line(lower, y, upper, y, accent) + line(lower, y - bh / 2, lower, y + bh / 2, accent) + line(upper, y - bh / 2, upper, y + bh / 2, accent);
      content += `<rect x="${q1}" y="${y - bh / 2}" width="${Math.max(1, q3 - q1)}" height="${bh}" fill="white" stroke="${accent}" stroke-width="2"/>` + line(median, y - bh / 2, median, y + bh / 2, accent);
      if (!options.omitOutliers) for (const v of box.outliers as unknown[]) content += circle(scale(finite(v)), y, f * .055);
    });

  } else if (spec.chart_type === "distribution") {
    const histogram = object(d.histogram),
      box = object(d.box);
    const edges = (histogram.edges as unknown[]).map(finite),
      counts = (histogram.counts as unknown[]).map(finite);
    if (
      edges.length !== counts.length + 1 ||
      !counts.length ||
      counts.reduce((a, b) => a + b, 0) !== saved.n
    )
      throw new Error("完整分布计数不符。");
    const hasHist = spec.layers.includes("histogram"),
      hasBox = spec.layers.includes("box");
    const histBottom = hasBox && hasHist ? bottom - f * 2.5 : bottom;
    const scale = xAxis(
      edges[0],
      edges.at(-1)!,
      bottom,
      `${spec.fields[0]}${spec.unit ? ` (${spec.unit})` : ""}`,
    );
    if (hasHist) {
      const maxCount = Math.max(...counts, 1),
        sy = (n: number) => histBottom - (n / maxCount) * (histBottom - top);
      for (let i = 0; i < counts.length; i++)
        content += `<rect x="${scale(edges[i])}" y="${sy(counts[i])}" width="${Math.max(0.5, scale(edges[i + 1]) - scale(edges[i]) - 1)}" height="${histBottom - sy(counts[i])}" fill="${accent}" fill-opacity="0.78"/>`;
      content +=
        line(left, top, left, histBottom) +
        text(left - f * 0.3, top + f * 0.2, maxCount, small, "end") +
        text(left - f * 0.3, histBottom, "0", small, "end") +
        text(left, top - f * 0.3, t("research.figure.frequency"), small);
    }
    if (hasBox) {
      const y = hasHist ? bottom - f : (top + bottom) / 2,
        bh = f * 0.7;
      const q1 = scale(finite(box.q1)),
        q3 = scale(finite(box.q3)),
        median = scale(finite(box.median));
      const lo = scale(finite(box.lower_whisker)),
        hi = scale(finite(box.upper_whisker));
      content +=
        line(lo, y, hi, y, accent) +
        line(lo, y - bh / 2, lo, y + bh / 2, accent) +
        line(hi, y - bh / 2, hi, y + bh / 2, accent);
      content +=
        `<rect x="${q1}" y="${y - bh / 2}" width="${Math.max(1, q3 - q1)}" height="${bh}" fill="white" stroke="${accent}" stroke-width="2"/>` +
        line(median, y - bh / 2, median, y + bh / 2, accent);
      if (!options.omitOutliers)
        for (const value of box.outliers as unknown[])
          content += circle(scale(finite(value)), y, f * 0.055);
    }

  } else if (spec.chart_type === "correlation_scatter") {
    const points = (d.points as unknown[]).map(object);
    if (
      !points.length ||
      points.length > 2000 ||
      points.length !== saved.displayed_n
    )
      throw new Error("保存散点数量不符。");
    const xs = points.map((p) => finite(p.x)),
      ys = points.map((p) => finite(p.y)),
      [lo, hi] = range(xs),
      [yl, yh] = range(ys);
    const scale = xAxis(
        lo,
        hi,
        bottom,
        `${spec.fields[0]}${spec.unit ? ` (${spec.unit})` : ""}`,
      ),
      sy = (v: number) => bottom - ((v - yl) / (yh - yl)) * (bottom - top);
    const yLabels = wrap(spec.fields[1], right - left, small);
    if (yLabels.length > 1)
      throw new Error("纵轴名称过长，请增加图面或使用简洁字段名。");
    content +=
      text(left, top - f * 0.4, spec.fields[1], small) +
      line(left, top, left, bottom);
    for (let i = 0; i <= 4; i++) {
      const value = yl + ((yh - yl) * i) / 4;
      content += text(
        left - f * 0.3,
        sy(value) + f * 0.22,
        pretty(value),
        small,
        "end",
      );
    }
    points.forEach((_, i) => {
      content += circle(scale(xs[i]), sy(ys[i]));
    });

  } else if (spec.chart_type === "ols_coefficients") {
    const rows = (d.coefficients as unknown[]).map(object),
      withCI = spec.layers.includes("coefficient_ci");
    if (!rows.length || (withCI && !d.ci_available))
      throw new Error("原记录未返回完整系数区间。");
    left = Math.max(w * 0.32, f * 4);
    right = w - f * 1.5;
    const rowHeight = (bottom - top) / rows.length;
    if (rowHeight < f * 1.6)
      throw new Error("系数项过多，需增加高度或降低字号以避免裁切。");
    const values = rows.flatMap((row) =>
      withCI
        ? [finite(row.coefficient), finite(row.ci_lower), finite(row.ci_upper)]
        : [finite(row.coefficient)],
    );
    const [lo, hi] = range([0, ...values]),
      scale = xAxis(
        lo,
        hi,
        bottom,
        `${t("research.figure.coefficient")}${spec.unit ? ` (${spec.unit})` : ` · ${t("research.figure.originalScale")}`}`,
      );
    content += line(scale(0), top, scale(0), bottom, muted, "5 5");
    rows.forEach((row, i) => {
      const y = top + rowHeight * (i + 0.5),
        label = wrap(String(row.term), left - f * 1.8, small);
      if (label.length > 2)
        throw new Error("系数项名称过长，请增加宽度或降低字号。");
      label.forEach((value, j) => {
        content += text(
          left - f * 0.35,
          y + (j - (label.length - 1) / 2) * f * 0.9,
          value,
          small,
          "end",
        );
      });
      if (withCI)
        content += line(
          scale(finite(row.ci_lower)),
          y,
          scale(finite(row.ci_upper)),
          y,
          accent,
        );
      content += circle(scale(finite(row.coefficient)), y);
    });

  } else throw new Error("未知科研图形 renderer。");
  footerLines.forEach((label, i) => {
    content += text(f * 1.5, h - f * (caption.length * 0.95 + footerLines.length - 1 - i + 0.7),
      label, small, "start", muted);
  });
  caption.forEach((value, i) => {
    content += text(
      f * 1.5,
      h - f * ((caption.length - i - 1) * 0.95 + 0.4),
      value,
      small,
    );
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${(w / spec.dpi) * 25.4}mm" height="${(h / spec.dpi) * 25.4}mm" viewBox="0 0 ${w} ${h}" role="img" aria-label="${escapeMarkup(spec.title)}" font-family="Arial, 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif"><title>${escapeMarkup(spec.title)}</title><desc>${escapeMarkup(footer)}</desc><rect width="${w}" height="${h}" fill="white"/>${content}</svg>`;
}
