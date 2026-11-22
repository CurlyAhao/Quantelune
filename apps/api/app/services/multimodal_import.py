"""Explicit mappings from precomputed NumPy feature bundles to analysis tables."""

from __future__ import annotations

import hashlib
import pickle
import re
from collections import OrderedDict
from pathlib import Path
from typing import Literal

import numpy as np
import polars as pl
from pydantic import BaseModel, Field

from app.exceptions import StatForgeError


def invalid(message: str) -> StatForgeError:
    return StatForgeError(422, "MULTIMODAL_IMPORT_INVALID", message)


class FeatureUnpickler(pickle.Unpickler):
    """Read data containers and NumPy arrays, without loading project classes."""

    def find_class(self, module: str, name: str):
        if (module, name) in {
            ("numpy", "ndarray"),
            ("numpy", "dtype"),
            ("numpy.core.multiarray", "_reconstruct"),
            ("numpy.core.multiarray", "scalar"),
            ("numpy._core.multiarray", "_reconstruct"),
            ("numpy._core.multiarray", "scalar"),
            ("numpy.core.numeric", "_frombuffer"),
            ("numpy._core.numeric", "_frombuffer"),
            ("_codecs", "encode"),
            ("builtins", "set"),
            ("builtins", "frozenset"),
            ("builtins", "slice"),
            ("builtins", "complex"),
            ("builtins", "bytes"),
            ("collections", "OrderedDict"),
        }:
            return super().find_class(module, name)
        raise invalid(
            f"文件包含尚未支持的对象 {module}.{name}。请使用字典、列表和 NumPy 数组形式的特征 PKL。"
        )


def load(path: str):
    try:
        with open(path, "rb") as source:
            return FeatureUnpickler(source, encoding="latin1").load()
    except StatForgeError:
        raise
    except Exception as exc:
        raise invalid(
            f"无法读取特征 PKL（{type(exc).__name__}），请核对文件和数据集说明。"
        ) from None


def container(value):
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, (list, tuple)) and value:
        if all(isinstance(row, dict) for row in value):
            keys = list(value[0])
            if all(set(row) == set(keys) for row in value):
                return {str(key): [row[key] for row in value] for key in keys}
        if isinstance(value, tuple):
            return {str(i): item for i, item in enumerate(value)}
    return None


def array(value):
    if not isinstance(value, (np.ndarray, list, tuple)):
        return None
    try:
        return np.asarray(value)
    except (ValueError, TypeError):
        result = np.empty(len(value), dtype=object)
        result[:] = value
        return result


def fields(value, prefix=None, depth=0):
    result = []
    for name, item in (container(value) or {}).items():
        path = [*(prefix or []), name]
        nested = container(item)
        if isinstance(item, dict) and depth < 3:
            result.extend(fields(nested, path, depth + 1))
            continue
        data = array(item)
        if data is None or data.ndim == 0 or not len(data):
            continue
        first = np.asarray(data[0])
        numeric = data.dtype.kind in "biuf" or (first.dtype.kind in "biuf" and first.ndim > 0)
        shape = list(data.shape) if data.dtype != object else [len(data), *list(first.shape)]
        result.append(
            {
                "path": path,
                "name": "/".join(path),
                "rows": len(data),
                "shape": shape,
                "dtype": str(data.dtype),
                "numeric": numeric,
                "scalar": first.size == 1,
                "sample": [str(np.asarray(item).tolist())[:160] for item in data[:3]],
            }
        )
    return result


