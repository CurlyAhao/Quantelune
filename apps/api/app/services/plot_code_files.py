"""Owned local IDE files. Existing files are retained when application code changes."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.exceptions import StatForgeError
from app.ml.resources import WorkerLimits
from app.ml.worker_control import run_ml_worker
from app.models.workspace import WorkspaceRecord
from app.preview.context import digest, load_dataset, verify_storage_version
from app.preview.workspace import credential_free, load_record, save_record
from app.preview.write_fence import acquire_write_fence
from app.research.output_handles import directories, lexical, opened
from app.schemas.workspace import PlotCodeFile, PlotDraft, SaveRecord
from app.services import plots, storage_service

MAX_CODE_FILE = 140000


def failure(code, text, status=409):
    return StatForgeError(status, "PLOT_CODE_" + code, text)


def local_only():
    if (
        settings.storage_backend != "local"
        or settings.db_backend != "sqlite"
        or not settings.app_data_path
    ):
        raise failure("UNAVAILABLE", "本机代码文件暂时无法使用。", 503)


def code_sha(code):
    return hashlib.sha256(code.replace("\r\n", "\n").encode()).hexdigest()


def scope_sha(draft):
    return digest(
        {
            key: draft.scope.model_dump(mode="json").get(key)
            for key in ("project_id", "dataset_id", "dataset_sha256", "schema_sha256", "columns")
        }
    )


def identity(project, draft, user):
    return uuid.uuid5(
        user.id,
        f"plot-code-file:{project}:{draft.id}:{scope_sha(PlotDraft.model_validate_json(draft.payload_json))}",
    )


@contextmanager
def folder(project, draft, scope, user):
    local_only()
    current = lexical(Path(settings.app_data_path))
    with ExitStack() as stack:
        stack.enter_context(directories(current))
        context = uuid.uuid5(user.id, f"plot-code-directory:{project}:{draft}:{scope}")
        for part in ("code", user.id.hex, context.hex):
            child = current / part
            try:
                child.mkdir(mode=0o700)
            except FileExistsError:
                pass
            stack.enter_context(directories(child))
            current = child
        yield current


def read(path, limit=MAX_CODE_FILE):
    try:
        with opened(path) as stream:
            value = stream.read(limit + 1) if limit is not None else stream.read()
            if limit is not None and len(value) > limit:
                raise failure("FILE_TOO_LARGE", "代码文件过大，请将绘图代码控制在32000字以内。")
            return value
    except StatForgeError:
        raise
    except Exception:
        raise failure("FILE_UNAVAILABLE", "代码文件无法读取，可以另存应用代码。") from None


def create(path, content):
    with opened(path, write=True, create=True) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def envelope(binding):
    prefix = (
        "# -*- coding: utf-8 -*-\n# StatForge: local data copy\n"
        "from pathlib import Path\nimport pandas as pd\nimport matplotlib.pyplot as plt\n"
        f'df = pd.read_parquet(Path(__file__).with_name("{binding.data_filename}"))\n'
        "# <statforge:code>\n"
    )
    suffix = (
        '\n# </statforge:code>\nif __name__ == "__main__":\n'
        "    for _figure_id in plt.get_fignums():\n"
        f"        plt.figure(_figure_id).set_dpi({binding.dpi})\n"
        "    plt.show()\n"
    )
    return prefix, suffix


def inspect(path, binding, draft):
    content = read(path / binding.filename)
    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeError:
        raise failure("ENCODING", "请将代码文件保存为UTF-8后重新读取。") from None
    prefix, suffix = envelope(binding)
    if not text.startswith(prefix) or not text.endswith(suffix):
        raise failure(
            "FILE_STRUCTURE", "数据读取段或末尾预览段已更改。可另存应用代码，保留当前文件。"
        )
    body = text[len(prefix) : -len(suffix)]
    if len(body) > 32000:
        raise failure("FILE_TOO_LARGE", "绘图代码超过32000字，请缩短后重新读取。")
    data = read(path / binding.data_filename, None)
    if hashlib.sha256(data).hexdigest() != binding.data_sha256:
        raise failure("DATA_CHANGED", "本地数据副本已更改，请另存应用代码和数据。")
    sha = hashlib.sha256(content).hexdigest()
    external = sha != binding.file_sha256
    application = code_sha(draft.code) != binding.code_sha256 or draft.dpi != binding.dpi
    equal = code_sha(body) == code_sha(draft.code) and draft.dpi == binding.dpi
    return {
        "code": body,
        "file_sha256": sha,
        "external_changed": external,
        "application_changed": application,
        "conflict": external and application and not equal,
        "file_path": str(path / binding.filename),
        "data_path": str(path / binding.data_filename),
        "base_revision": binding.draft_revision,
        "dpi": binding.dpi,
    }


def profile_folders():
    if os.name != "nt":
        return {}
    import ctypes

    result = {}
    for key, identifier in (("USERPROFILE", 0x28), ("APPDATA", 0x1A), ("LOCALAPPDATA", 0x1C)):
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, identifier, None, 0, buffer) == 0:
            result[key] = buffer.value
    return result


def editor_candidates():
    result = {}
    for key, command, executable, label, directory_name in (
        ("vscode", "code", "Code.exe", "Visual Studio Code", "Microsoft VS Code"),
        ("cursor", "cursor", "Cursor.exe", "Cursor", "cursor"),
        (
            "vscode-insiders",
            "code-insiders",
            "Code - Insiders.exe",
            "Visual Studio Code Insiders",
            "Microsoft VS Code Insiders",
        ),
    ):
        found = shutil.which(command)
        candidates = []
        if found:
            selected = Path(found)
            candidates += [selected.parent / executable, selected.parent.parent / executable]
        for parent in (
            os.environ.get("LOCALAPPDATA"),
            profile_folders().get("LOCALAPPDATA"),
            os.environ.get("ProgramFiles"),
            os.environ.get("SYSTEMDRIVE", "C:") + "/Program Files",
        ):
            if parent:
                candidates += [
                    Path(parent) / "Programs" / directory_name / executable,
                    Path(parent) / directory_name / executable,
                ]
        path = next(
            (value for value in candidates if value.is_file() and not value.is_symlink()), None
        )
        if path:
            result[key] = (label, path)
    if os.name == "nt":
        for parent in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if parent and (path := Path(parent) / "Notepad++" / "notepad++.exe").is_file():
                result["notepadpp"] = ("Notepad++", path)
    return result


def editors():
    local_only()
    found = editor_candidates()
    entries = [{"id": key, "name": value[0]} for key, value in found.items()]
    if os.name == "nt":
        entries.append({"id": "system", "name": "系统编辑器"})
    return {
        "editors": entries,
        "default_editor": next(iter(found), "system"),
        "native_launch": settings.desktop_mode,
    }


def launch(path, selected):
    if os.name != "nt":
        raise failure("EDITOR_UNAVAILABLE", "请在Windows本机打开代码编辑器。", 503)
    if selected == "system":
        try:
            # 'open' for .py can run Python. Only the registered edit verb is used.
            os.startfile(str(path), "edit")
        except OSError:
            raise failure(
                "EDITOR_UNAVAILABLE", "系统未配置编辑器，请选择其他编辑器。", 503
            ) from None
        return {"editor": "system", "launched": True}
    candidate = editor_candidates().get(selected)
    if not candidate:
        raise failure("EDITOR_UNAVAILABLE", "没有找到所选编辑器，请重新选择。", 503)
    environment = {
        key: os.environ[key]
        for key in (
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "PATH",
            "PATHEXT",
            "COMSPEC",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
            "TEMP",
            "TMP",
            "HOMEDRIVE",
            "HOMEPATH",
        )
        if key in os.environ
    }
    # The isolated source preview deliberately omits profile variables. Restore
    # only ordinary Windows known-folder values for the interactive editor.
    for key, value in profile_folders().items():
        environment.setdefault(key, value)
    if "USERPROFILE" in environment:
        profile = Path(environment["USERPROFILE"])
        environment.setdefault("HOMEDRIVE", profile.drive)
        environment.setdefault("HOMEPATH", str(profile)[len(profile.drive) :])
    try:
        child = subprocess.Popen(
            [str(candidate[1]), str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            close_fds=True,
            creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB,
        )
    except OSError:
        raise failure("EDITOR_UNAVAILABLE", "编辑器未能打开，请确认程序仍可使用。", 503) from None
    return {"editor": selected, "launched": True, "launcher_pid": child.pid}


async def draft_and_binding(db, project, draft_id, user):
    local_only()
    row = await load_record(db, draft_id, user, project)
    if row.kind != "plot":
        raise failure("NOT_FOUND", "没有找到这份图形代码。", 404)
    draft = PlotDraft.model_validate_json(row.payload_json)
    binding_id = identity(project, row, user)
    record = await db.get(WorkspaceRecord, binding_id)
    if record and (
        record.user_id != user.id or record.project_id != project or record.kind != "plot_code_file"
    ):
        raise failure("NOT_FOUND", "没有找到这份代码文件。", 404)
    binding = PlotCodeFile.model_validate_json(record.payload_json) if record else None
    return row, draft, binding_id, binding


async def status(db, project, draft_id, user):
    row, draft, _identity, binding = await draft_and_binding(db, project, draft_id, user)
    if binding is None:
        return {"available": False}
    with folder(project, draft_id, scope_sha(draft), user) as path:
        value = inspect(path, binding, draft)
    await credential_free(db, user, value["code"])
    return {"available": True, "draft_revision": row.revision, **value}


async def open_file(db, project, draft_id, revision, selected, as_new, user):
    row, draft, binding_id, binding = await draft_and_binding(db, project, draft_id, user)
    if row.revision != revision:
        raise failure("DRAFT_CHANGED", "应用代码已有修改，请先保存后打开。")
    if selected not in {entry["id"] for entry in editors()["editors"]}:
        raise failure("EDITOR_UNAVAILABLE", "请选择可用的编辑器。", 422)
    await credential_free(db, user, draft.model_dump(mode="json"))
    dataset = await load_dataset(db, draft.scope, user)
    await verify_storage_version(dataset)
    source_key, fmt, sheet, encoding = (
        dataset.object_key,
        dataset.file_format,
        dataset.sheet_name,
        dataset.csv_encoding,
    )
    needs_data = binding is None or as_new
    # Release a read transaction before the bounded worker; publication rechecks revision.
    await db.commit()
    data, data_meta = None, None
    if needs_data:
        from app.plotting.worker import verify_data

        with tempfile.TemporaryDirectory(prefix="sf-code-data-") as directory:
            source = Path(directory) / ("source." + fmt)
            storage_service.download_to_path(source_key, str(source))
            config = {"columns": draft.scope.columns}
            response = await run_ml_worker(
                str(source),
                fmt,
                config,
                draft.scope.dataset_sha256,
                operation="plot_data",
                sheet_name=sheet,
                csv_encoding=encoding,
                limits=WorkerLimits(wall_seconds=90),
            )
            data, data_meta = verify_data(response, config, draft.scope.dataset_sha256)
    await acquire_write_fence(db, user.id)
    row, current, binding_id, existing = await draft_and_binding(db, project, draft_id, user)
    if row.revision != revision or scope_sha(current) != scope_sha(draft):
        raise failure("DRAFT_CHANGED", "应用代码已有修改，请重新打开。")
    with folder(project, draft_id, scope_sha(current), user) as path:
        binding = existing
        inspection = inspect(path, binding, current) if binding and not as_new else None
        replace = (
            as_new
            or not binding
            or (inspection["application_changed"] and not inspection["external_changed"])
        )
        if replace:
            if data is None:
                # The original checked local data copy can be retained for new code.
                data = read(path / binding.data_filename, None)
                data_meta = {"sha256": binding.data_sha256}
            name = "data-" + data_meta["sha256"] + ".parquet"
            data_path = path / name
            if data_path.exists():
                if hashlib.sha256(read(data_path, None)).hexdigest() != data_meta["sha256"]:
                    # Keep edited local data as well as the user's old code file.
                    name = f"data-{data_meta['sha256']}-{uuid.uuid4().hex}.parquet"
                    data_path = path / name
                    create(data_path, data)
            else:
                create(data_path, data)
            binding = PlotCodeFile(
                title=current.title,
                draft_id=draft_id,
                scope_sha256=scope_sha(current),
                filename=f"plot-{uuid.uuid4().hex}.py",
                file_sha256="0" * 64,
                code_sha256=code_sha(current.code),
                data_filename=name,
                data_sha256=data_meta["sha256"],
                draft_revision=revision,
                dpi=current.dpi,
            )
            prefix, suffix = envelope(binding)
            content = (prefix + current.code.replace("\r\n", "\n") + suffix).encode()
            create(path / binding.filename, content)
            binding.file_sha256 = hashlib.sha256(content).hexdigest()
            await plots.put(db, user, project, binding, binding_id)
            inspection = inspect(path, binding, current)
        await db.commit()
        file_path = path / binding.filename
        # Packaged Desktop delegates to its main process outside the API Job.
        launched = (
            {"launched": False, "native_launch_required": True}
            if settings.desktop_mode
            else launch(file_path, selected)
        )
    return {"available": True, "draft_revision": revision, **inspection, **launched}


async def import_file(db, project, draft_id, revision, expected_file_sha, replace, user):
    await acquire_write_fence(db, user.id)
    row, draft, binding_id, binding = await draft_and_binding(db, project, draft_id, user)
    if not binding:
        raise failure("NOT_FOUND", "请先打开这份代码文件。", 404)
    if row.revision != revision:
        raise failure("DRAFT_CHANGED", "应用代码已有修改，请重新读取本地文件。")
    with folder(project, draft_id, scope_sha(draft), user) as path:
        value = inspect(path, binding, draft)
        if value["file_sha256"] != expected_file_sha:
            raise failure("FILE_CHANGED", "本地文件又有修改，请重新读取。")
        different = code_sha(value["code"]) != code_sha(draft.code) or draft.dpi != binding.dpi
        if value["application_changed"] and different and not replace:
            raise failure("CONFLICT", "应用与本地文件都有修改，请选择使用哪份代码。")
        payload = draft.model_copy(update={"code": value["code"], "dpi": binding.dpi})
        saved = await save_record(
            db,
            user,
            SaveRecord(project_id=project, expected_revision=revision, payload=payload),
            draft_id,
            commit=False,
        )
        binding.code_sha256 = code_sha(payload.code)
        binding.file_sha256 = value["file_sha256"]
        binding.draft_revision = saved["revision"]
        await plots.put(db, user, project, binding, binding_id)
        await db.commit()
    return {"record": saved, "file_sha256": value["file_sha256"]}


async def flush_project(db, project, user):
    """Saving from any project page includes external-only edits, never a conflict."""
    rows = (
        await db.scalars(
            select(WorkspaceRecord).where(
                WorkspaceRecord.project_id == project.id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "plot_code_file",
            )
        )
    ).all()
    for record in rows:
        binding = PlotCodeFile.model_validate_json(record.payload_json)
        row = await db.scalar(
            select(WorkspaceRecord).where(
                WorkspaceRecord.id == binding.draft_id,
                WorkspaceRecord.project_id == project.id,
                WorkspaceRecord.user_id == user.id,
                WorkspaceRecord.kind == "plot",
            )
        )
        if row is None:
            continue
        draft = PlotDraft.model_validate_json(row.payload_json)
        if identity(project.id, row, user) != record.id:
            continue
        value = await status(db, project.id, row.id, user)
        if not value["external_changed"]:
            continue
        if value["conflict"]:
            raise failure(
                "CONFLICT", f"“{draft.title}”在应用与本地文件中都有修改，请打开该图形选择。"
            )
        await import_file(db, project.id, row.id, row.revision, value["file_sha256"], False, user)
