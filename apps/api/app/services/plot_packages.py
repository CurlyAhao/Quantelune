"""Explicit, owned code/data exports; packaging never executes user code."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import tempfile
import zipfile
from pathlib import Path

from app.ml.resources import WorkerLimits
from app.ml.worker_control import MLTaskScope, run_ml_worker
from app.plotting.worker import FORMATS, verify_data
from app.preview.context import load_dataset, verify_storage_version
from app.schemas.workspace import PlotRun
from app.services import plots, storage_service
from app.services.workflow_service import check_project_access

LIBRARIES = ("matplotlib", "seaborn", "numpy", "pandas", "polars", "pyarrow", "pillow")

README = """# 绘图代码与数据

此包只包含该次运行所选字段，不包含账户或模型连接。
原始输入文件的 SHA 与 data.parquet 的 SHA 分开记录在 manifest.json；后者是所选字段的本地副本。

1. 解压到一个新文件夹。
2. 用独立 Python 环境安装 requirements.txt：python -m pip install -r requirements.txt
3. 在该文件夹运行：python run.py

figure.py 是绘图代码及可编辑的有序样式注释；renderer.py 是有限绘图解释器，支持的语法与软件内一致。
请保留样式注释的位置和格式；它们由解释器按顺序应用，不能作为独立 Python 脚本运行。
运行器不会连接 Quantelune 或网络，也不会覆盖原图。
新结果和 reproduction.json 写入新的 output-* 文件夹；报告逐项比较 SVG、PNG、PDF 的字节哈希。
退出码 2 表示图像与原图存在差异，不能当作已精确重现。
环境安装需要自行联网或使用自己的离线依赖缓存。

若要修改图形，编辑 figure.py 后运行 python run.py --edited。
数据与渲染器仍需通过哈希校验，结果仍另存；修改后的结果不能当作原始运行。

字体使用 Microsoft YaHei（微软雅黑）。操作系统、字体及依赖的底层构建不同，可能导致图形字节不同。
包不包含字体许可或字体文件。库版本已锁定，Python 版本及原始运行参数在 manifest.json。
这不是跨平台逐字节一致的保证。

渲染器来源：{origin}
"""


async def export(db, project, identity, user, cancel_check):
    record = await plots.get_run(db, project, identity, user)
    run = PlotRun.model_validate(record["payload"])
    if run.status != "completed":
        raise plots.failure("NOT_READY", "这次绘图还没有可导出的结果。")
    dataset = await load_dataset(db, run.scope, user)
    await verify_storage_version(dataset)
    source_key, fmt, sheet, encoding = (
        dataset.object_key,
        dataset.file_format,
        dataset.sheet_name,
        dataset.csv_encoding,
    )
    await db.commit()
    async with MLTaskScope(WorkerLimits(wall_seconds=90), cancel_check) as task:
        with tempfile.TemporaryDirectory(prefix="sf-plot-package-") as directory:
            source = Path(directory) / ("source." + fmt)
            storage_service.download_to_path(source_key, str(source))
            config = {"columns": run.scope.columns}
            response = await run_ml_worker(
                str(source),
                fmt,
                config,
                run.scope.dataset_sha256,
                operation="plot_data",
                sheet_name=sheet,
                csv_encoding=encoding,
                task=task,
            )
            data, data_meta = verify_data(response, config, run.scope.dataset_sha256)
            expected = run.metadata.get("selected_data_sha256")
            if expected and expected != data_meta["sha256"]:
                raise plots.failure(
                    "DATA_VERSION_CHANGED", "数据读取环境已变化，请重新运行后导出代码与数据。"
                )
            if data_meta["input_rows"] != run.metadata.get("input_rows"):
                raise plots.failure(
                    "DATA_VERSION_CHANGED", "数据行数与原运行不同，请重新运行后导出。"
                )
            plotting = Path(__file__).parents[1] / "plotting"
            code, _ = await plots.replay_code(db, project, run, user)
            files = {
                "figure.py": code.encode("utf-8"),
                "data.parquet": data,
                "run.py": (plotting / "portable_runner.py").read_bytes(),
            }
            # Legacy styled runs captured a renderer without style replay support.
            legacy_style = code != run.code
            captured = "renderer" in run.artifacts and not legacy_style
            if captured:
                files["renderer.py"], _ = await plots.artifact(
                    db, project, identity, "renderer", user
                )
            else:
                files["renderer.py"] = (plotting / "program.py").read_bytes()
            for extension in FORMATS:
                await task.check()
                files["original/figure." + extension], _ = await plots.artifact(
                    db, project, identity, extension, user
                )
            versions = {name: importlib.metadata.version(name) for name in LIBRARIES}
            versions.update(
                {
                    name: value
                    for name, value in run.metadata.get("software_versions", {}).items()
                    if name in LIBRARIES
                }
            )
            files["requirements.txt"] = (
                "\n".join(f"{name}=={version}" for name, version in versions.items()) + "\n"
            ).encode()
            origin = "saved_run" if captured else "current_renderer_for_legacy_run"
            note = (
                "随原始运行保存的渲染器。"
                if captured
                else "旧记录未保存当时的渲染器；此包提供当前渲染器。"
                "原图保留供比较，不承诺还原旧版渲染行为。"
            )
            files["README.md"] = README.format(origin=note).encode("utf-8")
            manifest = {
                "schema_version": 1,
                "run_id": str(identity),
                "run_revision": record["revision"],
                "title": run.title,
                "completed_at": run.completed_at,
                "source_sha256": run.scope.dataset_sha256,
                "schema_sha256": run.scope.schema_sha256,
                "columns": run.scope.columns,
                "input_rows": data_meta["input_rows"],
                "dpi": run.dpi,
                "source_format": fmt,
                "sheet_name": sheet,
                "csv_encoding": encoding,
                "renderer_origin": origin,
                "selected_data_matches_saved_run": bool(expected),
                "python_version": run.metadata.get("python_version"),
                "required_versions": versions,
                "randomness": run.metadata.get("randomness"),
                "files": {
                    name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                    for name, value in files.items()
                },
            }
            files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode(
                "utf-8"
            )
            target = Path(directory) / "package.zip"
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
                for name, value in files.items():
                    await task.check()
                    bundle.writestr(name, value)
            await check_project_access(db, project, user)
            await task.check()
            return target.read_bytes()