def _inspect(path: str) -> dict:
    root = load(path)
    groups = []
    tree = []
    seen = set()

    def visit(value, node_path, depth):
        if len(tree) >= 300 or depth > 6 or id(value) in seen:
            return
        seen.add(id(value))
        children = container(value)
        data = array(value) if children is None else None
        tree.append(
            {
                "path": node_path,
                "kind": type(value).__name__,
                "shape": list(data.shape) if data is not None else None,
                "keys": len(children) if children is not None else None,
            }
        )
        if children is None:
            return
        candidates = fields(value)
        split_children = {
            key.lower() for key, item in children.items() if isinstance(item, dict)
        } & {"train", "test", "valid", "val", "validation", "dev"}
        if not split_children and len(candidates) > 1 and any(f["numeric"] for f in candidates):
            groups.append(
                {"path": node_path, "name": "/".join(node_path) or "根节点", "fields": candidates}
            )
        for key, item in list(children.items())[:100]:
            visit(item, [*node_path, key], depth + 1)

    visit(root, [], 0)
    if not groups:
        raise invalid(
            "未发现按样本排列的数值特征。当前支持字典、元组或记录列表中的 NumPy 特征数组。"
        )
    return _inspection_result(path, groups, tree)


def _inspection_result(path, groups, tree):
    with open(path, "rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {
        "groups": groups,
        "tree": tree,
        "structure_truncated": len(tree) >= 300,
        "sha256": digest,
    }


def resolve(value, path):
    for part in path:
        current = container(value)
        if current is None or part not in current:
            raise invalid("所选字段已不存在，请重新查看文件结构。")
        value = current[part]
    return value


class Modality(BaseModel):
    path: list[str]
    name: str = Field(min_length=1, max_length=40)
    pooling: Literal["mean", "max", "flatten"] = "mean"
    dimensions: Literal[0, 16, 32, 64] = 32
    lengths: list[str] | None = None


class Mapping(BaseModel):
    node: list[str] = Field(default_factory=list)
    label: list[str]
    identifier: list[str] | None = None
    modalities: list[Modality] = Field(min_length=1, max_length=8)
    task: Literal["classification", "regression"] = "classification"
    expected_sha256: str


