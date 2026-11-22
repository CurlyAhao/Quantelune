"""Assistant adapters for the existing preparation and plotting services."""

import asyncio
import json
import uuid
from typing import Any, Literal

from app.database import async_session_factory
from app.exceptions import StatForgeError
from app.models.user import User
from app.plotting.acoustic_summary import (
    ACOUSTIC_SUMMARY_FIELDS,
    acoustic_summary_program,
    mixed_unit_bar_list,
)
from app.preview.workspace import load_record, save_record
from app.preview.write_fence import acquire_write_fence
from app.schemas.workspace import FigureSpec, GenerateFigureData, PlotDraft, PlotRun, SaveRecord
from app.services import dataset_service
from app.services import project_assistant_tasks as tasks


def media_run_facts(project, run):
    """Read only bounded, non-content facts from a completed run's own output."""

    from app.services import local_workspaces
    from app.services.material_run_facts import measurement_range, read_run_file

    root = local_workspaces.project_root(project)
    if root is None or run.get("status") != "completed":
        return None
    try:
        identity = uuid.UUID(str(run["id"]))
        output = local_workspaces.checked_path(
            root / ".statforge" / "storage" / "media-runs" / identity.hex
        )
        index = json.loads(read_run_file(output, "index.json").read_text(encoding="utf-8-sig"))
        params = json.loads(
            read_run_file(output, "parameters.json").read_text(encoding="utf-8-sig")
        )
        entries = []
        for entry in index.get("entries", [])[:50]:
            speech = entry.get("speech_rate") or {}
            entries.append(
                {
                    "sample_id": (entry.get("sample") or {}).get("sample_id"),
                    **measurement_range(entry, params),
                    "speech_rate": {
                        key: speech.get(key)
                        for key in ("unit", "count_source", "duration_seconds", "text_scope")
                    },
                }
            )
        return {"executed_steps": params.get("executed_steps", []), "measurements": entries}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def data_tools(live):
    from app.research.project_chat_tools import (
        attach_result,
        dataset_metadata,
        json_text,
        selected_dataset,
    )

    async def data_operations() -> str:
        "Read the existing data preparation operations and exact parameter schema before cleaning."
        from app.schemas.preparation import Preparation
        from app.services.preparation import LABELS, UNITS

        return json_text(
            {
                "operations": LABELS,
                "parameters": Preparation.model_json_schema(),
                "automatic": ["source_sha256", "right_sha256", "result_sha256"],
                "units": list(UNITS),
                "cast_targets": ["text", "integer", "number", "boolean"],
                "dates": (
                    "value is the source date format; default accepts YYYY-MM-DD/"
                    "YYYY/MM/DD/YYYY.MM.DD"
                ),
                "join": (
                    "left join; column is left key, right_key is right key; dupli"
                    "cate defaults block"
                ),
                "feature_processing": (
                    "run_machine_learning supports existing fold-local imputation"
                    ", scaling, categorical encoding, outlier handling and featur"
                    "e selection (see analysis_methods). Do not fit transforms on"
                    " all train/test data. No arbitrary new-column expressions or"
                    " persistent fitted feature pipeline are supported."
                ),
                "versions": (
                    "Every prepare_data call retains source and saves an immutabl"
                    "e derived dataset in the current task."
                ),
            }
        )

    async def prepare_source_table(
        dataset_id: str, sheet_name: str | None = None, csv_encoding: str = "auto"
    ) -> str:
        """Open a project CSV, JSON records, Parquet or Excel as an analysis table.
        Reuse exact bytes and import settings. With several sheets, returns names so the user can
        choose.
        """
        from app.routers.project_sources import PrepareSource, prepare_source, source_sheets

        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            source = await selected_dataset(db, live, dataset_id)
            if not source.raw_file:
                return json_text({"status": "ready", "dataset": dataset_metadata(source)})
            if source.file_format not in {"csv", "xlsx", "parquet", "json"}:
                return json_text(
                    {
                        "status": "unsupported",
                        "reason": "请选择 CSV、Excel、JSON 记录表或 Parquet 表格。",
                    }
                )
            if source.file_format == "xlsx" and not sheet_name:
                sheets = (await source_sheets(source.id, db, user))["sheet_names"]
                if len(sheets) != 1:
                    return json_text({"status": "needs_input", "sheet_names": sheets})
                sheet_name = sheets[0]
            ready = await prepare_source(
                source.id, PrepareSource(sheet_name=sheet_name, csv_encoding=csv_encoding), db, user
            )
            await tasks.append(db, user, live, "dataset_ids", ready.id, source_dataset=source.id)
            await db.commit()
            return json_text(
                {
                    "status": "ready",
                    "dataset": dataset_metadata(ready),
                    "result": attach_result(
                        live, "dataset", ready.id, ready.original_filename, ready.id
                    ),
                }
            )

    async def prepare_data(configuration: dict[str, Any], dataset_id: str = "") -> str:
        """Apply one existing cleaning/field preparation step and save a derived data
        version. Use data_operations first. Supply operation and required
        column/rule/value/target/right_dataset_id/right_key; hashes are filled
        automatically. No source overwrite.
        """
        from app.schemas.preparation import Preparation
        from app.services import preparation

        live.progress("正在处理数据…")
        async with async_session_factory() as db:
            dataset = await selected_dataset(db, live, dataset_id)
            source_id = dataset.id
            values = {**configuration, "source_sha256": dataset.sha256, "result_sha256": None}
            if values.get("right_dataset_id"):
                right = await selected_dataset(db, live, values["right_dataset_id"])
                values["right_sha256"] = right.sha256
            body = Preparation.model_validate(values)
            preview = await preparation.prepare(db, live.owner, source_id, body)
            live.check()
            if preview["blocked"]:
                joined = preview.get("join") or {}
                return json_text(
                    {
                        "status": "not_applied",
                        "reason": "关联键不唯一，请确认重复键的处理规则。",
                        "duplicate_keys": joined.get("duplicate_keys"),
                    }
                )
            join = preview.get("join") or {}
            body.result_sha256 = preview["result_sha256"]
            await acquire_write_fence(db, live.owner)
            derived = await preparation.prepare(db, live.owner, source_id, body, apply=True)
            user = await db.get(User, live.owner)
            await tasks.append(db, user, live, "dataset_ids", derived.id, source_dataset=source_id)
            await db.commit()
            live.value["dataset_id"] = str(derived.id)
            reference = attach_result(
                live, "dataset", derived.id, derived.original_filename, derived.id
            )
            # Summaries only: preparation previews contain raw sample rows.
            return json_text(
                {
                    "status": "completed",
                    "dataset": dataset_metadata(derived),
                    "join": {
                        key: join[key]
                        for key in (
                            "left_rows",
                            "right_rows",
                            "matched",
                            "unmatched",
                            "duplicate_keys",
                        )
                        if key in join
                    },
                    "summary": {
                        key: preview.get(key)
                        for key in (
                            "label",
                            "before_rows",
                            "after_rows",
                            "affected_rows",
                            "removed_rows",
                            "schema_changes",
                            "notes",
                        )
                    },
                    "result": reference,
                }
            )

    async def plotting_capabilities() -> str:
        """Read existing plotting APIs and style options; do this before generating a chart."""
        from app.plotting.program import PYPLOT, RC_KEYS, SEABORN

        return json_text(
            {
                "result_figures": {
                    "descriptive": "distribution",
                    "pearson/spearman": "correlation_scatter",
                    "linear_regression": "ols_coefficients",
                    "welch_t": "welch_groups_difference",
                },
                "result_figure_styles": {
                    "theme": ["research", "paper"],
                    "font_size": "8–16 (legacy Welch 10–32)",
                    "width": "600–4000",
                    "height": "400–3000",
                    "dpi": "72–600",
                    "caption": "text",
                    "unit": "text",
                    "title": "text",
                    "layers": (
                        "existing chart layers only; preserves the saved projection a"
                        "nd statistical Run"
                    ),
                },
                "acoustic_summary": (
                    "Use create_acoustic_summary_plot for saved audio feature sum"
                    "maries: each metric is shown separately in its original unit"
                    "s; never combine unlike measures on a shared numeric axis or"
                    " apply arbitrary scaling."
                ),
                "scientific_plot": {
                    "data": (
                        "df is a pandas DataFrame of the selected actual fields; do n"
                        "ot create example or synthetic data"
                    ),
                    "pyplot": sorted(PYPLOT),
                    "seaborn": sorted(SEABORN),
                    "rcParams": sorted(RC_KEYS),
                    "imports": (
                        "matplotlib.pyplot as plt, seaborn as sns, numpy as np, pandas as pd"
                    ),
                    "rules": (
                        "Use one Figure with optional subplots. Finite registered API"
                        " interpreter; no filesystem, network, arbitrary imports/func"
                        "tions, exec or shell. Return real SVG/PNG/PDF."
                    ),
                    "style": (
                        "Use style_plot to adjust a saved rendered figure without rec"
                        "omputing data or statistics. For gaps between existing bars use "
                        "bar_width_scale (e.g. 0.88). For changes requiring different plotting "
                        "instructions, use read_figure then revise_plot on the selected run."
                    ),
                },
            }
        )

    async def create_result_figure(
        run_id: str, fields: list[str] | None = None, title: str = "分析图表", bins: int = 12
    ) -> str:
        """Create an editable task FigureSpec from an actual completed statistical Run.
        Chart type follows its method. Reuses the saved result; does not run
        statistical analysis again.
        """
        from app.preview.figure_data import generate
        from app.routers.statistics import get_statistical_run

        live.progress("正在生成图表…")
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            result = await get_statistical_run(uuid.UUID(run_id), db, user)
            dataset = await selected_dataset(db, live, str(result.dataset_id))
            if (
                result.project_id != live.project
                or result.status.value != "completed"
                or not result.result
            ):
                raise StatForgeError(
                    422, "FIGURE_RESULT_REQUIRED", "请选择当前项目中已完成的统计结果。"
                )
            config = result.input_config or {}
            chart = {
                "descriptive": "distribution",
                "pearson": "correlation_scatter",
                "spearman": "correlation_scatter",
                "linear_regression": "ols_coefficients",
                "welch_t": "welch_groups_difference",
            }.get(result.method)
            if not chart:
                return json_text(
                    {
                        "status": "unsupported",
                        "reason": "该结果暂无现成结果图。\
可按实际字段使用 create_plot，不能编造统计投影。",
                    }
                )
            chosen = fields or (
                [config["target"]]
                if chart == "ols_coefficients"
                else config.get("variables", [])[: 2 if chart == "correlation_scatter" else 1]
            )
            bound = tasks.scope(
                dataset,
                [
                    *config.get("variables", []),
                    *config.get("categorical_variables", []),
                    *(config.get("reference_categories") or {}),
                    config.get("target"),
                    config.get("group_variable"),
                ],
            )
            bound.statistical_run_id = result.id
            if chart == "welch_groups_difference":
                spec = FigureSpec(
                    title=title,
                    scope=bound,
                    fields=config.get("variables", []),
                    group_order=list(result.result.descriptive_statistics),
                )
            else:
                snapshot = await generate(
                    db,
                    user,
                    GenerateFigureData(
                        scope=bound,
                        chart_type=chart,
                        fields=chosen,
                        bins=bins,
                        request_id=str(uuid.uuid4()),
                    ),
                )
                data = snapshot["payload"]
                layers = {
                    "distribution": (
                        ["box"] if data["data"].get("groups") else ["histogram", "box"]
                    ),
                    "correlation_scatter": ["points"],
                    "ols_coefficients": ["coefficients", "coefficient_ci"],
                }[chart]
                if chart == "ols_coefficients" and not data["data"].get("ci_available"):
                    layers = ["coefficients"]
                spec = FigureSpec(
                    schema_version=2,
                    title=title,
                    scope=data["scope"],
                    chart_type=chart,
                    renderer_version="workspace-figure-v2",
                    fields=chosen,
                    layers=layers,
                    font_size=10,
                    height=min(3000, max(800, 320 + 70 * len(data["data"].get("groups", [])))),
                    interval="coefficient_ci" if "coefficient_ci" in layers else "none",
                    snapshot_id=snapshot["id"],
                    content_sha256=data["content_sha256"],
                )
            await acquire_write_fence(db, user.id)
            saved = await save_record(
                db, user, SaveRecord(project_id=live.project, payload=spec), commit=False
            )
            await tasks.append(db, user, live, "figure_ids", uuid.UUID(saved["id"]))
            await db.commit()
            return json_text(
                {
                    "status": "completed",
                    "result": attach_result(live, "figure", saved["id"], spec.title, dataset.id),
                }
            )

    async def read_figure(figure_id: str) -> str:
        """Read an actual saved FigureSpec or scientific PlotRun, its source, code and
        styling options. IDs come from task references/tool outputs.
        """
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            row = await load_record(db, uuid.UUID(figure_id), user, live.project)
            if row.kind == "figure":
                return json_text(
                    {
                        "id": str(row.id),
                        "figure": FigureSpec.model_validate_json(row.payload_json).model_dump(
                            mode="json"
                        ),
                    }
                )
            if row.kind == "plot_run":
                run = PlotRun.model_validate_json(row.payload_json)
                from app.plotting.program import split_styles
                from app.services import plots

                replay, _ = await plots.replay_code(db, live.project, run, user)
                base_code, styles = split_styles(replay)
                return json_text(
                    {
                        "id": str(row.id),
                        "kind": row.kind,
                        "status": run.status,
                        "title": run.title,
                        "scope": run.scope.model_dump(mode="json"),
                        "code": base_code,
                        "applied_styles": [item["style"] for item in styles],
                        "revision": row.revision,
                        "dpi": run.dpi,
                        "colors": run.metadata.get("colors", []),
                        "style": run.metadata.get("style"),
                        "style_available": "figure_state" in run.artifacts,
                    }
                )
            return json_text({"status": "unsupported", "reason": "请选择图表记录。"})

    async def style_figure(figure_id: str, style: dict[str, Any]) -> str:
        """Save a new version of an existing result FigureSpec with only supplied style
        options. Allowed: theme, width, height, dpi, font_size, title, caption;
        version 2 supports accent_color, tick_digits, line_weight, axis_label and
        margin_scale. Does not recompute statistics or data projection, and keeps the
        old figure.
        """
        allowed = {
            "theme",
            "width",
            "height",
            "dpi",
            "font_size",
            "title",
            "caption",
            "accent_color",
            "tick_digits",
            "line_weight",
            "axis_label",
            "margin_scale",
        }
        if not style or set(style) - allowed:
            return json_text({"status": "unsupported", "allowed": sorted(allowed)})
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            row = await load_record(db, uuid.UUID(figure_id), user, live.project)
            if row.kind != "figure":
                return json_text({"status": "unsupported", "reason": "自由绘图请使用 style_plot。"})
            old = FigureSpec.model_validate_json(row.payload_json)
            changes = dict(style)
            if old.schema_version == 2 and "layers" in changes:
                changes["interval"] = (
                    "coefficient_ci" if "coefficient_ci" in changes["layers"] else "none"
                )
            spec = FigureSpec.model_validate({**old.model_dump(), **changes})
            await acquire_write_fence(db, user.id)
            saved = await save_record(
                db, user, SaveRecord(project_id=live.project, payload=spec), commit=False
            )
            await tasks.append(db, user, live, "figure_ids", uuid.UUID(saved["id"]))
            await db.commit()
            return json_text(
                {
                    "status": "completed",
                    "source_figure_id": figure_id,
                    "result": attach_result(
                        live, "figure", saved["id"], spec.title, spec.scope.dataset_id
                    ),
                }
            )

    async def wait_plot(identity):
        from app.services import plots

        live.plot_job_id = identity
        while True:
            async with async_session_factory() as db:
                user = await db.get(User, live.owner)
                record = await (
                    plots.cancel(db, live.project, identity, user)
                    if live.cancelled.is_set()
                    else plots.get_run(db, live.project, identity, user)
                )
                await db.commit()
            run = PlotRun.model_validate(record["payload"])
            if run.status not in plots.ACTIVE:
                live.plot_job_id = None
                if run.status == "completed":
                    await tasks.attach_record(live, "plot_run_ids", identity)
                    reference = attach_result(
                        live, "plot", identity, run.title, run.scope.dataset_id
                    )
                    live.check()
                    return json_text(
                        {
                            "status": "completed",
                            "result": reference,
                            "formats": ["svg", "png", "pdf"],
                        }
                    )
                live.check()
                return json_text({"status": run.status, "error": run.error_message})
            live.progress("正在绘图…")
            await asyncio.sleep(0.35)

    async def create_plot(
        code: str, columns: list[str], title: str = "科研图表", dataset_id: str = "", dpi: int = 150
    ) -> str:
        """Render a real chart through the existing finite Matplotlib/Seaborn plotting
        service. df contains selected actual columns. Use df["column"],
        plt.subplots(), ax.scatter or ax.plot. Standard plotting aliases are
        available. Use plotting_capabilities first; never synthesize data. Saves
        editable code and SVG/PNG/PDF to the research task.
        """
        from app.plotting.program import parse
        from app.services import plots

        parse(code)
        async with async_session_factory() as db:
            dataset = await selected_dataset(db, live, dataset_id)
            if (dataset.media_result or {}).get("kind") == "summary_csv" and mixed_unit_bar_list(
                code
            ):
                return json_text(
                    {
                        "status": "needs_revision",
                        "reason": (
                            "不同单位的声学指标不能合并为同一柱状图；\
请使用 create_acoustic_summary_plot 分别展示，或按"
                            "明确研究目的绘制可解释的比较。"
                        ),
                    }
                )
            user = await db.get(User, live.owner)
            draft = PlotDraft(title=title, scope=tasks.scope(dataset, columns), code=code, dpi=dpi)
            saved = await save_record(db, user, SaveRecord(project_id=live.project, payload=draft))
            live.check()
            started = await plots.start(
                db, live.project, uuid.UUID(saved["id"]), saved["revision"], uuid.uuid4(), user
            )
        return await wait_plot(uuid.UUID(started["id"]))

    async def create_acoustic_summary_plot(
        dataset_id: str = "", title: str = "声学指标概览", dpi: int = 150
    ) -> str:
        """Plot available saved acoustic summary measures separately in original units.
        Use the summary dataset ID returned by extract_media_features; no extraction
        or model rerun. Saves an editable new plot and SVG/PNG/PDF while retaining
        previous plots.
        """
        async with async_session_factory() as db:
            dataset = await selected_dataset(db, live, dataset_id)
            names = {item["name"] for item in dataset_metadata(dataset)["schema"]}
            columns = [name for name in ACOUSTIC_SUMMARY_FIELDS if name in names]
            if "speech_rate_per_second" in columns and "speech_count_unit" in names:
                columns.append("speech_count_unit")
            rows = dataset.row_count
            identity = str(dataset.id)
        if not columns:
            return json_text(
                {"status": "unsupported", "reason": "该数据集没有可绘制的声学摘要字段。"}
            )
        return await create_plot(
            acoustic_summary_program(columns, rows, title), columns, title, identity, dpi
        )

    async def revise_plot(
        plot_run_id: str,
        code: str,
        columns: list[str] | None = None,
        title: str | None = None,
        keep_styles: bool = True,
    ) -> str:
        """Render an edited version of an existing scientific plot from its actual dataset.
        Read the original first. Supply the complete revised plotting program; preserve its
        fields, binning and transforms unless the request changes them. Omit columns to
        keep the original inputs. Existing style edits carry forward unless incompatible
        with the requested chart. For appearance alone prefer style_plot.
        """
        from app.plotting.program import STYLE_MARKER, parse, split_styles
        from app.services import plots

        base_code, _ = split_styles(code)
        parse(base_code)
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            source = await plots.get_run(db, live.project, uuid.UUID(plot_run_id), user)
            original = PlotRun.model_validate(source["payload"])
            if source.get("archived") or original.status != "completed":
                return json_text({"error": "请选择当前项目内已完成的可编辑图形。"})
            dataset = await selected_dataset(db, live, str(original.scope.dataset_id))
            if (dataset.media_result or {}).get("kind") == "summary_csv" and mixed_unit_bar_list(
                base_code
            ):
                return json_text({"error": "请为不同单位的指标使用各自的坐标轴。"})
            replay, _ = await plots.replay_code(db, live.project, original, user)
            if keep_styles and STYLE_MARKER in replay:
                base_code += STYLE_MARKER + replay.split(STYLE_MARKER, 1)[1]
            scope = (
                original.scope
                if columns is None
                else original.scope.model_copy(update={"columns": columns})
            )
            draft = PlotDraft(
                title=title or original.title, scope=scope, code=base_code, dpi=original.dpi
            )
            saved = await save_record(
                db, user, SaveRecord(project_id=live.project, payload=draft), commit=False
            )
            live.check()
            started = await plots.start(
                db,
                live.project,
                uuid.UUID(saved["id"]),
                saved["revision"],
                uuid.uuid4(),
                user,
                lineage={
                    "revision_parent_id": str(source["id"]),
                    "style_root_draft_id": original.metadata.get(
                        "style_root_draft_id", str(original.draft_id)
                    ),
                },
            )
        return await wait_plot(uuid.UUID(started["id"]))

    async def style_plot(plot_run_id: str, style: dict[str, Any]) -> str:
        """Restyle the saved rendered scientific plot without re-running source
        code/statistics. Options: colors (old-color:new-color), font_family,
        font_size, width_inches, height_inches, title, xlabel, ylabel, grid,
        tight_layout, dpi, line_width and bar_width_scale. A bar_width_scale such as
        0.88 narrows the rendered bars around their centers relative to their current
        widths, leaving gaps without changing counts or bins.
        For colors, first read_figure and use an actual rendered color from its colors list,
        not the code's input color (Seaborn can desaturate it). Keeps old plot and saves
        a new task version.
        """
        from app.schemas.plot_style import PlotStyle
        from app.services import plots

        patch = PlotStyle.model_validate(style)
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            started = await plots.start_style(
                db, live.project, uuid.UUID(plot_run_id), patch, uuid.uuid4(), user
            )
        return await wait_plot(uuid.UUID(started["id"]))

    async def package_signal_windows(
        dataset_id: str | None = None,
        action: Literal["select", "package"] = "package",
        start: float | None = None,
        end: float | None = None,
        window_seconds: float | None = None,
        step_seconds: float | None = None,
        window_index: int | None = None,
        timezone: str | None = None,
    ) -> str:
        """Apply a signal view or package its windows using the same service as the
        manual view. HAPT/BIDMC start and end are relative seconds; PhysioCGM uses
        UTC Unix seconds with its recorded timezone. HAPT accepts window_seconds and
        step_seconds in 0.02s increments, BIDMC accepts positive window_seconds and
        step_seconds (default step equals window); PhysioCGM uses its CGM windows and
        accepts neither. window_index selects an existing packaged window, not its
        duration. Never infer a timezone or transmit raw samples.
        """

        import anyio

        from app.services import bidmc, hapt, physiocgm

        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            row = await selected_dataset(db, live, dataset_id)
            if not row.signal_session:
                return json_text(
                    {"status": "unsupported", "reason": "请选择原始信号会话或分段 PKL。"}
                )
            task_id = (live.value.get("task_context") or {}).get("id")
            saved = (
                json.loads(row.preview_json or "{}")
                .get("signal_options", {})
                .get(task_id or "project")
            )
            changes = {
                k: v
                for k, v in {
                    "start": start,
                    "end": end,
                    "window": window_index,
                    "timezone": timezone,
                }.items()
                if v is not None
            }
            if not saved and not changes and window_seconds is None and step_seconds is None:
                return json_text(
                    {"status": "needs_input", "reason": "请指定选段或先在数据中打开会话。"}
                )
            base = {**(saved or {}), **changes, "task_id": task_id}
            if window_index is not None and start is None and end is None:
                base.update(start=None, end=None)
            options = physiocgm.Options.model_validate(base)
            info = await anyio.to_thread.run_sync(physiocgm.source_info, row, options)
            schema = info["schema"]
            if window_index is not None and not info["packaged"]:
                return json_text({"status": "unsupported", "reason": "窗口索引仅用于已分段文件。"})
            if info["packaged"] and (window_seconds is not None or step_seconds is not None):
                return json_text(
                    {
                        "status": "unsupported",
                        "reason": "已分段文件不能重新设置窗口长度，请打开原会话。",
                    }
                )
            if schema == hapt.SCHEMA:
                if window_seconds is not None:
                    base["hapt_window_seconds"] = window_seconds
                if step_seconds is not None:
                    base["hapt_step_seconds"] = step_seconds
            elif schema == bidmc.SCHEMA:
                if window_seconds is not None:
                    base["window_seconds"] = window_seconds
                if step_seconds is not None:
                    base["step_seconds"] = step_seconds
            elif window_seconds is not None or step_seconds is not None:
                return json_text(
                    {
                        "status": "unsupported",
                        "reason": "PhysioCGM 使用 CGM 匹配窗口，不支持固定窗或步长。",
                    }
                )
            options = physiocgm.Options.model_validate(base)
            live.progress("正在应用信号选段…")
            view, applied = await physiocgm.apply_view(db, user, row, options)
            description = {
                "kind": view.get("kind", "physiocgm"),
                "selection": view["selection"],
                "window_count": len(view["windows"]),
                "settings": applied,
            }
            if action == "select":
                return json_text({"status": "applied", **description})
            live.progress("正在分段打包…")
            result = await physiocgm.package(
                db, user, row, physiocgm.Options.model_validate(applied)
            )
            await db.commit()
            refs = [
                attach_result(live, "dataset", result[key], title, result[key])
                for key, title in (("package_id", "原始信号窗口 PKL"), ("summary_id", "窗口摘要"))
            ]
            return json_text(
                {
                    "status": "completed",
                    **description,
                    "window_count": result["windows"],
                    "results": refs,
                }
            )

    async def media_operations() -> str:
        """Read supported composable media steps and exact parameters before processing.
        MFCC/RMS require audio extraction; ResNet requires frames. No
        transcription/OCR or arbitrary code. File association is not synchronization.
        """
        from app.schemas.media_runs import MediaAssociation, MediaRequest

        return json_text(
            {
                "parameters": MediaRequest.model_json_schema(),
                "association": MediaAssociation.model_json_schema(),
                "formats": {"video": ["mp4"], "audio": ["wav"], "text": ["UTF-8 txt", "md"]},
                "defaults": {
                    "media": ["extract_audio", "mfcc", "rms", "frames"],
                    "ravdess": "legacy complete preset",
                    "coco": "legacy complete preset",
                },
                "reuse": (
                    "Matching files, parameters and tools reuse a completed resul"
                    "t. force=true explicitly recomputes without overwriting old "
                    "results."
                ),
                "alignment": (
                    "Independent WAV keeps its own time origin. Association and e"
                    "qual relative seconds do not establish precise synchronizati"
                    "on."
                ),
                "pause_measurement": "Not provided by the available media tools.",
            }
        )

    async def associate_media_files(samples: list[dict[str, Any]]) -> str:
        """Associate current-project file IDs: video_id with optional audio_id/text_id,
        or a standalone WAV audio_id with optional text_id. First inspect project
        files; do not invent IDs or assume uncertain filename pairings. Save an
        immutable association with the same manual service, then use
        extract_media_features.
        """
        from app.schemas.media_runs import MediaAssociation
        from app.services import multimodal_cases

        body = MediaAssociation(
            samples=samples, task_id=(live.value.get("task_context") or {}).get("id")
        )
        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            anchor = await dataset_service.get_dataset_with_access(
                db, body.samples[0].video_id or body.samples[0].audio_id, live.owner
            )
            if anchor.project_id != live.project:
                raise StatForgeError(422, "MEDIA_PROJECT", "请从当前项目选择材料。")
            result = await multimodal_cases.associate(db, user, anchor.id, body)
            return json_text(
                {
                    "status": "saved",
                    "dataset_id": result["id"],
                    "result": attach_result(
                        live, "dataset", result["id"], "媒体关联", result["id"]
                    ),
                }
            )

    async def inspect_media_samples(dataset_id: str = "") -> str:
        """List saved ordinary-media/RAVDESS/COCO sample IDs, file associations, current
        selection and actual runs. No raw media transmission.
        """
        from app.services import multimodal_cases

        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            dataset = await selected_dataset(db, live, dataset_id)
            if dataset.media_result:
                dataset = await selected_dataset(db, live, dataset.media_result["collection_id"])
            context_key = (live.value.get("task_context") or {}).get("id", "project")
            data = await multimodal_cases.view(db, user, dataset.id, context_key)
            # Dataset metadata/labels only. The tools never send pixels, waveform arrays or
            # transcripts.
            return json_text(
                {
                    "dataset_id": str(dataset.id),
                    "kind": data["kind"],
                    "selection": data["selection"],
                    "samples": [
                        {
                            k: row[k]
                            for k in (
                                "sample_id",
                                "title",
                                "actor_id",
                                "emotion",
                                "image_id",
                                "video_id",
                                "audio_id",
                                "text_id",
                                "audio_alignment",
                            )
                            if k in row
                        }
                        for row in data["samples"]
                    ],
                    "runs": [
                        {
                            **{
                                k: run.get(k)
                                for k in (
                                    "id",
                                    "status",
                                    "progress",
                                    "summary_id",
                                    "package_id",
                                    "error_message",
                                )
                            },
                            "parameters": {
                                k: v
                                for k, v in run.get("parameters", {}).items()
                                if k not in {"case", "input_files", "signature"}
                            },
                            "measured_facts": media_run_facts(live.project, run),
                        }
                        for run in data["runs"]
                    ],
                }
            )

    async def extract_media_features(
        dataset_id: str = "",
        sample_ids: list[str] | None = None,
        start: float | None = None,
        end: float | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> str:
        """Run the same local FFmpeg/librosa/Praat/ResNet18/MiniLM feature service as
        the manual button. Use inspect_media_samples to select explicit IDs (e.g.
        actor 01). Full arrays and a separate summary are saved into the current
        task. None sample_ids follows saved selection; [] explicitly processes all.
        The saved processing draft applies unless configuration overrides it. The
        saved time range applies only when exactly the current sample is selected,
        including an explicit matching ID; start/end override it and use video-player
        seconds. Never invent results; failed processing returns its real error.
        """
        from app.schemas.media_runs import MediaRequest
        from app.services import multimodal_cases

        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            dataset = await selected_dataset(db, live, dataset_id)
            if dataset.media_result:
                dataset = await selected_dataset(db, live, dataset.media_result["collection_id"])
            task_id = (live.value.get("task_context") or {}).get("id")
            saved = multimodal_cases.saved_selection(dataset, task_id or "project")
            explicit = dict(configuration or {})
            if sample_ids is None:
                if not saved.get("sample_id"):
                    return json_text(
                        {"status": "needs_input", "reason": "请选择一个样本，或指定要处理的样本。"}
                    )
                sample_ids = [saved["sample_id"]]
            current_sample = len(sample_ids) == 1 and sample_ids[0] == saved.get("sample_id")
            # The manual workbench saves its processing draft independently of the range.
            draft = dict(saved.get("processing") or {})
            if (
                not saved.get("processing")
                and dataset.media_case
                and dataset.media_case.get("kind") == "media"
            ):
                view = await multimodal_cases.view(db, user, dataset.id, task_id or "project")
                last = next(
                    (
                        run
                        for run in view["runs"]
                        if run.get("dataset_id") == str(dataset.id)
                        and isinstance(run.get("parameters", {}).get("steps"), list)
                    ),
                    None,
                )
                if last:
                    draft = {
                        key: value
                        for key, value in last["parameters"].items()
                        if key in MediaRequest.model_fields
                        and key not in {"task_id", "sample_ids", "start", "end", "force"}
                    }
            if saved.get("audio_source"):
                draft["audio_source"] = saved["audio_source"]
            options = {**draft, **explicit}
            selected_start = (
                start
                if start is not None
                else explicit.get("start")
                if "start" in explicit
                else saved.get("start")
                if current_sample
                else None
            )
            selected_end = (
                end
                if end is not None
                else explicit.get("end")
                if "end" in explicit
                else saved.get("end")
                if current_sample
                else None
            )
            if "speech_count" not in explicit and options.get("speech_count") is not None:
                count_for = multimodal_cases.speech_count_scope(
                    dataset.id,
                    sample_ids[0] if len(sample_ids) == 1 else None,
                    selected_start,
                    selected_end,
                    options,
                )
                if saved.get("speech_count_for") != count_for:
                    options.pop("speech_count")
            result = await multimodal_cases.start(
                db,
                user,
                dataset.id,
                MediaRequest(
                    **{
                        **options,
                        "task_id": task_id,
                        "sample_ids": sample_ids,
                        "start": selected_start,
                        "end": selected_end,
                    }
                ),
            )
            identity = uuid.UUID(result["id"])
            dataset_identity = dataset.id
        while result["status"] in {"queued", "running"}:
            live.check()
            live.progress(result["progress"] or "正在本地提取特征…")
            await asyncio.sleep(1)
            async with async_session_factory() as db:
                user = await db.get(User, live.owner)
                dataset = await selected_dataset(db, live, str(dataset_identity))
                result = await multimodal_cases.get_run(db, user, dataset, identity)
        if result["status"] != "completed":
            return json_text(
                {
                    "status": result["status"],
                    "error": result["error_message"],
                    "run_id": result["id"],
                }
            )
        refs = [
            attach_result(live, "dataset", result[key], title, result[key])
            for key, title in [("summary_id", "特征摘要"), ("package_id", "完整特征")]
        ]
        return json_text(
            {
                "status": "completed",
                "run_id": result["id"],
                "results": refs,
                "reused": result.get("reused", False),
                "parameters": {
                    k: v
                    for k, v in result["parameters"].items()
                    if k not in {"case", "input_files"}
                },
                "measured_facts": media_run_facts(live.project, result),
                "next": (
                    "Use the summary dataset for statistics/plots. Original sampl"
                    "e links remain in each summary row."
                ),
            }
        )

    async def locate_media_sample(dataset_id: str, sample_id: str) -> str:
        """Save an explicit original sample selection in the current task and return its
        real dataset reference. Use an actual row sample_id (for example maximum
        energy from saved statistics), never infer IDs from names.
        """

        from app.services import multimodal_cases

        async with async_session_factory() as db:
            user = await db.get(User, live.owner)
            row = await selected_dataset(db, live, dataset_id)
            if row.media_result:
                row = await selected_dataset(db, live, row.media_result["collection_id"])
            source, root, descriptor = await multimodal_cases.collection(db, user, row.id)
            if sample_id not in {
                s["sample_id"] for s in multimodal_cases.sample_rows(root, descriptor)
            }:
                return json_text({"status": "not_found", "sample_id": sample_id})
            task_id = (live.value.get("task_context") or {}).get("id", "project")
            await acquire_write_fence(db, user.id)
            meta = multimodal_cases.read_metadata(source)
            meta.setdefault("sample_selection", {})[task_id] = {"sample_id": sample_id}
            source.preview_json = json.dumps(meta)
            await db.commit()
            return json_text(
                {
                    "sample_id": sample_id,
                    "result": attach_result(live, "dataset", str(row.id), "原始样本", str(row.id)),
                }
            )

    from app.research.transcription_tools import transcription_tools
    from app.research.visual_tools import visual_tools

    return [
        *visual_tools(live),
        *transcription_tools(live),
        data_operations,
        prepare_source_table,
        prepare_data,
        plotting_capabilities,
        create_result_figure,
        read_figure,
        style_figure,
        create_plot,
        create_acoustic_summary_plot,
        revise_plot,
        style_plot,
        package_signal_windows,
        media_operations,
        associate_media_files,
        inspect_media_samples,
        extract_media_features,
        locate_media_sample,
    ]
