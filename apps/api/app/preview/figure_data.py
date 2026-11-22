"""Authorized immutable local plot projections, not new statistical Runs."""

import hashlib
import json
import math
import tempfile
import uuid
from pathlib import Path

import anyio
import numpy as np
import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import StatForgeError
from app.models.user import User
from app.models.workspace import WorkspaceRecord, WorkspaceRevision
from app.preview.context import (
    canonical,
    digest,
    load_dataset,
    resolve_context,
    verify_storage_version,
)
from app.preview.write_fence import acquire_write_fence
from app.routers.statistics import get_statistical_run
from app.schemas.preview import PreviewScope
from app.schemas.workspace import FigureData, FigureSpec, GenerateFigureData
from app.services import storage_service
from app.services.profiling_service import load_dataframe
from app.statistics.plot_projection import PROJECTION_VERSION, distribution_summary, select_scatter


def unavailable(code="FIGURE_SOURCE_UNAVAILABLE"):
    messages = {
        ("FIGURE_SAMPLE_MISMATCH"): (
            "绘图数据与所选统计结果的有效样本或分组不一致。请在结果中确认数据版本，"
            "使用原参数重新分析后绘图。"
        ),
        "FIGURE_SAMPLE_UNKNOWN": "此历史结果未保存有效样本信息。请使用原参数重新分析后绘图。",
        "FIGURE_NONFINITE_INPUT": "绘图变量包含无穷值，请在数据中处理该变量后重新分析。",
        ("FIGURE_LEGACY_PAIRING_UNKNOWN"): (
            "此历史相关分析未保存成对观测信息，请使用原参数重新分析后绘图。"
        ),
        ("FIGURE_PROJECTION_UNAVAILABLE"): (
            "所选变量没有可绘制的有限数值，或数值范围无法表示。请检查数据后重新分析。"
        ),
        "FIGURE_REQUEST_CONFLICT": "这次绘图请求已用于其他输入，请重新点击生成。",
    }
    return StatForgeError(
        409,
        code,
        messages.get(
            code,
            "图表所引用的数据版本或统计结果不可用。请返回结果，重新选择对应分析；若原文件已改变，请重新分析。",
        ),
    )


def normalized(scope: PreviewScope) -> PreviewScope:
    if scope.ml_run_id or not scope.statistical_run_id:
        raise unavailable()
    return scope.model_copy(
        update={
            "statistical_config": None,
            "ml_config": None,
            "session_id": None,
            "session_revision": None,
        }
    )


def input_hash(scope, chart_type, fields, bins):
    return digest(
        {
            "scope": normalized(scope).model_dump(mode="json"),
            "chart_type": chart_type,
            "fields": fields,
            "bins": bins if chart_type == "distribution" else None,
            "projection_version": PROJECTION_VERSION,
        }
    )


async def source(db: AsyncSession, scope: PreviewScope, user: User):
    scope = normalized(scope)
    await resolve_context(db, scope, user)
    dataset = await load_dataset(db, scope, user)
    await verify_storage_version(dataset)
    assert scope.statistical_run_id is not None
    run = await get_statistical_run(scope.statistical_run_id, db, user)
    if (
        run.project_id != scope.project_id
        or run.dataset_id != dataset.id
        or run.dataset_sha256 != scope.dataset_sha256
        or run.status.value != "completed"
        or run.result is None
        or run.result.status != "completed"
        or not run.input_config
    ):
        raise unavailable()
    return dataset, run


async def validate_snapshot(db, payload: FigureData, project, user):
    if payload.scope.project_id != project or payload.scope != normalized(payload.scope):
        raise unavailable()
    _, run = await source(db, payload.scope, user)
    assert run.result is not None
    if (
        payload.input_config != run.input_config
        or payload.source_result_sha256 != digest(run.result.model_dump(mode="json"))
        or payload.content_sha256 != digest(payload.data)
        or payload.input_hash
        != input_hash(
            payload.scope, payload.chart_type, payload.fields, payload.params.get("bins", 12)
        )
        or payload.engine_version != PROJECTION_VERSION
    ):
        raise unavailable()
    return run


async def read_snapshot(db, snapshot_id, user, project=None):
    from app.preview.workspace import load_record

    row = await load_record(db, snapshot_id, user, project)
    if row.kind != "figure_data" or row.revision != 1:
        raise unavailable()
    try:
        payload = FigureData.model_validate_json(row.payload_json)
    except ValueError:
        raise unavailable() from None
    await validate_snapshot(db, payload, row.project_id, user)
    return row, payload


async def validate_figure(db, spec: FigureSpec, project, user):
    assert spec.snapshot_id is not None
    _, snapshot = await read_snapshot(db, spec.snapshot_id, user, project)
    if (
        spec.content_sha256 != snapshot.content_sha256
        or spec.scope != snapshot.scope
        or spec.chart_type != snapshot.chart_type
        or spec.fields != snapshot.fields
        or ("coefficient_ci" in spec.layers and not snapshot.data.get("ci_available"))
    ):
        raise unavailable()