def _convert(path: str, mapping: Mapping, destination: str) -> dict:
    with open(path, "rb") as source:
        sha = hashlib.file_digest(source, "sha256").hexdigest()
    if sha != mapping.expected_sha256:
        raise invalid("文件已变化，请重新查看结构并确认字段。")
    root = resolve(load(path), mapping.node)
    labels = array(resolve(root, mapping.label))
    if labels is None or labels.ndim == 0 or not len(labels):
        raise invalid("标签字段必须包含每个样本的一个标签。")
    count = len(labels)
    if labels.size != count:
        raise invalid(
            "标签包含多个值，请选择一个标量标签字段；当前不把 one-hot 或多标签静默转为单标签。"
        )
    labels = labels.reshape(-1)
    if count > 50_000:
        raise invalid("当前桌面分析支持最多 50,000 个样本，请选择较小的数据节点。")
    if mapping.task == "regression":
        try:
            labels = labels.astype(float)
        except (ValueError, TypeError):
            raise invalid("回归标签必须是数值。") from None
        if not np.isfinite(labels).all():
            raise invalid("标签含缺失或无穷值，请先整理标签。")
    else:
        if any(
            item is None or (isinstance(item, (float, np.floating)) and not np.isfinite(item))
            for item in labels
        ):
            raise invalid("标签含缺失或无穷值，请先整理标签。")
        labels = np.asarray([str(item) for item in labels])
    identifiers = np.arange(count).astype(str)
    if mapping.identifier:
        values = array(resolve(root, mapping.identifier))
        if values is None or values.size != count:
            raise invalid("样本 ID 数量与标签数量不一致。")
        identifiers = np.asarray([str(item) for item in values.reshape(-1)])
    output = OrderedDict(sample_id=identifiers.tolist(), label=labels.tolist())
    used = set()
    metadata = []
    for modality in mapping.modalities:
        if modality.path == mapping.label or modality.path == mapping.identifier:
            raise invalid("标签和样本 ID 不能同时作为模态特征。")
        name = re.sub(r"[^\w]", "_", modality.name).strip("_")
        if not name or name.lower() in used:
            raise invalid("请为每个模态填写不同的名称。")
        used.add(name.lower())
        values = array(resolve(root, modality.path))
        if values is None or len(values) != count:
            raise invalid(f"{modality.name} 的样本数与标签不一致。")
        lengths = array(resolve(root, modality.lengths)) if modality.lengths else None
        if lengths is not None and lengths.size != count:
            raise invalid(f"{modality.name} 的有效长度数量不一致。")
        vectors = []
        shape = None
        for index, sample in enumerate(values):
            try:
                feature = np.asarray(sample, dtype=np.float64)
            except (TypeError, ValueError):
                raise invalid(f"{modality.name} 第 {index + 1} 个样本不是数值特征。") from None
            if shape is None:
                shape = list(feature.shape)
            if feature.ndim > 1:
                if lengths is not None:
                    raw_length = np.asarray(lengths).reshape(-1)[index]
                    if (
                        not np.isfinite(raw_length)
                        or int(raw_length) != raw_length
                        or not 1 <= int(raw_length) <= feature.shape[0]
                    ):
                        raise invalid(f"{modality.name} 第 {index + 1} 个样本的有效长度无效。")
                    feature = feature[: int(raw_length)]
                if modality.pooling == "mean":
                    feature = feature.mean(axis=tuple(range(feature.ndim - 1)))
                elif modality.pooling == "max":
                    feature = feature.max(axis=tuple(range(feature.ndim - 1)))
            feature = feature.reshape(-1)
            if not feature.size or not np.isfinite(feature).all():
                raise invalid(f"{modality.name} 第 {index + 1} 个样本含空特征、缺失或无穷值。")
            if vectors and feature.size != vectors[0].size:
                raise invalid(f"{modality.name} 的特征长度不同，请选择均值或最大值聚合。")
            vectors.append(feature)
        matrix = np.stack(vectors)
        input_dimensions = matrix.shape[1]
        if modality.dimensions and input_dimensions > modality.dimensions:
            # Data-independent projection: no fitting across held-out samples.
            from sklearn.random_projection import GaussianRandomProjection

            matrix = GaussianRandomProjection(
                n_components=modality.dimensions, random_state=42
            ).fit_transform(matrix)
        columns = [f"{name}__{i:04d}" for i in range(matrix.shape[1])]
        if len(output) + len(columns) > 500:
            raise invalid("特征超过当前 500 列导入上限，请为模态选择 16、32 或 64 维固定投影。")
        for i, column in enumerate(columns):
            output[column] = matrix[:, i]
        metadata.append(
            {
                **modality.model_dump(),
                "columns": columns,
                "sample_shape": shape,
                "input_dimensions": input_dimensions,
                "output_dimensions": len(columns),
                "projection": "gaussian_seed_42" if len(columns) < input_dimensions else None,
            }
        )
    frame = pl.DataFrame(output)
    frame.write_parquet(destination)
    return {
        "format": "statforge-multimodal-features-v1",
        "source_sha256": sha,
        "node": mapping.node,
        "label_path": mapping.label,
        "label_column": "label",
        "identifier_path": mapping.identifier,
        "identifier_column": "sample_id",
        "task": mapping.task,
        "label_count": len(set(labels.tolist())),
        "modalities": metadata,
        "rows": count,
        "split": "/".join(mapping.node) or None,
        "evaluation": "selected_node_only",
    }


def _run_parser(operation: str, path: str, options: dict | None = None) -> dict:
    from app.services.parser_control import run

    try:
        result = run(operation, path, timeout=120, options=options)
    except Exception:
        raise invalid("特征 PKL 解析超时或资源不足，请检查文件后重试。") from None
    if "error" in result:
        raise invalid(result["error"])
    return result


def inspect(path: str) -> dict:
    return _run_parser("inspect", path)


def convert(path: str, mapping: Mapping, destination: str) -> dict:
    return _run_parser(
        "convert",
        path,
        {"mapping": mapping.model_dump(), "destination": str(Path(destination).resolve())},
    )
