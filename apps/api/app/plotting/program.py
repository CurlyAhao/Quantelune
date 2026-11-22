"""Interpret plotting statements through registered APIs, never Python eval/exec.

Scientific imports and rendering belong in the existing OS-limited worker. This
module does not grant a caller access to files, imports, callbacks or processes.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import json
import math
import operator
import pickle
import warnings
from dataclasses import dataclass
from functools import wraps
from types import SimpleNamespace
from typing import Any


class PlotProgramError(ValueError):
    code = "PLOT_CODE_INVALID"

    def __init__(self, message: str, line: int | None = None):
        self.line = line
        super().__init__(f"第{line}行：{message}" if line else message)


@dataclass(frozen=True)
class Module:
    name: str


@dataclass(frozen=True)
class Call:
    owner: Any
    name: str


@dataclass(frozen=True)
class Indexer:
    owner: Any
    name: str


@dataclass(frozen=True)
class Rc:
    pass


MODULES = {
    "matplotlib",
    "matplotlib.pyplot",
    "seaborn",
    "numpy",
    "pandas",
    "math",
}
PYPLOT = {
    "subplots",
    "subplot_mosaic",
    "figure",
    "subplot",
    "plot",
    "scatter",
    "hist",
    "bar",
    "barh",
    "boxplot",
    "violinplot",
    "errorbar",
    "hexbin",
    "imshow",
    "contour",
    "contourf",
    "pcolormesh",
    "colorbar",
    "legend",
    "xlabel",
    "ylabel",
    "title",
    "suptitle",
    "xlim",
    "ylim",
    "xscale",
    "yscale",
    "xticks",
    "yticks",
    "grid",
    "tight_layout",
    "subplots_adjust",
    "axhline",
    "axvline",
    "fill_between",
    "fill_betweenx",
    "text",
    "annotate",
}
SEABORN = {
    "histplot",
    "kdeplot",
    "ecdfplot",
    "rugplot",
    "scatterplot",
    "lineplot",
    "regplot",
    "residplot",
    "boxplot",
    "violinplot",
    "boxenplot",
    "stripplot",
    "swarmplot",
    "pointplot",
    "barplot",
    "countplot",
    "heatmap",
    "clustermap",
    "relplot",
    "displot",
    "catplot",
    "lmplot",
    "jointplot",
    "pairplot",
    "FacetGrid",
    "PairGrid",
    "JointGrid",
    "despine",
    "move_legend",
    "color_palette",
    "light_palette",
    "dark_palette",
    "diverging_palette",
    "cubehelix_palette",
    "blend_palette",
}
NUMPY = {
    "array",
    "asarray",
    "arange",
    "linspace",
    "logspace",
    "zeros",
    "ones",
    "eye",
    "sqrt",
    "log",
    "log10",
    "exp",
    "abs",
    "clip",
    "where",
    "isfinite",
    "isnan",
    "mean",
    "median",
    "std",
    "var",
    "sum",
    "min",
    "max",
    "percentile",
    "quantile",
    "unique",
    "sort",
    "argsort",
    "meshgrid",
    "triu",
    "tril",
    "column_stack",
    "concatenate",
    "stack",
    "sin",
    "cos",
    "tan",
    "polyfit",
    "polyval",
    "histogram",
    "cumsum",
    "diff",
    "round",
}
PANDAS = {"DataFrame", "Series", "to_numeric", "to_datetime", "concat", "crosstab", "cut", "qcut"}
FRAME = {
    "dropna",
    "fillna",
    "drop",
    "rename",
    "select_dtypes",
    "sort_values",
    "groupby",
    "corr",
    "head",
    "tail",
    "nlargest",
    "nsmallest",
    "melt",
    "pivot",
    "pivot_table",
    "stack",
    "unstack",
    "reset_index",
    "set_index",
    "astype",
    "assign",
    "mean",
    "median",
    "sum",
    "min",
    "max",
    "std",
    "var",
    "count",
    "nunique",
    "quantile",
    "value_counts",
    "agg",
    "size",
    "to_numpy",
    "tolist",
    "round",
    "clip",
    "abs",
    "sort_index",
    "isin",
    "between",
}
AXES = PYPLOT - {
    "subplots",
    "subplot_mosaic",
    "figure",
    "subplot",
    "colorbar",
    "xlabel",
    "ylabel",
    "title",
    "suptitle",
    "xlim",
    "ylim",
    "xscale",
    "yscale",
    "xticks",
    "yticks",
    "tight_layout",
    "subplots_adjust",
} | {
    "set",
    "set_title",
    "set_xlabel",
    "set_ylabel",
    "set_xlim",
    "set_ylim",
    "set_xscale",
    "set_yscale",
    "set_xticks",
    "set_yticks",
    "set_xticklabels",
    "set_yticklabels",
    "set_aspect",
    "set_axis_off",
    "set_axis_on",
    "tick_params",
    "invert_xaxis",
    "invert_yaxis",
    "set_facecolor",
    "legend",
    "twinx",
    "twiny",
}
RC_KEYS = {
    "font.family",
    "font.sans-serif",
    "font.serif",
    "font.size",
    "axes.titlesize",
    "axes.labelsize",
    "axes.unicode_minus",
    "axes.spines.top",
    "axes.spines.right",
    "axes.facecolor",
    "axes.edgecolor",
    "axes.grid",
    "figure.facecolor",
    "figure.titlesize",
    "xtick.labelsize",
    "ytick.labelsize",
    "legend.fontsize",
    "grid.alpha",
    "grid.color",
    "lines.linewidth",
    "lines.markersize",
}
AGGREGATES = {
    "mean",
    "median",
    "sum",
    "min",
    "max",
    "std",
    "var",
    "count",
    "size",
    "nunique",
    "first",
    "last",
}
BUILTINS = {
    "len": len,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "str": str,
    "int": int,
    "float": float,
    "list": list,
    "tuple": tuple,
    "zip": zip,
    "enumerate": enumerate,
}
BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
}
CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def parse(code: str) -> ast.Module:
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > 32000:
        raise PlotProgramError("请填写绘图代码，最多32 KB。")
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        raise PlotProgramError("代码语法有误，请检查括号和缩进。", error.lineno) from None
    if sum(1 for _ in ast.walk(tree)) > 6000:
        raise PlotProgramError("代码过长，请拆成几幅图。")
    return tree


class Program:
    def __init__(self, frame):
        # This constructor is for a resource-controlled worker, never the API.
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
        import seaborn as sns
        from matplotlib.artist import Artist
        from matplotlib.axes import Axes
        from matplotlib.colorbar import Colorbar
        from matplotlib.figure import Figure
        from matplotlib.spines import Spine, Spines, SpinesProxy
        from pandas.core.groupby.generic import DataFrameGroupBy, SeriesGroupBy
        from seaborn.axisgrid import Grid

        self.modules = {
            "matplotlib": matplotlib,
            "matplotlib.pyplot": plt,
            "seaborn": sns,
            "numpy": np,
            "pandas": pd,
            "math": math,
        }
        self.frame_types = (pd.DataFrame, pd.Series, DataFrameGroupBy, SeriesGroupBy)
        self.axes_type, self.figure_type = Axes, Figure
        self.artist_type, self.colorbar_type = Artist, Colorbar
        self.spine_types, self.grid_type = (Spine, Spines, SpinesProxy), Grid
        self.env = {
            "df": frame.copy(deep=True),
            "plt": Module("matplotlib.pyplot"),
            "sns": Module("seaborn"),
            "np": Module("numpy"),
            "pd": Module("pandas"),
            **{key: Call("builtin", key) for key in BUILTINS},
            "range": Call("builtin", "range"),
        }
        self.steps = 0
        self.line = None

    def error(self, message):
        raise PlotProgramError(message, self.line)

    def member(self, value, name):
        if name.startswith("_"):
            self.error("请使用公开的绘图属性。")
        if isinstance(value, Module):
            if value.name == "matplotlib" and name == "pyplot":
                return Module("matplotlib.pyplot")
            if value.name in {"matplotlib", "matplotlib.pyplot"} and name == "rcParams":
                return Rc()
            allowed = {
                "matplotlib.pyplot": PYPLOT,
                "seaborn": SEABORN,
                "numpy": NUMPY,
                "pandas": PANDAS,
                "math": {"sqrt", "log", "log10", "exp", "sin", "cos", "tan", "ceil", "floor"},
            }
            if name in allowed.get(value.name, set()):
                return Call(value, name)
            if value.name in {"numpy", "math"} and name in {"pi", "e", "nan"}:
                return getattr(self.modules[value.name], name)
        if isinstance(value, Rc) and name == "update":
            return Call(value, name)
        if isinstance(value, self.frame_types):
            if name in FRAME:
                return Call(value, name)
            if name in {"loc", "iloc"} and hasattr(value, name):
                return Indexer(value, name)
            if name in {"columns", "shape", "T", "name"} and hasattr(value, name):
                return getattr(value, name)
        np = self.modules["numpy"]
        if isinstance(value, np.ndarray):
            if name == "flat":
                return list(value.flat)
            if name in {"T", "shape", "size"}:
                return getattr(value, name)
            if name in {"reshape", "ravel", "flatten", "mean", "sum", "min", "max", "astype"}:
                return Call(value, name)
        if isinstance(value, self.figure_type):
            if name == "axes":
                return value.axes
            if name in {
                "suptitle",
                "supxlabel",
                "supylabel",
                "tight_layout",
                "subplots_adjust",
                "set_size_inches",
                "set_dpi",
                "colorbar",
                "align_labels",
            }:
                return Call(value, name)
        if isinstance(value, self.axes_type):
            if name in {"figure", "spines"}:
                return getattr(value, name)
            if name in AXES:
                return Call(value, name)
        if isinstance(value, self.spine_types) and name in {
            "set_visible",
            "set_color",
            "set_linewidth",
            "set_position",
        }:
            return Call(value, name)
        if isinstance(value, self.grid_type):
            if name in {"fig", "figure", "axes", "ax_joint", "ax_marg_x", "ax_marg_y"} and hasattr(
                value, name
            ):
                return getattr(value, name)
            if name in {
                "set",
                "set_axis_labels",
                "set_titles",
                "add_legend",
                "despine",
                "refline",
                "map",
                "map_dataframe",
                "map_diag",
                "map_offdiag",
                "map_upper",
                "map_lower",
            }:
                return Call(value, name)
        if isinstance(value, self.colorbar_type) and name in {
            "set_label",
            "set_ticks",
            "set_ticklabels",
        }:
            return Call(value, name)
        if isinstance(value, self.artist_type) and name in {
            "set_color",
            "set_facecolor",
            "set_edgecolor",
            "set_alpha",
            "set_linewidth",
            "set_linestyle",
            "set_marker",
            "set_markersize",
            "set_fontsize",
            "set_fontfamily",
            "set_text",
            "set_rotation",
            "set_visible",
            "get_texts",
            "get_title",
            "get_legend",
        }:
            return Call(value, name)
        self.error(f"不支持属性 {name}，请使用绘图API。")

    def expression(self, node):
        self.steps += 1
        if self.steps > 100000:
            self.error("计算步骤过多，请简化循环。")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int, float, bool, type(None))):
                return node.value
        if isinstance(node, ast.Name) and node.id in self.env:
            return self.env[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [self.expression(item) for item in node.elts]
            return values if isinstance(node, ast.List) else tuple(values)
        if isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
            return {
                self.expression(key): self.expression(value)
                for key, value in zip(node.keys, node.values, strict=True)
            }
        if isinstance(node, ast.Attribute):
            return self.member(self.expression(node.value), node.attr)
        if isinstance(node, ast.Slice):
            return slice(
                *(
                    self.expression(value) if value else None
                    for value in (node.lower, node.upper, node.step)
                )
            )
        if isinstance(node, ast.Subscript):
            value, key = self.expression(node.value), self.expression(node.slice)
            if isinstance(value, Indexer):
                return getattr(value.owner, value.name)[key]
            if isinstance(
                value,
                (
                    list,
                    tuple,
                    dict,
                    str,
                    *self.frame_types,
                    self.modules["numpy"].ndarray,
                    *self.spine_types,
                ),
            ):
                return value[key]
        if isinstance(node, ast.Call):
            target = self.expression(node.func)
            if not isinstance(target, Call) or any(key.arg is None for key in node.keywords):
                self.error("请直接调用具名绘图函数。")
            args = [self.expression(value) for value in node.args]
            kwargs = {key.arg: self.expression(key.value) for key in node.keywords}
            return self.call(target, args, kwargs)
        if isinstance(node, ast.BinOp) and type(node.op) in BIN:
            return BIN[type(node.op)](self.expression(node.left), self.expression(node.right))
        if isinstance(node, ast.UnaryOp):
            operations = {
                ast.USub: operator.neg,
                ast.UAdd: operator.pos,
                ast.Not: operator.not_,
                ast.Invert: operator.invert,
            }
            if type(node.op) in operations:
                return operations[type(node.op)](self.expression(node.operand))
        if isinstance(node, ast.Compare) and all(type(value) in CMP for value in node.ops):
            left = self.expression(node.left)
            result = True
            for operation, right in zip(node.ops, node.comparators, strict=True):
                value = self.expression(right)
                result = result & CMP[type(operation)](left, value)
                left = value
            return result
        if isinstance(node, ast.IfExp):
            return self.expression(node.body if self.expression(node.test) else node.orelse)
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue) and value.conversion in {-1, 115, 114}:
                    item = self.expression(value.value)
                    if not isinstance(item, (str, int, float, bool, type(None))):
                        self.error("文字标签请使用数值或字符串。")
                    if value.conversion == 115:
                        item = str(item)
                    elif value.conversion == 114:
                        item = repr(item)
                    parts.append(
                        format(
                            item, self.expression(value.format_spec) if value.format_spec else ""
                        )
                    )
                else:
                    self.error("请检查文字标签的格式。")
            return "".join(parts)
        if isinstance(node, ast.Name):
            self.error(f"未知名称 {node.id}，请先定义变量。")
        self.error(f"不支持这项写法：{type(node).__name__}。")

    def call(self, target, args, kwargs):
        forbidden = {
            "backend",
            "usetex",
            "url",
            "urls",
            "fname",
            "filename",
            "path",
            "rc",
            "fontproperties",
        }

        def style_options(values):
            if isinstance(values, dict):
                return not (set(values) & forbidden) and all(
                    style_options(value) for value in values.values()
                )
            if isinstance(values, (tuple, list)):
                return all(style_options(value) for value in values)
            return True

        if set(kwargs) & forbidden or any(
            not style_options(value)
            for key, value in kwargs.items()
            if key
            in {
                "font",
                "fontdict",
                "prop",
                "subplot_kw",
                "gridspec_kw",
                "textprops",
                "arrowprops",
                "legend_kws",
                "cbar_kws",
                "facet_kws",
                "plot_kws",
                "diag_kws",
                "marginal_kws",
                "joint_kws",
                "line_kws",
                "scatter_kws",
                "err_kws",
                "boxprops",
                "whiskerprops",
                "capprops",
                "medianprops",
                "meanprops",
                "flierprops",
                "hist_kws",
                "kde_kws",
            }
        ):
            self.error("请在绘图代码中设置图形参数；导出由保存按钮处理。")
        if target.name in {"agg", "pivot_table", "crosstab"}:

            def aggregates(value):
                if isinstance(value, str):
                    return value in AGGREGATES
                if isinstance(value, (list, tuple)):
                    return all(aggregates(item) for item in value)
                if isinstance(value, dict):
                    return all(aggregates(item) for item in value.values())
                return False

            functions = args[:1] if target.name == "agg" else []
            positional = {"pivot_table": 3, "crosstab": 5}.get(target.name)
            if positional is not None and len(args) > positional:
                functions.append(args[positional])
            functions += [kwargs[key] for key in ("func", "aggfunc") if key in kwargs]
            if not all(aggregates(value) for value in functions):
                self.error("汇总请使用mean、sum、count等具名函数。")
        if isinstance(target.owner, str) and target.owner == "builtin":
            if target.name == "range":
                result = range(*args, **kwargs)
                if len(result) > 1000:
                    self.error("循环最多1000项，请用数组计算。")
                return result
            return BUILTINS[target.name](*args, **kwargs)
        if isinstance(target.owner, Rc):
            settings = dict(*args, **kwargs)
            if set(settings) - RC_KEYS:
                self.error("这项全局设置不可用，请设置颜色、字体或坐标轴。")
            self.modules["matplotlib"].rcParams.update(settings)
            return None
        if isinstance(target.owner, Module):
            if target.owner.name == "seaborn":
                return self.seaborn_call(target.name, args, kwargs)
            function = getattr(self.modules[target.owner.name], target.name)
        else:
            function = getattr(target.owner, target.name)
            if target.name.startswith("map") and args and isinstance(args[0], Call):
                callback = args[0]
                if (
                    not isinstance(callback.owner, Module)
                    or callback.owner.name != "seaborn"
                    or callback.name not in SEABORN - {"FacetGrid", "PairGrid", "JointGrid"}
                ):
                    self.error("分面映射请使用Seaborn绘图函数。")

                @wraps(getattr(self.modules["seaborn"], callback.name))
                def seeded_callback(*values, **options):
                    return self.seaborn_call(callback.name, values, options)

                args[0] = seeded_callback
        return function(*args, **kwargs)

    def seaborn_call(self, name, args, kwargs):
        function = getattr(self.modules["seaborn"], name)
        if "seed" in inspect.signature(function).parameters:
            kwargs.setdefault("seed", 0)
        if name == "relplot" and kwargs.get("kind") == "line":
            kwargs.setdefault("seed", 0)
        if name in {"pairplot", "jointplot"} and kwargs.get("kind") == "reg":
            key = "plot_kws" if name == "pairplot" else "joint_kws"
            options = dict(kwargs.get(key) or {})
            options.setdefault("seed", 0)
            kwargs[key] = options
        return function(*args, **kwargs)

    def assign(self, node, value):
        if isinstance(node, ast.Name) and not node.id.startswith("__"):
            self.env[node.id] = value
            return
        if isinstance(node, (ast.Tuple, ast.List)):
            values = list(value)
            if len(values) != len(node.elts):
                self.error("左右变量数量不一致。")
            for target, item in zip(node.elts, values, strict=True):
                self.assign(target, item)
            return
        if isinstance(node, ast.Subscript):
            owner, key = self.expression(node.value), self.expression(node.slice)
            if isinstance(owner, Rc):
                if key not in RC_KEYS:
                    self.error("这项全局设置不可用，请设置颜色、字体或坐标轴。")
                self.modules["matplotlib"].rcParams[key] = value
                return
            if isinstance(owner, Indexer):
                getattr(owner.owner, owner.name)[key] = value
                return
            if isinstance(owner, self.frame_types[:2]):
                owner[key] = value
                return
        self.error("请将计算结果保存到变量或数据列。")

    def statements(self, statements):
        for node in statements:
            self.line = node.lineno
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in MODULES:
                        self.error("请使用Matplotlib、Seaborn、NumPy或Pandas。")
                    self.env[alias.asname or alias.name.split(".")[0]] = Module(
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.level or node.module not in MODULES:
                    self.error("请使用绘图库的公开导入。")
                for alias in node.names:
                    self.env[alias.asname or alias.name] = self.member(
                        Module(node.module), alias.name
                    )
            elif isinstance(node, ast.Assign):
                value = self.expression(node.value)
                for target in node.targets:
                    self.assign(target, value)
            elif isinstance(node, ast.Expr):
                self.expression(node.value)
            elif isinstance(node, ast.For) and not node.orelse:
                for index, value in enumerate(self.expression(node.iter)):
                    if index >= 1000:
                        self.error("循环最多1000项，请用数组计算。")
                    self.assign(node.target, value)
                    self.statements(node.body)
            elif isinstance(node, ast.If):
                self.statements(node.body if self.expression(node.test) else node.orelse)
            elif isinstance(node, ast.Assert):
                if not self.expression(node.test):
                    message = (
                        self.expression(node.msg) if node.msg else "数据不满足这幅图形的条件。"
                    )
                    self.error(str(message)[:500])
            else:
                self.error(f"不支持这项语句：{type(node).__name__}。")


def export_figure(fig, dpi):
    width, height = fig.get_size_inches() * dpi
    if (
        not all(math.isfinite(value) and 100 <= value <= 12000 for value in (width, height))
        or width * height > 24000000
        or not 1 <= len(fig.axes) <= 36
    ):
        raise PlotProgramError("图面尺寸过大或过小，请调整尺寸和DPI。")
    # Keep heatmap labels legible in the saved image and all export formats.
    # This changes text geometry only; the matrix and plotted values stay intact.
    from matplotlib.collections import QuadMesh

    fig.canvas.draw()
    for axis in fig.axes:
        if getattr(axis, "_colorbar", None) is not None or not any(
            isinstance(item, QuadMesh) for item in axis.collections
        ):
            continue
        ylabels = [
            label for label in axis.get_yticklabels() if label.get_visible() and label.get_text()
        ]
        ybounds = sorted([label.get_window_extent() for label in ylabels], key=lambda box: box.y0)
        if any(a.y1 + 4 > b.y0 for a, b in zip(ybounds, ybounds[1:])):
            for label in ylabels:
                label.set_rotation(0)
                label.set_horizontalalignment("right")
                label.set_verticalalignment("center")
            if fig.get_layout_engine() is None:
                fig.set_layout_engine("constrained")
        labels = [
            label for label in axis.get_xticklabels() if label.get_visible() and label.get_text()
        ]
        bounds = [label.get_window_extent() for label in labels]
        if any(a.x1 + 12 > b.x0 for a, b in zip(bounds, bounds[1:])):
            for label in labels:
                label.set_rotation(60)
                label.set_horizontalalignment("right")
                label.set_rotation_mode("anchor")
            if fig.get_layout_engine() is None:
                fig.set_layout_engine("constrained")
    files = {}
    for extension in ("svg", "png", "pdf"):
        buffer = io.BytesIO()
        metadata = (
            {"Date": None}
            if extension == "svg"
            else {"CreationDate": None, "ModDate": None}
            if extension == "pdf"
            else None
        )
        fig.savefig(buffer, format=extension, dpi=dpi, metadata=metadata)
        files[extension] = buffer.getvalue()
    # Only the native renderer creates this internal artifact. Never accepted
    # from a user upload/import; restyling verifies the owned Run + byte digest.
    files["figure_state"] = pickle.dumps(fig, protocol=5)
    if any(len(value) > 20000000 for value in files.values()):
        raise PlotProgramError("图形超过20 MB，请减少图层或尺寸。")
    return files


def figure_colors(fig):
    from matplotlib.colors import to_hex, to_rgba_array

    found = []
    for artist in fig.findobj():
        for name in ("get_color", "get_facecolor", "get_edgecolor"):
            getter = getattr(artist, name, None)
            if callable(getter):
                try:
                    for rgba in to_rgba_array(getter()):
                        color = to_hex(rgba, keep_alpha=True)
                        if color not in found:
                            found.append(color)
                except (TypeError, ValueError):
                    pass
        if len(found) >= 32:
            break
    return found[:32]


# This comment is data for the finite renderer, never executable Python.
STYLE_MARKER = "\n# statforge-style:"


def split_styles(code):
    parts = code.split(STYLE_MARKER)
    if len(parts) == 1:
        return code, []
    styles = []
    for part in parts[1:]:
        line, sep, tail = part.partition("\n")
        if sep and tail:
            raise PlotProgramError("样式记录必须位于代码末尾。")
        try:
            item = json.loads(line)
            if not isinstance(item, dict) or set(item) != {"style", "dpi", "base_dpi"}:
                raise ValueError()
            validate_style(item["style"])
            for key in ("dpi", "base_dpi"):
                if type(item[key]) is not int or not 72 <= item[key] <= 600:
                    raise ValueError()
            styles.append(item)
        except (ValueError, TypeError, KeyError):
            raise PlotProgramError("样式记录无效，请重新应用样式。") from None
    if len(styles) > 100 or any(x["base_dpi"] != styles[0]["base_dpi"] for x in styles):
        raise PlotProgramError("样式记录过多或不一致。")
    return parts[0], styles


def validate_style(style):
    defaults = {
        "colors": {},
        "font_family": None,
        "font_size": None,
        "line_width": None,
        "bar_width_scale": None,
        "width_inches": None,
        "height_inches": None,
        "title": None,
        "xlabel": None,
        "ylabel": None,
        "grid": None,
        "tight_layout": False,
        "dpi": None,
    }
    if not isinstance(style, dict) or set(style) - set(defaults):
        raise ValueError()
    values = {**defaults, **style}
    scale = values["bar_width_scale"]
    if scale is not None and (type(scale) not in (int, float) or not 0 < scale <= 1):
        raise ValueError()
    colors = values["colors"]
    if (
        not isinstance(colors, dict)
        or len(colors) > 32
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in colors.items())
    ):
        raise ValueError()
    for key, low, high in (
        ("font_size", 6, 48),
        ("line_width", 0.2, 12),
        ("width_inches", 2, 24),
        ("height_inches", 2, 24),
        ("dpi", 72, 600),
    ):
        value = values[key]
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not low <= value <= high
            or (key == "dpi" and type(value) is not int)
        ):
            raise ValueError()
    for key, limit in (("font_family", 100), ("title", 200), ("xlabel", 200), ("ylabel", 200)):
        value = values[key]
        if value is not None and (
            not isinstance(value, str) or len(value) > limit or (key == "font_family" and not value)
        ):
            raise ValueError()
    if values["grid"] is not None and type(values["grid"]) is not bool:
        raise ValueError()
    if type(values["tight_layout"]) is not bool:
        raise ValueError()
    return SimpleNamespace(**values)


def restyle(content, style, dpi):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import to_hex, to_rgba
    from matplotlib.container import BarContainer
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.text import Text

    try:
        patch = validate_style(style)
    except ValueError:
        raise PlotProgramError("样式记录无效，请重新应用样式。") from None
    plt.close("all")
    try:
        # content is a hash-verified internal artifact, never a source PKL or
        # imported project artifact. Runs lacking this state are not restyled.
        fig = pickle.loads(content)
        if not isinstance(fig, Figure):
            raise PlotProgramError("图表渲染状态不可用。")
        if patch.bar_width_scale is not None:
            bars = [
                group
                for axis in fig.axes
                for group in axis.containers
                if isinstance(group, BarContainer) and group.patches
            ]
            if not bars:
                raise PlotProgramError("图中没有可直接调整宽度的柱，请修改绘图代码。")
            for group in bars:
                for rect in group.patches:
                    if group.orientation == "horizontal":
                        height = rect.get_height()
                        rect.set_y(rect.get_y() + height * (1 - patch.bar_width_scale) / 2)
                        rect.set_height(height * patch.bar_width_scale)
                    else:
                        width = rect.get_width()
                        rect.set_x(rect.get_x() + width * (1 - patch.bar_width_scale) / 2)
                        rect.set_width(width * patch.bar_width_scale)
        # The UI and Agent receive the rendered 8-bit colors from figure_colors.
        # Seaborn may retain higher-precision/desaturated RGBA values internally.
        colors = {to_hex(old, keep_alpha=True): to_rgba(new) for old, new in patch.colors.items()}
        for artist in fig.findobj():
            if isinstance(artist, Line2D) and patch.line_width is not None:
                artist.set_linewidth(patch.line_width)
            if isinstance(artist, Text):
                if patch.font_family:
                    artist.set_fontfamily(patch.font_family)
                if patch.font_size:
                    artist.set_fontsize(patch.font_size)
            for suffix in ("color", "facecolor", "edgecolor"):
                getter, setter = (
                    getattr(artist, "get_" + suffix, None),
                    getattr(artist, "set_" + suffix, None),
                )
                if colors and callable(getter) and callable(setter):
                    original = getter()
                    try:
                        single = to_rgba(original)
                    except (ValueError, TypeError):
                        if (
                            isinstance(original, np.ndarray)
                            and original.ndim == 2
                            and original.shape[1] == 4
                        ):
                            changed = original.copy()
                            for index, rgba in enumerate(original):
                                replacement = colors.get(to_hex(rgba, keep_alpha=True))
                                if replacement is not None:
                                    changed[index] = replacement
                            setter(changed)
                    else:
                        replacement = colors.get(to_hex(single, keep_alpha=True))
                        if replacement is not None:
                            setter(replacement)
        width, height = fig.get_size_inches()
        fig.set_size_inches(patch.width_inches or width, patch.height_inches or height)
        if patch.title is not None:
            # A heatmap's colorbar is an auxiliary Axes, not a second panel.
            panels = [axis for axis in fig.axes if getattr(axis, "_colorbar", None) is None]
            if len(panels) == 1:
                panels[0].set_title(patch.title)
                if fig._suptitle is not None:
                    fig._suptitle.set_text("")
            else:
                fig.suptitle(patch.title)
        for axis in fig.axes:
            if patch.xlabel is not None:
                axis.set_xlabel(patch.xlabel)
            if patch.ylabel is not None:
                axis.set_ylabel(patch.ylabel)
            if patch.grid is not None:
                axis.grid(patch.grid)
        if patch.tight_layout:
            fig.tight_layout()
        files = export_figure(fig, dpi)
        return {
            "files": files,
            "width": round(fig.get_figwidth() * dpi),
            "height": round(fig.get_figheight() * dpi),
            "axes": len(fig.axes),
            "colors": figure_colors(fig),
        }
    finally:
        plt.close("all")


def configure_export(code, source_sha256):
    """Keep native style workers and exported replay on the same vector settings."""
    import matplotlib as mpl

    base_code, _ = split_styles(code)
    mpl.rcParams.update(
        {
            "text.usetex": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.family": "Microsoft YaHei",
            "axes.unicode_minus": False,
            "svg.hashsalt": hashlib.sha256((base_code + source_sha256).encode()).hexdigest(),
        }
    )


def render(code, frame, source_sha256, *, dpi=150):
    """Called only inside the registered worker after resource limits are set."""
    base_code, styles = split_styles(code)
    tree = parse(base_code)
    render_dpi = styles[0]["base_dpi"] if styles else dpi
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(value not in "0123456789abcdef" for value in source_sha256)
    ):
        raise PlotProgramError("数据版本无法确认，请重新打开数据。")
    program = Program(frame)
    program.modules["numpy"].random.seed(0)
    plt = program.modules["matplotlib.pyplot"]
    _ = program.modules["matplotlib"]
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise PlotProgramError("导出DPI应为72–600的整数。")
    plt.close("all")
    configure_export(base_code, source_sha256)
    try:
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("error", RuntimeWarning)
            program.statements(tree.body)
            figures = plt.get_fignums()
            if len(figures) != 1:
                raise PlotProgramError("请保留一个Figure；组合图可使用多个子图。")
            fig = plt.figure(figures[0])
            width, height = fig.get_size_inches() * render_dpi
            if (
                not all(math.isfinite(value) and 100 <= value <= 12000 for value in (width, height))
                or width * height > 24000000
                or not 1 <= len(fig.axes) <= 36
            ):
                raise PlotProgramError("图面尺寸过大或过小，请调整figsize和DPI。")
            files = export_figure(fig, render_dpi)
            if any(
                "constrained_layout not applied" in str(item.message)
                or "not enough room" in str(item.message)
                for item in observed
            ):
                raise PlotProgramError("图面空间不足，请增加尺寸或缩小字号。")
            result = {
                "files": files,
                "width": round(width),
                "height": round(height),
                "axes": len(fig.axes),
                "input_rows": len(frame),
                "colors": figure_colors(fig),
                "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "source_sha256": source_sha256,
                "randomness": {
                    "numpy_seed": 0,
                    "bootstrap_default_seed": 0,
                    "explicit_seed_preserved": True,
                },
            }
            for index, item in enumerate(styles):
                final_dpi = dpi if index == len(styles) - 1 else item["dpi"]
                result.update(restyle(result["files"]["figure_state"], item["style"], final_dpi))
            return result
    except PlotProgramError:
        raise
    except Exception as error:
        raise PlotProgramError(
            str(error)[:500] or "请检查变量、数值范围和绘图参数。", program.line
        ) from None
    finally:
        plt.close("all")