def read_frame(dataset):
    parent = Path(settings.app_data_path or settings.upload_temp_dir) / "preview-temp"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="figure-", dir=parent) as directory:
        path = Path(directory) / "source"
        storage_service.download_to_path(dataset.object_key, str(path))
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != dataset.sha256:
                raise unavailable()
        return load_dataframe(
            str(path), dataset.file_format, dataset.sheet_name, dataset.csv_encoding
        )


def numeric(frame, field):
    values = frame.get_column(field).cast(pl.Float64, strict=False).to_numpy()
    if np.any(np.isinf(values)):
        raise unavailable("FIGURE_NONFINITE_INPUT")
    return values


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def project_data(dataset, run, body):
    result = run.result
    config = run.input_config
    sample = result.sample
    if sample is None:
        raise unavailable("FIGURE_SAMPLE_UNKNOWN")
    method = {
        "distribution": {"descriptive"},
        "correlation_scatter": {"pearson", "spearman"},
        "ols_coefficients": {"linear_regression"},
    }[body.chart_type]
    if run.method not in method or not set(body.fields) <= set(body.scope.columns):
        raise unavailable()
    if body.chart_type == "ols_coefficients":
        if body.fields != [config.get("target")]:
            raise unavailable()
        coefficients = result.estimates.get("coefficients")
        if (
            not isinstance(coefficients, list)
            or not coefficients
            or any(not isinstance(c, dict) for c in coefficients)
        ):
            raise unavailable()
        data = {
            "coefficients": coefficients,
            "encoding": result.estimates.get("encoding", {}),
            "reference_categories": config.get("reference_categories"),
            "confidence_level": result.confidence_level,
            "standardize": config.get("standardize"),
            "ci_available": all(
                finite_number(c.get("ci_lower")) and finite_number(c.get("ci_upper"))
                for c in coefficients
            ),
        }
        if any(not finite_number(c.get("coefficient")) for c in coefficients):
            raise unavailable()
        return data, sample.final_n, len(coefficients), "saved-ols-coefficients-v1", None, None
    frame = read_frame(dataset)
    if frame.height != sample.original_n:
        raise unavailable("FIGURE_SAMPLE_MISMATCH")
    if body.chart_type == "distribution":
        if len(body.fields) != 1 or body.fields[0] not in config.get("variables", []):
            raise unavailable()
        field = body.fields[0]
        raw = numeric(frame, field)
        grouping = config.get("group_variable")
        if grouping:
            if (
                grouping not in frame.columns
                or result.descriptive_statistics.get("group_variable") != grouping
            ):
                raise unavailable("FIGURE_SAMPLE_MISMATCH")
            groups = frame.get_column(grouping).to_list()
            valid_groups = np.array(
                [g is not None and not (isinstance(g, float) and np.isnan(g)) for g in groups]
            )
            valid = ~np.isnan(raw) & valid_groups
            if (
                sample.reason != "missing values excluded per variable and grouping column"
                or sample.per_variable.get(field) != int((~valid).sum())
            ):
                raise unavailable("FIGURE_SAMPLE_MISMATCH")
            summaries = result.descriptive_statistics.get("variables", {})
            projected = []
            for group in dict.fromkeys(g for g, ok in zip(groups, valid_groups) if ok):
                group_mask = np.array([g == group for g in groups])
                values = raw[valid & group_mask]
                key = f"{grouping}={group} · {field}"
                saved = summaries.get(key, {})
                if saved.get("count") != len(values) or sample.per_variable.get(key) != int(
                    (group_mask & ~valid).sum()
                ):
                    raise unavailable("FIGURE_SAMPLE_MISMATCH")
                data = (
                    distribution_summary(values.tolist(), bins=body.bins)
                    if len(values)
                    else {"n": 0}
                )
                if len(values):
                    for quantile in ("q1", "median", "q3"):
                        if not finite_number(saved.get(quantile)) or not math.isclose(
                            saved[quantile], data["box"][quantile], rel_tol=1e-6, abs_tol=1e-8
                        ):
                            raise unavailable("FIGURE_SAMPLE_MISMATCH")
                projected.append({"label": str(group), **data})
            if not valid.any():
                raise unavailable("FIGURE_PROJECTION_UNAVAILABLE")
            return (
                {"group_variable": grouping, "groups": projected},
                int(valid.sum()),
                sum(g.get("box", {}).get("outliers_displayed", 0) for g in projected),
                "descriptive-per-group-nan-v1",
                None,
                None,
            )
        values = raw[~np.isnan(raw)]
        saved = result.descriptive_statistics.get("variables", {}).get(field, {})
        if (
            saved.get("count") != len(values)
            or sample.per_variable.get(field) != int(np.isnan(raw).sum())
            or sample.reason != "missing values excluded per variable"
        ):
            raise unavailable("FIGURE_SAMPLE_MISMATCH")
        data = distribution_summary(values.tolist(), bins=body.bins)
        for key in ("q1", "median", "q3"):
            if not isinstance(saved.get(key), (int, float)) or not math.isclose(
                saved[key], data["box"][key], rel_tol=1e-6, abs_tol=1e-8
            ):
                raise unavailable("FIGURE_SAMPLE_MISMATCH")
        return (
            data,
            len(values),
            data["box"]["outliers_displayed"],
            "descriptive-per-variable-nan-v1",
            None,
            None,
        )
    if body.fields != config.get("variables") or len(body.fields) != 2:
        raise unavailable()
    x, y = (numeric(frame, field) for field in body.fields)
    if (
        config.get("missing_policy") == "pairwise_delete"
        and sample.excluded_n
        and sample.reason != "listwise deletion"
    ):
        raise unavailable("FIGURE_LEGACY_PAIRING_UNKNOWN")
    mask = ~np.isnan(x) & ~np.isnan(y)
    indices = np.flatnonzero(mask)
    if len(indices) != sample.final_n or result.statistic.get("n") != len(indices):
        raise unavailable("FIGURE_SAMPLE_MISMATCH")
    data = select_scatter([(int(i), float(x[i]), float(y[i])) for i in indices])
    return (
        data,
        data["total_n"],
        data["displayed_n"],
        data["rule"],
        data["selection_sha256"],
        data["seed"],
    )


