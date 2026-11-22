"""Unit-aware programs for saved media feature summaries."""

import ast

ACOUSTIC_SUMMARY_FIELDS = {
    "mean_frame_rms": ("平均帧 RMS", "（线性幅度）", "amplitude"),
    "pitch_mean_hz": ("平均基频", "Hz", "frequency"),
    "pitch_cv": ("基频变异系数", "（无量纲）", "ratio"),
    "pitch_range_hz": ("基频极差", "Hz", "frequency"),
    "intensity_energy_mean_db": ("Praat 平均强度", "dB", "intensity"),
    "f1_mean_hz": ("第一共振峰均值", "Hz", "frequency"),
    "f2_mean_hz": ("第二共振峰均值", "Hz", "frequency"),
    "f3_mean_hz": ("第三共振峰均值", "Hz", "frequency"),
    "f4_mean_hz": ("第四共振峰均值", "Hz", "frequency"),
    "speech_rate_per_second": ("语速", "计数单位/秒", "speech_rate"),
}


def mixed_unit_bar_list(code: str) -> bool:
    """Catch the observed overview pattern: a mixed-unit list and a bar chart.

    Other code, including scatter and grouped comparisons, is left untouched.
    This is a narrow check, not a general semantic validator of plot programs.
    """
    tree = ast.parse(code)
    if not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"bar", "barh"}
        for node in ast.walk(tree)
    ):
        return False
    # Restrict the guard to the observed one-row overview idiom; leave
    # general grouped/multi-row plots for the author to specify.
    if not any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "iloc"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == 0
        for node in ast.walk(tree)
    ):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            names = {
                part.slice.value
                for part in ast.walk(node)
                if isinstance(part, ast.Subscript)
                and isinstance(part.slice, ast.Constant)
                and isinstance(part.slice.value, str)
                and part.slice.value in ACOUSTIC_SUMMARY_FIELDS
            }
            if len({ACOUSTIC_SUMMARY_FIELDS[name][2] for name in names}) > 1:
                return True
    return False


def acoustic_summary_program(columns: list[str], row_count: int | None, title: str) -> str:
    """Generate an editable plot without changing or scaling source values."""
    fields = [name for name in ACOUSTIC_SUMMARY_FIELDS if name in columns]
    if not fields:
        raise ValueError("No supported acoustic summary fields selected.")
    rows = (len(fields) + 1) // 2
    lines = [
        "import matplotlib.pyplot as plt",
        "import numpy as np",
        f"plt.figure(figsize=(10, {max(3, rows * (1.6 if row_count == 1 else 2.5))}))",
        f"plt.suptitle({title!r}, fontsize=15)",
    ]
    for index, name in enumerate(fields, 1):
        label, unit, _ = ACOUSTIC_SUMMARY_FIELDS[name]
        lines.append(f"ax = plt.subplot({rows}, 2, {index})")
        if name == "speech_rate_per_second" and "speech_count_unit" in columns:
            lines.extend(
                [
                    "unit = '计数单位/秒'",
                    "if df['speech_count_unit'].nunique() == 1:",
                    "    if df['speech_count_unit'].iloc[0] == 'english_words':",
                    "        unit = '英文词/秒'",
                    "    if df['speech_count_unit'].iloc[0] == 'chinese_characters':",
                    "        unit = '汉字/秒'",
                ]
            )
        else:
            lines.append(f"unit = {unit!r}")
        if row_count == 1:
            lines.extend(
                [
                    "ax.set_axis_off()",
                    f"value = float(df[{name!r}].iloc[0])",
                    f"ax.text(0.03, 0.75, {label!r}, fontsize=12, color='#39544c')",
                    "if np.isfinite(value):",
                    "    ax.text(0.03, 0.30, f'{value:.6g} {unit}', fontsize=17, color='#193f37')",
                    "else:",
                    "    ax.text(0.03, 0.30, '—', fontsize=14)",
                ]
            )
        else:
            lines.extend(
                [
                    f"ax.plot(df[{name!r}], marker='o', linestyle='none')",
                    f"ax.set_title({label!r})",
                    "ax.set_ylabel(unit)",
                    "ax.set_xlabel('样本（原行序）')",
                ]
            )
    lines.append("plt.tight_layout(rect=[0, 0, 1, 0.94])")
    return "\n".join(lines)
