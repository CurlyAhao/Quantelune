"""Conversation tools backed by the ordinary project/analysis services."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from sqlalchemy import select

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.models.dataset import UploadStatus
from app.models.ml import MLExperiment, MLRun
from app.models.statistics import StatisticalRun
from app.models.user import User
from app.services import analysis_jobs, dataset_service
from app.services import project_assistant_tasks as task_bindings


def json_text(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def dataset_metadata(dataset):
    fields = json.loads(dataset.schema_json or "[]")
    meta = json.loads(dataset.preview_json or "{}")
    history = meta.get("preparation", {})
    last = (history.get("steps") or [{}])[-1]
    recipe = last.get("recipe", {})
    return {
        "id": str(dataset.id),
        "name": dataset.display_name or dataset.original_filename,
        "prepared_from": meta.get("source_dataset_id"),
        "join": {
            "left_id": history.get("parent_id"),
            "right_id": recipe.get("right_dataset_id"),
            "left_key": recipe.get("column"),
            "right_key": recipe.get("right_key"),
            "counts": last.get("join_counts"),
        }
        if recipe.get("operation") == "join"
        else None,
        "sha256": dataset.sha256,
        "rows": dataset.row_count,
        "columns": dataset.column_count,
        "ready": dataset.upload_status == UploadStatus.READY,
        "schema": [
            {
                "name": item.get("name"),
                "type": item.get(
                    "polars_type",
                    item.get(
                        "physical_type", item.get("dtype", item.get("type", item.get("data_type")))
                    ),
                ),
                "inferred_type": item.get("inferred_type"),
            }
            for item in fields
            if isinstance(item, dict)
        ],
        "multimodal": dataset.multimodal,
    }


async def project_context(live, *, dataset_name="", project_catalog=False):
    async with async_session_factory() as db:
        rows = await dataset_service.list_project_datasets(db, live.project, live.owner)
        task = live.value.get("task_context")
        references = []
        if task:
            from app.preview.workspace import load_record

            user = await db.get(User, live.owner)
            for identity in (
                task.get("analysis_ids", [])
                + task.get("figure_ids", [])
                + task.get("plot_run_ids", [])
            ):
                row = await load_record(db, uuid.UUID(identity), user, live.project)
                payload = json.loads(row.payload_json)
                references.append(
                    {
                        "id": str(row.id),
                        "kind": row.kind,
                        "title": payload.get("title"),
                        "scope": payload.get("scope"),
                        "status": payload.get("status"),
                    }
                )
        from app.services.project_assistant import material_selection

        # Only disclose a project item's label when it is in this task, under
        # selection, or explicitly named in the user's current question.
        question = next(
            (
                message["content"].casefold()
                for message in reversed(live.value.get("messages", []))
                if message["role"] == "user"
            ),
            "",
        )
        visible = {live.value.get("dataset_id")}
        if task:
            visible.update(task.get("dataset_ids", []))
            visible.add(task.get("dataset_id"))
        for reference in references:
            visible.add((reference.get("scope") or {}).get("dataset_id"))
        name = dataset_name.strip().casefold()
        lookup = bool(name and name in question)
        # A whole-project inventory is available on demand in an unscoped project chat.
        catalog = (
            project_catalog
            and not task
            and not live.value.get("dataset_id")
            and (live.value.get("context_key", "project") == "project")
        )
        scoped_rows = [
            row
            for row in rows
            if catalog
            or str(row.id) in visible
            or (
                lookup
                and any(
                    label and label.casefold() == name
                    for label in (row.original_filename, row.display_name)
                )
            )
        ]
        active = next((item for item in rows if str(item.id) == live.value.get("dataset_id")), None)
        material_measurement = None
        if live.value.get("material_action"):
            from app.services.project_assistant import latest_material_result

            material = active
            if material is not None:
                saved = await latest_material_result(
                    db, live.owner, live.project, material, live.value["context_key"]
                )
                if saved and saved.get("material_facts"):
                    material_measurement = {
                        "measurement": saved["material_facts"],
                        "results": [
                            {"kind": ref["kind"], "id": ref["id"], "title": ref["title"]}
                            for ref in saved["messages"][-1]["results"]
                            if ref["kind"] in {"dataset", "plot", "figure"}
                        ],
                    }
        return {
            "project_id": str(live.project),
            "current_figure": live.value.get("current_figure"),
            "current_dataset_id": live.value["dataset_id"],
            "current_material_measurement": material_measurement,
            "current_selection": material_selection(
                active, live.value.get("context_key") or "project"
            )
            if active
            else {},
            "research_task": task,
            "material_action": live.value.get("material_action", False),
            "task_results": references,
            "datasets": [
                dataset_metadata(item)
                if str(item.id) == live.value.get("dataset_id")
                else {
                    "id": str(item.id),
                    "name": item.display_name or item.original_filename,
                    "type": item.file_format,
                    "rows": item.row_count,
                    "columns": item.column_count,
                    "collection": bool(item.media_case),
                }
                for item in scoped_rows
            ],
        }


async def selected_dataset(db, live, identity):
    identity = identity or live.value["dataset_id"]
    if not identity:
        raise StatForgeError(
            422, "ASSISTANT_DATASET_REQUIRED", "项目有多份数据，请根据用户问题选择对应数据。"
        )
    row = await dataset_service.get_dataset_with_access(db, uuid.UUID(identity), live.owner)
    if row.project_id != live.project or not (
        row.upload_status == UploadStatus.READY
        or (row.raw_file and row.upload_status == UploadStatus.UPLOADED)
    ):
        raise StatForgeError(
            422, "ASSISTANT_DATASET_REQUIRED", "这份数据不属于当前项目或尚未导入完成。"
        )
    return row


def attach_result(live, kind, identity, title, dataset_id=None, workspace_record_id=None):
    route = {
        "statistics": "statistics",
        "ml": "machine-learning",
        "profile": "profiling",
        "dataset": "data",
        "figure": "workspace",
        "plot": "plots",
        "material": "workspace",
    }[kind]
    value = {
        "kind": kind,
        "id": str(identity),
        "title": title,
        "href": f"/projects/{live.project}/{route}?datasetId={dataset_id}&runId={identity}",
    }
    if workspace_record_id:
        value["workspace_record_id"] = str(workspace_record_id)
    if live.value.get("task_context"):
        value["task_id"] = live.value["task_context"]["id"]
    results = live.value["messages"][-1]["results"]
    if not any(item["kind"] == kind and item["id"] == str(identity) for item in results):
        results.append(value)
    live.checkpoint()
    return value


async def result_facts(live, kind, identity):
    from app.routers import ml, statistics

    async with async_session_factory() as db:
        user = await db.get(User, live.owner)
        if kind == "statistics":
            native = await db.get(StatisticalRun, uuid.UUID(identity))
            if native is None or native.project_id != live.project:
                raise StatForgeError(404, "RESULT_NOT_FOUND", "当前项目中没有这个结果。")
            response = await statistics.get_statistical_run(uuid.UUID(identity), db, user)
            value = response.model_dump(mode="json")
            result = value.get("result") or {}
            # No residual/prediction rows, raw records, local paths or arbitrary artifacts.
            facts = {
                key: result.get(key)
                for key in (
                    "method",
                    "status",
                    "sample",
                    "estimates",
                    "statistic",
                    "p_value",
                    "confidence_interval",
                    "effect_size",
                    "descriptive_statistics",
                    "assumption_checks",
                    "warnings",
                    "limitations",
                )
            }
            record_id = None
            if value["status"] == "completed":
                dataset = await selected_dataset(db, live, str(native.dataset_id))
                record_id = await task_bindings.analysis(
                    db, live, user, dataset, kind, identity, response.input_config, response.method
                )
            reference = attach_result(
                live, kind, identity, response.method, native.dataset_id, record_id
            )
            config = response.input_config or {}
            matching = []
            for figure_id in (live.value.get("task_context") or {}).get("figure_ids", []):
                from app.preview.workspace import load_record

                figure = await load_record(db, uuid.UUID(str(figure_id)), user, live.project)
                spec = json.loads(figure.payload_json)
                if figure.kind == "figure" and spec.get("scope", {}).get(
                    "statistical_run_id"
                ) == str(identity):
                    matching.append(
                        attach_result(live, "figure", figure.id, spec["title"], native.dataset_id)
                    )
            return {
                "run_id": identity,
                "status": value["status"],
                "method": response.method,
                "variables": config.get("variables", []),
                "group_variable": config.get("group_variable"),
                "facts": facts,
                "result": reference,
                "figures": matching,
            }
        native = await db.get(MLRun, uuid.UUID(identity))
        experiment = await db.get(MLExperiment, native.experiment_id) if native else None
        if not experiment or experiment.project_id != live.project:
            raise StatForgeError(404, "RESULT_NOT_FOUND", "当前项目中没有这个模型结果。")
        response = await ml.get_run(uuid.UUID(identity), db, user)
        value = response.model_dump(mode="json")
        record_id = None
        if value["status"] == "completed":
            dataset = await selected_dataset(db, live, str(experiment.dataset_id))
            record_id = await task_bindings.analysis(
                db,
                live,
                user,
                dataset,
                "ml",
                identity,
                json.loads(experiment.config_json),
                experiment.name,
            )
        reference = attach_result(
            live, "ml", identity, experiment.name, experiment.dataset_id, record_id
        )
        models = [
            {
                key: model.get(key)
                for key in (
                    "model_id",
                    "evaluation_protocol_id",
                    "status",
                    "cv",
                    "validation_metrics",
                    "test_metrics",
                    "overfit_gap",
                    "training_seconds",
                )
            }
            for model in value["models"]
        ]
        return {
            "run_id": identity,
            "status": value["status"],
            "validation_strategy": value["validation_strategy"],
            "n_observations": value["n_observations"],
            "models": models,
            "comparison": value["comparison"],
            "leakage_checks": [
                {key: check.get(key) for key in ("code", "severity", "passed", "rule_id")}
                for check in value["leakage_checks"]
            ],
            "result": reference,
        }


async def wait_job(live, identity, kind):
    live.job_id = identity
    phases = {
        "queued": "正在等待计算…",
        "prepare": "正在准备数据…",
        "compute": "正在计算…",
        "publish": "正在保存结果…",
    }
    while True:
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            record = await (
                analysis_jobs.cancel(db, identity, user)
                if live.cancelled.is_set()
                else analysis_jobs.get(db, identity, user)
            )
        value = record["payload"]
        if value["status"] not in analysis_jobs.ACTIVE:
            live.job_id = None
            if value["status"] in {"completed", "not_applicable"} and value.get("native_id"):
                facts = await result_facts(live, kind, value["native_id"])
                live.check()
                return facts
            live.check()
            return {
                "status": value["status"],
                "error": value.get("error_message") or "计算未完成，请调整方法或字段。",
            }
        live.progress(phases.get(value["phase"], "正在计算…"))
        await asyncio.sleep(0.35)


def tools_for(live):
    async def inspect_project(dataset_name: str = "", project_catalog: bool = False) -> str:
        """Inspect current task/material. To compare another material, pass its exact name
        from the user request. Set project_catalog only in a project-wide chat when the user
        asks for a list of project datasets.
        """
        return json_text(
            await project_context(live, dataset_name=dataset_name, project_catalog=project_catalog)
        )

    async def inspect_dataset(dataset_id: str = "") -> str:
        """Compute/reuse an actual data overview and numeric/missingness summaries for a
        project dataset.
        """
        from app.routers.profiling import create_profile

        live.progress("正在查看数据概况…")
        async with async_session_factory() as db:
            dataset = await selected_dataset(db, live, dataset_id)
            user = await db.get(User, live.owner)
            response = await create_profile(dataset.id, False, db, user)
            await db.commit()
            result = response.result or {}
            columns = [
                {
                    key: item.get(key)
                    for key in (
                        "name",
                        "physical_type",
                        "inferred_type",
                        "null_count",
                        "null_rate",
                        "unique_count",
                        "numeric_stats",
                        "warnings",
                    )
                }
                for item in result.get("columns", [])
            ]
            for column in columns:
                # Plot diagnostics also contain individual outlier observations; the
                # assistant needs scalar summaries, not those source observations.
                column["numeric_stats"] = {
                    key: value
                    for key, value in (column["numeric_stats"] or {}).items()
                    if value is None or isinstance(value, (int, float))
                }
            metadata = dataset_metadata(dataset)
            joined = metadata["join"]
            if joined:
                # Older saved joins have the immutable recipe but no stored match counts.
                if "right_rows" not in (joined["counts"] or {}):
                    from app.schemas.preparation import Preparation
                    from app.services import preparation

                    step = json.loads(dataset.preview_json)["preparation"]["steps"][-1]
                    preview = await preparation.prepare(
                        db,
                        live.owner,
                        uuid.UUID(joined["left_id"]),
                        Preparation.model_validate(step["recipe"]),
                    )
                    if not preview["blocked"] and preview["result_sha256"] == dataset.sha256:
                        joined["counts"] = {
                            key: preview["join"][key]
                            for key in (
                                "left_rows",
                                "right_rows",
                                "matched",
                                "unmatched",
                                "duplicate_keys",
                            )
                        }
                attach_result(live, "dataset", dataset.id, "关联表", dataset.id)
            reference = attach_result(live, "profile", response.id, "数据概况", dataset.id)
            return json_text(
                {
                    "dataset": metadata,
                    "overview": result.get("overview"),
                    "columns": columns,
                    "missingness": result.get("missingness"),
                    "result": reference,
                }
            )

    async def analysis_methods() -> str:
        """Get the real statistical methods, required fields, ML models and split
        strategies. Use before selecting a method.
        """
        from app.schemas.ml import MLExperimentCreateRequest, build_metadata
        from app.schemas.statistics import StatisticalRunRequest
        from app.statistics.registry import METHOD_REGISTRY

        return json_text(
            {
                "statistics": METHOD_REGISTRY.describe(),
                "machine_learning": build_metadata().model_dump(mode="json"),
                "statistics_configuration": StatisticalRunRequest.model_json_schema(),
                "ml_configuration": MLExperimentCreateRequest.model_json_schema(),
            }
        )

    async def run_statistics(configuration: dict[str, Any], dataset_id: str = "") -> str:
        """Execute a real local statistical analysis and save its result. configuration
        requires method_id and variables (field names); grouping tests also need
        group_variable; regression also needs target. Optional confidence_level
        defaults .95, alternative two_sided, missing_value_policy listwise_delete.
        Call analysis_methods for valid methods. Never invent data or run IDs.
        """
        from app.schemas.statistics import StatisticalRunRequest

        request = StatisticalRunRequest.model_validate(configuration)
        live.progress("正在准备统计分析…")
        async with async_session_factory() as db:
            dataset = await selected_dataset(db, live, dataset_id)
            user = await db.get(User, live.owner)
            job = await analysis_jobs.start(
                db, user, dataset, "statistics", uuid.uuid4(), request.model_dump(mode="json")
            )
        return json_text(await wait_job(live, uuid.UUID(job["id"]), "statistics"))

    async def run_machine_learning(configuration: dict[str, Any], dataset_id: str = "") -> str:
        """Train and evaluate real local ML models. configuration requires task_type
        (binary_classification/multiclass_classification/regression), target,
        features, model_ids and a suitable split_strategy. Optional name,
        categorical_features, group_variable, time_variable, n_splits, test_size,
        random_seed, standardize. Use analysis_methods. Exclude labels/IDs/split
        columns from features; respect repeated subjects and existing train/test
        boundaries. Clarify scientifically ambiguous targets or splits before
        fitting.
        """
        from app.routers.ml import create_experiment
        from app.schemas.ml import MLExperimentCreateRequest

        request = MLExperimentCreateRequest.model_validate(configuration)
        live.progress("正在准备模型…")

        async def cancelled():
            return live.cancelled.is_set()

        async with async_session_factory() as db:
            dataset = await selected_dataset(db, live, dataset_id)
            user = await db.get(User, live.owner)
            response = await create_experiment(
                dataset.id, request, db, user, cancel_check=cancelled, commit_result=True
            )
            live.check()
            experiment = await db.get(MLExperiment, response.id)
            job = await analysis_jobs.start(
                db,
                user,
                dataset,
                "ml",
                uuid.uuid4(),
                json.loads(experiment.config_json),
                experiment,
            )
        return json_text(await wait_job(live, uuid.UUID(job["id"]), "ml"))

    async def list_results() -> str:
        """List recent actual statistics and ML runs in this project, including runs
        from manual analysis or earlier conversations.
        """
        async with async_session_factory() as db:
            stats = (
                await db.scalars(
                    select(StatisticalRun)
                    .where(StatisticalRun.project_id == live.project)
                    .order_by(StatisticalRun.created_at.desc())
                    .limit(30)
                )
            ).all()
            models = (
                await db.scalars(
                    select(MLRun)
                    .join(MLExperiment)
                    .where(MLExperiment.project_id == live.project)
                    .order_by(MLRun.created_at.desc())
                    .limit(30)
                )
            ).all()
            return json_text(
                {
                    "statistics": [
                        {
                            "id": str(row.id),
                            "dataset_id": str(row.dataset_id),
                            "method": row.method,
                            "status": row.status.value,
                            "created_at": row.created_at,
                        }
                        for row in stats
                    ],
                    "ml": [
                        {
                            "id": str(row.id),
                            "experiment_id": str(row.experiment_id),
                            "status": row.status.value,
                            "created_at": row.created_at,
                        }
                        for row in models
                    ],
                }
            )

    async def read_result(kind: str, run_id: str) -> str:
        """Read real saved result facts. kind is statistics or ml, run_id must come from
        a tool result or list_results.
        """
        if kind not in {"statistics", "ml"}:
            return json_text({"error": "结果类型应为statistics或ml。"})
        return json_text(await result_facts(live, kind, run_id))

    from app.research.project_data_tools import data_tools

    definitions = [
        inspect_project,
        inspect_dataset,
        analysis_methods,
        run_statistics,
        run_machine_learning,
        list_results,
        read_result,
        *data_tools(live),
    ]
    result = []
    for function in definitions:
        tool = StructuredTool.from_function(coroutine=function)
        original = tool.coroutine

        async def guarded(_function=original, _name=tool.name, **kwargs):
            async with live.tool_lock:
                live.check()
                key = _name + json.dumps(kwargs, sort_keys=True, default=str)
                mutating = (
                    _name.startswith(("run_", "create_", "style_", "revise_"))
                    or _name == "prepare_data"
                )
                if mutating and key in live.tool_cache:
                    return live.tool_cache[key]
                try:
                    value = await _function(**kwargs)
                    live.check()
                    try:
                        details = json.loads(value) if isinstance(value, str) else {}
                    except ValueError:
                        details = {}
                    live.value["messages"][-1].setdefault("used_tools", []).append(
                        {
                            "name": _name,
                            "status": details.get(
                                "status", "error" if details.get("error") else "returned"
                            )
                            if isinstance(details, dict)
                            else "returned",
                            "result_ids": [
                                r["id"] for r in live.value["messages"][-1].get("results", [])
                            ],
                        }
                    )
                    live.checkpoint()
                    if mutating:
                        live.tool_cache[key] = value
                    return value
                except asyncio.CancelledError:
                    raise
                except (ValidationError, ValueError):
                    return json_text(
                        {
                            ("error"): (
                                "方法或参数格式不适用。请先读取analysis_methods，使用真"
                                "实字段后修正；必要时简短询问用户。"
                            )
                        }
                    )
                except StatForgeError as exc:
                    return json_text({"error_code": exc.error_code, "error": exc.detail})
                except Exception:
                    return json_text(
                        {"error": "这项操作未完成。请核对数据状态和方法；不要声称计算成功。"}
                    )

        tool.coroutine = guarded
        result.append(tool)
    return result