async def generate(db: AsyncSession, user: User, body: GenerateFigureData):
    from app.preview.workspace import credential_free, read_record

    await acquire_write_fence(db, user.id)
    await credential_free(db, user, body.model_dump(mode="json"))
    scope = normalized(body.scope)
    dataset, run = await source(db, scope, user)
    hash_value = input_hash(scope, body.chart_type, body.fields, body.bins)
    request_key = uuid.uuid5(user.id, f"figure-request:{body.request_id}")
    receipt = await db.get(WorkspaceRecord, request_key, populate_existing=True)
    if receipt is not None:
        saved = json.loads(receipt.payload_json)
        if (
            receipt.user_id != user.id
            or receipt.project_id != scope.project_id
            or receipt.kind != "figure_request"
            or saved.get("input_hash") != hash_value
        ):
            raise unavailable("FIGURE_REQUEST_CONFLICT")
        return await read_record(db, uuid.UUID(saved["snapshot_id"]), user)
    snapshot_key = uuid.uuid5(user.id, f"figure-data:{hash_value}")
    row = await db.get(WorkspaceRecord, snapshot_key, populate_existing=True)
    if row is not None:
        await read_snapshot(db, snapshot_key, user, scope.project_id)
    else:
        try:
            data, n, displayed, rule, selection, seed = await anyio.to_thread.run_sync(
                project_data, dataset, run, body.model_copy(update={"scope": scope})
            )
        except (ValueError, OverflowError):
            raise unavailable("FIGURE_PROJECTION_UNAVAILABLE") from None
        payload = FigureData(
            title="科研图形数据",
            scope=scope,
            chart_type=body.chart_type,
            fields=body.fields,
            input_config=run.input_config,
            source_result_sha256=digest(run.result.model_dump(mode="json")),
            sample_rule=rule,
            selection_sha256=selection,
            params={"bins": body.bins} if body.chart_type == "distribution" else {},
            engine_version=PROJECTION_VERSION,
            n=n,
            displayed_n=displayed,
            seed=seed,
            data=data,
            content_sha256=digest(data),
            request_id=body.request_id,
            input_hash=hash_value,
        )
        await credential_free(db, user, payload.model_dump(mode="json"))
        raw = canonical(payload.model_dump(mode="json"))
        db.add(
            WorkspaceRecord(
                id=snapshot_key,
                user_id=user.id,
                project_id=scope.project_id,
                kind="figure_data",
                payload_json=raw,
            )
        )
        await db.flush()
        db.add(WorkspaceRevision(record_id=snapshot_key, revision=1, payload_json=raw))
    raw_receipt = canonical(
        {"snapshot_id": str(snapshot_key), "input_hash": hash_value, "request_id": body.request_id}
    )
    db.add(
        WorkspaceRecord(
            id=request_key,
            user_id=user.id,
            project_id=scope.project_id,
            kind="figure_request",
            payload_json=raw_receipt,
        )
    )
    await db.flush()
    db.add(WorkspaceRevision(record_id=request_key, revision=1, payload_json=raw_receipt))
    await db.commit()
    return await read_record(db, snapshot_key, user)
