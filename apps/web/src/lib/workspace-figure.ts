import type { AppLocale } from "./i18n";
import { createAppTranslator } from "./i18n-messages";
import type { StatisticalRun } from "./types";
import type { FigureData, FigureSpec, WorkspaceRecord } from "./workspace-api";
import { researchFigureSvg } from "./research-figure";

const escape = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
      })[c]!,
  );
const number = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** Geometry uses saved aggregates only. Mean points have no invented intervals. */
export function figureSvg(
  spec: FigureSpec,
  run: StatisticalRun,
  snapshot?: WorkspaceRecord<FigureData> | null,
  locale: AppLocale = "zh-CN",
): string {
  const t = createAppTranslator(locale);
  const pretty = (value: number) =>
    value.toLocaleString(locale, { maximumSignificantDigits: 6 });
  if (spec.schema_version === 2)
    return researchFigureSvg(spec, run, snapshot, { locale });
  if (
    run.id !== spec.scope.statistical_run_id ||
    run.dataset_id !== spec.scope.dataset_id ||
    run.dataset_sha256 !== spec.scope.dataset_sha256 ||
    run.project_id !== spec.scope.project_id ||
    run.method !== "welch_t" ||
    run.status !== "completed" ||
    !run.result
  )
    throw new Error("Figure source unavailable.");
  const result = run.result;
  const groups = spec.group_order.map((label) => {
    const data = result.descriptive_statistics[label] as
      Record<string, unknown> | undefined;
    const mean = number(data?.mean),
      sd = number(data?.std),
      n = number(data?.n);
    if (mean === null || sd === null || n === null)
      throw new Error("Saved group summary unavailable.");
    return { label, mean, sd, n };
  });
  if (
    groups.length !== 2 ||
    Object.keys(result.descriptive_statistics).join("\0") !==
      spec.group_order.join("\0")
  )
    throw new Error("Group order changed.");
  const ci = result.confidence_interval ?? {};
  const difference = number(result.estimates.mean_difference);
  const lower = number(ci.lower),
    upper = number(ci.upper);
  const hasCI =
    ci.schema_version === 1 &&
    ci.estimand === "mean_difference" &&
    ci.direction === "group_1_minus_group_2" &&
    JSON.stringify(ci.group_order) === JSON.stringify(spec.group_order) &&
    difference !== null &&
    (lower !== null || ci.lower_unbounded === true) &&
    (upper !== null || ci.upper_unbounded === true);
  if (spec.layers.includes("difference_ci") && !hasCI)
    throw new Error("This saved Run has no mean-difference CI.");
  const w = spec.width,
    h = spec.height,
    f = spec.font_size;
  const left = w * 0.24,
    right = w * 0.9,
    span = right - left;
  const ink = "#172337",
    secondary = "#526078",
    accent = spec.theme === "paper" ? "#172337" : "#245bd6";
  const text = (
    x: number,
    y: number,
    content: unknown,
    size = f,
    anchor = "start",
  ) =>
    `<text x="${x}" y="${y}" fill="${ink}" font-size="${size}" text-anchor="${anchor}">${escape(content)}</text>`;
  const line = (
    x1: number,
    y1: number,
    x2: number,
    y2: number,
    color = secondary,
    dash = "",
  ) =>
    `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="2"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
  const circle = (x: number, y: number) =>
    `<circle cx="${x}" cy="${y}" r="6" fill="${accent}"/>`;
  const wrap = (content: string, max: number) => {
    const chars = Array.from(content),
      out = [];
    for (let i = 0; i < chars.length; i += max)
      out.push(chars.slice(i, i + max).join(""));
    return out;
  };
  const heading = wrap(spec.title, Math.max(20, Math.floor(w / (f * 0.8))));
  if (heading.length > 3 || heading.length * f * 1.3 > h * 0.14)
    throw new Error("标题超出图形空间，请增大尺寸或缩短标题。");
  let body = heading
    .map((t, i) => text(w * 0.06, h * 0.075 + i * f * 1.3, t, f * 1.3))
    .join("");
  if (spec.layers.includes("group_means")) {
    const a = Math.min(...groups.map((g) => g.mean)),
      b = Math.max(...groups.map((g) => g.mean));
    const pad = Math.max(
      (b - a) * 0.2,
      Math.max(Math.abs(a), Math.abs(b), 1) * 0.1,
    );
    const min = a - pad,
      max = b + pad,
      x = (v: number) => left + ((v - min) / (max - min)) * span;
    body += text(
      w * 0.06,
      h * 0.22,
      `组均值${spec.unit ? ` (${spec.unit})` : ""} · 点无区间`,
    );
    body += line(left, h * 0.43, right, h * 0.43);
    [min, (min + max) / 2, max].forEach((v) => {
      body += text(x(v), h * 0.43 + f * 1.5, pretty(v), f * 0.9, "middle");
    });
    groups.forEach((g, i) => {
      const y = h * (0.3 + i * 0.09);
      const rows = wrap(
        g.label,
        Math.max(4, Math.floor((left - w * 0.06) / f)),
      );
      body += rows
        .slice(0, 2)
        .map((t, j) => (j === 1 && rows.length > 2 ? t.slice(0, -1) + "…" : t))
        .map((t, j) => text(left - f, y + j * f * 1.1, t, f, "end"))
        .join("");
      body +=
        circle(x(g.mean), y) +
        text(x(g.mean), y - 12, pretty(g.mean), f, "middle");
    });
  }
  if (spec.layers.includes("difference_ci") && hasCI) {
    const bounds = [
      0,
      difference!,
      ...(lower === null ? [] : [lower]),
      ...(upper === null ? [] : [upper]),
    ];
    const a = Math.min(...bounds),
      b = Math.max(...bounds),
      pad = Math.max((b - a) * 0.15, 0.1);
    const min = a - pad,
      max = b + pad,
      x = (v: number) => left + ((v - min) / (max - min)) * span;
    const y = h * 0.66;
    body += text(
      w * 0.06,
      h * 0.56,
      t("research.figure.legacyInterval", {
        level: Number(ci.confidence_level) * 100,
        alternative: String(ci.alternative),
      }),
    );
    body += text(w * 0.06, h * 0.59, "组 1 − 组 2（按保存顺序）", f * 0.9);
    body += line(x(0), y - 25, x(0), y + 25, secondary, "4 4");
    body += line(
      lower === null ? left : x(lower),
      y,
      upper === null ? right : x(upper),
      y,
      accent,
    );
    body += circle(x(difference!), y);
    if (lower === null)
      body += `<path d="M${left + 10},${y - 6} L${left},${y} L${left + 10},${y + 6}" fill="none" stroke="${accent}" stroke-width="2"/>`;
    if (upper === null)
      body += `<path d="M${right - 10},${y - 6} L${right},${y} L${right - 10},${y + 6}" fill="none" stroke="${accent}" stroke-width="2"/>`;
    body += text(
      left,
      y + f * 1.8,
      t("research.figure.differenceValue", {
        lower: lower === null ? "−∞" : pretty(lower),
        upper: upper === null ? "+∞" : pretty(upper),
        difference: pretty(difference!),
      }),
    );
  }
  const annotation = groups
    .map((g) =>
      t("research.figure.groupSummary", {
        group: g.label,
        n: g.n,
        mean: pretty(g.mean),
        sd: pretty(g.sd),
      }),
    )
    .join("; ");
  const footer = wrap(
    annotation,
    Math.max(16, Math.floor((w * 0.88) / (f * 0.85))),
  );
  const captions = wrap(
    spec.caption,
    Math.max(16, Math.floor((w * 0.88) / (f * 0.85))),
  );
  if ((footer.length + captions.length) * f * 1.1 > h * 0.15)
    throw new Error(
      "分组名称或图注超出图形空间，请增大尺寸、减小字号或缩短图注。",
    );
  body += [...footer, ...captions]
    .map((t, i) => text(w * 0.06, h * 0.8 + i * f * 1.1, t, f * 0.85))
    .join("");
  body += text(
    w * 0.06,
    h * 0.97,
    `Welch · Run ${run.id} · ${spec.dpi} DPI (PNG)`,
    f * 0.7,
  );
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${escape(spec.title)}"><title>${escape(spec.title)}</title><desc>${escape(annotation)}. ${escape(spec.caption)}. ${escape(t("research.figure.legacyDescription"))}</desc><rect width="100%" height="100%" fill="white"/><g font-family="Segoe UI,Microsoft YaHei,Noto Sans CJK SC,sans-serif">${body}</g></svg>`;
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++)
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/** Canvas encodes exact pixels; pHYs makes the approved DPI explicit. */
export function withPngDpi(png: Uint8Array, dpi: number): Uint8Array {
  if (
    !Number.isInteger(dpi) ||
    dpi < 72 ||
    dpi > 600 ||
    png.length < 45 ||
    [137, 80, 78, 71, 13, 10, 26, 10].some((v, i) => png[i] !== v) ||
    new DataView(png.buffer, png.byteOffset + 8, 4).getUint32(0) !== 13 ||
    new TextDecoder().decode(png.subarray(12, 16)) !== "IHDR"
  )
    throw new Error("Invalid PNG or DPI.");
  const chunk = new Uint8Array(21),
    view = new DataView(chunk.buffer);
  view.setUint32(0, 9);
  chunk.set([112, 72, 89, 115], 4);
  const perMeter = Math.round(dpi / 0.0254);
  view.setUint32(8, perMeter);
  view.setUint32(12, perMeter);
  chunk[16] = 1;
  view.setUint32(17, crc32(chunk.subarray(4, 17)));
  // Remove encoder pHYs before inserting one authoritative density chunk.
  const segments = [png.slice(0, 33), chunk];
  let parsed = 33,
    ended = false;
  for (let offset = 33; offset + 12 <= png.length;) {
    const size =
      new DataView(png.buffer, png.byteOffset + offset, 4).getUint32(0) + 12;
    if (offset + size > png.length) throw new Error("Invalid PNG.");
    const type = new TextDecoder().decode(png.subarray(offset + 4, offset + 8));
    if (type === "IEND") {
      if (size !== 12 || offset + size !== png.length)
        throw new Error("Invalid PNG end.");
      ended = true;
    }
    if (type !== "pHYs") segments.push(png.slice(offset, offset + size));
    offset += size;
    parsed = offset;
  }
  if (!ended || parsed !== png.length) throw new Error("Invalid PNG end.");
  const output = new Uint8Array(segments.reduce((sum, s) => sum + s.length, 0));
  let offset = 0;
  segments.forEach((s) => {
    output.set(s, offset);
    offset += s.length;
  });
  return output;
}

export async function figureBlob(
  svg: string,
  spec: FigureSpec,
  format: "svg" | "png",
): Promise<Blob> {
  if (format === "svg")
    return new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  await document.fonts.ready;
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = reject;
      image.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = spec.width;
    canvas.height = spec.height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas unavailable.");
    context.drawImage(image, 0, 0, spec.width, spec.height);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error("PNG unavailable."))),
        "image/png",
      ),
    );
    return new Blob(
      [
        withPngDpi(
          new Uint8Array(await blob.arrayBuffer()),
          spec.dpi,
        ) as BlobPart,
      ],
      { type: "image/png" },
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}
