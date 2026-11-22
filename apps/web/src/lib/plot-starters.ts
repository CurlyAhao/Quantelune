export const plotStarters = {
  trend: ["时间趋势", "Time trend"],
  hist: ["分布直方图", "Histogram"],
  scatter: ["散点图", "Scatter plot"],
  box: ["箱线与原始观测", "Box plot and observations"],
  violin: ["小提琴与原始观测", "Violin plot and observations"],
  mean: ["均值与区间", "Mean and interval"],
  heatmap: ["相关性热图", "Correlation heatmap"],
  dashboard: ["多面板组合", "Multiple panels"],
} as const;
export type PlotStarterKind = keyof typeof plotStarters;
export type PlotStarterSelection = {
  kind: PlotStarterKind;
  x: string;
  y: string;
  group: string;
  numeric: string[];
};

export function plotStarter(selection: PlotStarterSelection, locale: string) {
  const { kind, x, y, group, numeric } = selection;
  const zh = locale === "zh-CN";
  const q = JSON.stringify;
  const paired = kind === "scatter" || kind === "dashboard";
  if (
    (kind === "heatmap" && numeric.length < 2) ||
    (kind === "trend" && !numeric.some((name) => name !== x)) ||
    (kind !== "heatmap" && !x) ||
    (paired && (!y || x === y))
  )
    throw new Error(
      zh ? "请选择需要的数值字段。" : "Choose the required numeric columns.",
    );
  const columns = [
    ...new Set(
      kind === "trend"
        ? [x, ...numeric.filter((name) => name !== x)]
        : kind === "heatmap"
        ? numeric
        : [x, ...(paired ? [y] : []), ...(group ? [group] : [])],
    ),
  ];
  const label = plotStarters[kind][zh ? 0 : 1];
  const subject = kind === "heatmap" ? numeric.join("、") : kind === "trend" ? numeric.filter(name => name !== x).join("、") : paired ? `${x} × ${y}` : x;
  const title = [label, subject, group ? (zh ? `按 ${group} 分组` : `grouped by ${group}`) : ""].filter(Boolean).join(" · ");
  const imports =
    "import matplotlib.pyplot as plt\nimport seaborn as sns\nimport numpy as np\n\n";
  if (kind === "trend") {
    const values = [...new Set(numeric.filter((name) => name !== x))];
    const colors = ["#315e50", "#a45e43", "#527b9c", "#8e6da6", "#a88a3b"];
    let code = imports + `plot_data = df[${q(columns)}].dropna(subset=[${q(x)}]).sort_values(${q(x)})\nassert len(plot_data) > 0, ${q(zh ? "时间字段没有有效观测。" : "No valid time observations.")}\n`;
    code += `fig, axes = plt.subplots(${values.length}, 1, sharex=True, squeeze=False, figsize=(8, ${Math.max(3, values.length * 2.6)}), layout="constrained")\n`;
    values.forEach((name, index) => {
      code += `axes[${index}, 0].plot(plot_data[${q(x)}], plot_data[${q(name)}], color=${q(colors[index % colors.length])}, linewidth=1.7)\n`;
      code += `axes[${index}, 0].set_ylabel(${q(name)})\naxes[${index}, 0].grid(True, alpha=0.2)\n`;
    });
    code += `axes[${values.length - 1}, 0].set_xlabel(${q(x)})\naxes[0, 0].set_title(${q(title)})\n`;
    return { title, code, columns };
  }
  const selected = `plot_data = df[${q(columns)}]${kind === "heatmap" ? "" : ".dropna()"}\nassert len(plot_data) > 0, ${q(zh ? "所选字段没有完整观测。" : "No complete observations in the selected columns.")}\n`;
  const groupX = group ? `x=${q(group)}, ` : "";
  const hue = group ? `hue=${q(group)}, ` : "";
  function correlation(data: string) {
    return `matrix_data = ${data}\nmatrix_data = matrix_data.loc[:, matrix_data.nunique() > 1]\nassert len(matrix_data.columns) >= 2, ${q(zh ? "至少需要两列有变化的数值。" : "At least two varying numeric columns are required.")}\nvalues = matrix_data.to_numpy()\nvalid = np.isfinite(values) | np.isnan(values)\nassert valid.sum() == valid.size, ${q(zh ? "数值中包含无穷大，请先核对数据。" : "Values contain infinity. Check the data first.")}\nscaled = matrix_data / matrix_data.abs().max()\ncorrelation = scaled.corr(min_periods=2)\nassert np.isfinite(correlation.to_numpy()).sum() > len(correlation), ${q(zh ? "没有足够的成对观测计算相关性。" : "Not enough paired observations for correlation.")}\n`;
  }
  let code = imports + selected;
  if (kind === "heatmap") code += correlation("plot_data");
  if (kind === "dashboard") code += correlation(`plot_data[${q([x, y])}]`);
  code +=
    kind === "dashboard"
      ? 'fig, axes = plt.subplots(2, 2, figsize=(10, 8), layout="constrained")\n'
      : 'fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")\n';
  if (kind === "hist")
    code += `sns.histplot(data=plot_data, x=${q(x)}, ${hue}ax=ax)\n`;
  if (kind === "scatter")
    code += `sns.scatterplot(data=plot_data, x=${q(x)}, y=${q(y)}, ${hue}ax=ax)\n`;
  if (kind === "box" || kind === "violin") {
    code += `sns.${kind === "box" ? "boxplot" : "violinplot"}(data=plot_data, ${groupX}y=${q(x)}, ax=ax)\n`;
    code += `sns.stripplot(data=plot_data, ${groupX}y=${q(x)}, jitter=False, alpha=0.45, size=3, color="0.25", ax=ax)\n`;
  }
  if (kind === "mean")
    code += `sns.pointplot(data=plot_data, ${groupX}y=${q(x)}, errorbar=("ci", 95), seed=0, ax=ax)\n`;
  if (kind === "heatmap")
    code +=
      'sns.heatmap(correlation, vmin=-1, vmax=1, center=0, annot=True, cmap="vlag", ax=ax)\n';
  if (kind === "dashboard") {
    code += `sns.histplot(data=plot_data, x=${q(x)}, ${hue}ax=axes[0, 0])\n`;
    code += `sns.scatterplot(data=plot_data, x=${q(x)}, y=${q(y)}, ${hue}ax=axes[0, 1])\n`;
    code += `sns.boxplot(data=plot_data, ${groupX}y=${q(x)}, ax=axes[1, 0])\n`;
    code +=
      'sns.heatmap(correlation, vmin=-1, vmax=1, center=0, annot=True, cmap="vlag", ax=axes[1, 1])\n';
    code += `fig.suptitle(${q(title)} + f" · n={len(plot_data)}")\n`;
  } else if (kind === "heatmap") code += `ax.set_title(${q(title)})\n`;
  else code += `ax.set_title(${q(title)} + f" · n={len(plot_data)}")\n`;
  return { title, code, columns };
}
