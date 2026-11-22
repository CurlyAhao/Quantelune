"""Crash recovery of pre-registered local SQLite desktop Workflow byte outputs.

Only journaled fresh keys are considered. Unknown or changed evidence is retained;
this module never scans storage to infer ownership and never deserializes models.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import settings
from app.ml.bounded_writer import MAX_ARTIFACT_BYTES
from app.models.dataset import Dataset
from app.models.ml import MLExperiment, MLRun, MLRunStatus
from app.models.workflow import Workflow, WorkflowRun
from app.research.migration import MigrationRefusedError
from app.research.output_handles import (
    directories,
    lexical,
    long_path,
    opened,
    rename_opened,
    unlink_opened,
)
from app.research.produced import read_bytes, write_document

MAX_JOURNAL_ENTRIES = 2048
MAX_DOCUMENT_BYTES = 16384
_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ModelOutputOwner:
    """Internal synchronous Workflow binding, never populated from request fields."""

    project_id: uuid.UUID
    workflow_id: uuid.UUID
    run_id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_sha256: str


def enabled() -> bool:
    return (
        settings.desktop_mode
        and settings.db_backend == "sqlite"
        and settings.storage_backend == "local"
    )


def _native(path: Path) -> Path:
    return Path(long_path(lexical(path)))


def _identity(info) -> list[int]:
    return [info.st_dev, info.st_ino]


def _regular_identity(path: Path) -> list[int]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise MigrationRefusedError("Workflow recovery database identity is unknown.")
    return _identity(info)


def _roots() -> tuple[Path, Path, Path]:
    database = _native(Path(settings.sqlite_path))
    storage = _native(Path(settings.local_storage_path))
    journal = database.with_name(database.name + ".workflow-outputs")
    return database, storage, journal


def _child_directory(parent: Path, name: str, stack: ExitStack) -> tuple[Path, int]:
    # Parent is already held. Never resolve a link or use recursive deletion.
    child = parent / name
    try:
        os.mkdir(long_path(child), mode=0o700)
    except FileExistsError:
        pass
    return child, stack.enter_context(directories(child))


def _key_identity(key: str, project_id: str, run_id: str) -> tuple[str, str | None]:
    if not isinstance(key, str) or len(key) > 1024:
        raise MigrationRefusedError("Workflow recovery key is outside its owned scope.")
    parts = key.split("/")
    if len(parts) not in {4, 5} or parts[1] != str(uuid.UUID(project_id)):
        raise MigrationRefusedError("Workflow recovery project scope differs.")
    if parts[0] == "ml-artifacts" and len(parts) == 4:
        if (
            str(uuid.UUID(parts[2])) != parts[2]
            or not parts[3].endswith(".joblib")
            or str(uuid.UUID(parts[3][:-7])) != parts[3][:-7]
        ):
            raise MigrationRefusedError("Workflow model key is unverifiable.")
        return "model", None
    if len(parts) != 5:
        raise MigrationRefusedError("Workflow recovery key component count differs.")
    if parts[0] == "workflow-artifacts":
        if (
            parts[2] != str(uuid.UUID(run_id))
            or not re.fullmatch(r"[0-9a-f]{32}", parts[3])
            or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,160}", parts[4])
            or ".." in parts[4]
        ):
            raise MigrationRefusedError("Workflow artifact key is unverifiable.")
        return "artifact", None
    if parts[0] == "workflow-derived-data":
        dataset_id = str(uuid.UUID(parts[2]))
        if (
            dataset_id != parts[2]
            or not _HEX.fullmatch(parts[3])
            or parts[4] not in {"input.parquet", "provenance.json"}
        ):
            raise MigrationRefusedError("Workflow derived-data key is unverifiable.")
        return "derived", dataset_id
    raise MigrationRefusedError("Workflow recovery namespace is not owned.")


def write_local_output(
    key: str,
    content: bytes,
    *,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    model_source: dict | None = None,
) -> None:
    """Persist ownership before exclusive file creation; do not commit business rows."""
    if (
        not enabled()
        or not isinstance(content, bytes)
        or not 0 < len(content) <= MAX_ARTIFACT_BYTES
    ):
        raise MigrationRefusedError("Workflow local recovery output boundary refused.")
    project, workflow, run = str(project_id), str(workflow_id), str(run_id)
    kind, dataset = _key_identity(key, project, run)
    _model_source(key, kind, model_source)
    database, storage, journal = _roots()
    path = storage.joinpath(*key.split("/"))
    entry_name = hashlib.sha256(key.encode("utf-8")).hexdigest()
    with ExitStack() as stack:
        stack.enter_context(directories(database.parent))
        stack.enter_context(directories(storage))
        journal, _ = _child_directory(database.parent, journal.name, stack)
        if len(list(journal.iterdir())) >= MAX_JOURNAL_ENTRIES:
            raise MigrationRefusedError(
                "Workflow output journal requires bounded startup reconciliation."
            )
        entry = journal / entry_name
        os.mkdir(long_path(entry), mode=0o700)  # A fresh key cannot reuse an old intent.
        stack.enter_context(directories(entry))
        parent = storage
        for name in key.split("/")[:-1]:
            parent, parent_fd = _child_directory(parent, name, stack)
        for candidate in [path, path.with_name(path.name + ".pending")]:
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            raise MigrationRefusedError("Existing Workflow output retained without overwrite.")
        document = {
            "version": 1,
            "key": key,
            "kind": kind,
            "dataset_id": dataset,
            "model_source": model_source,
            "project_id": project,
            "workflow_id": workflow,
            "run_id": run,
            "database_identity": _regular_identity(database),
            "storage_identity": _identity(storage.lstat()),
            "parent_identity": _identity(parent.lstat()),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        write_document(entry / "intent.json", document)
        pending = path.with_name(path.name + ".pending")
        with opened(pending, write=True, create=True) as stream:
            write_document(
                entry / "owned.json", {"file_identity": _identity(os.fstat(stream.fileno()))}
            )
            if stream.write(content) != len(content):
                raise OSError("Workflow output write was incomplete.")
            stream.flush()
            os.fsync(stream.fileno())
            rename_opened(stream, path, parent_fd)
        if os.name == "posix":
            os.fsync(parent_fd)


def _model_source(key: str, kind: str, source: dict | None) -> None:
    if kind != "model":
        if source is not None:
            raise MigrationRefusedError("Unexpected Workflow model ownership fields.")
        return
    if not isinstance(source, dict) or set(source) != {
        "dataset_id",
        "dataset_sha256",
        "experiment_id",
        "ml_run_id",
    }:
        raise MigrationRefusedError("Workflow model ownership is incomplete.")
    for name in ["dataset_id", "experiment_id", "ml_run_id"]:
        if str(uuid.UUID(source[name])) != source[name]:
            raise MigrationRefusedError("Workflow model identity is not canonical.")
    parts = key.split("/")
    if (
        parts[2] != source["experiment_id"]
        or parts[3] != source["ml_run_id"] + ".joblib"
        or not isinstance(source["dataset_sha256"], str)
        or not _HEX.fullmatch(source["dataset_sha256"])
    ):
        raise MigrationRefusedError("Workflow model input or object identity differs.")


def _load(path: Path) -> tuple[dict, bytes]:
    raw = read_bytes(path, MAX_DOCUMENT_BYTES)

    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise MigrationRefusedError("Duplicate Workflow recovery document fields.")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise MigrationRefusedError("Workflow recovery document is not an object.")
    return value, raw


def _validated(
    entry: Path, database: Path, storage: Path
) -> tuple[dict, dict | None, dict[str, bytes]]:
    names = {p.name for p in entry.iterdir()}
    if not names <= {"intent.json", "owned.json"} or "intent.json" not in names:
        raise MigrationRefusedError("Partial or unknown Workflow journal retained.")
    document, raw = _load(entry / "intent.json")
    fields = {
        "version",
        "key",
        "kind",
        "dataset_id",
        "model_source",
        "project_id",
        "workflow_id",
        "run_id",
        "database_identity",
        "storage_identity",
        "parent_identity",
        "bytes",
        "sha256",
    }
    if set(document) != fields or type(document["version"]) is not int or document["version"] != 1:
        raise MigrationRefusedError("Workflow recovery intent version or fields differ.")
    for key in ["project_id", "workflow_id", "run_id"]:
        if str(uuid.UUID(document[key])) != document[key]:
            raise MigrationRefusedError("Workflow recovery identity is not canonical.")
    kind, dataset = _key_identity(document["key"], document["project_id"], document["run_id"])
    _model_source(document["key"], kind, document["model_source"])
    if (
        document["kind"] != kind
        or document["dataset_id"] != dataset
        or entry.name != hashlib.sha256(document["key"].encode("utf-8")).hexdigest()
    ):
        raise MigrationRefusedError("Workflow recovery key binding differs.")
    if (
        type(document["bytes"]) is not int
        or not 0 < document["bytes"] <= MAX_ARTIFACT_BYTES
        or not isinstance(document["sha256"], str)
        or not _HEX.fullmatch(document["sha256"])
    ):
        raise MigrationRefusedError("Workflow recovery byte binding differs.")
    if document["database_identity"] != _regular_identity(database) or document[
        "storage_identity"
    ] != _identity(storage.lstat()):
        raise MigrationRefusedError("Workflow recovery database or storage changed; retained.")
    for key in ["database_identity", "storage_identity", "parent_identity"]:
        value = document[key]
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(type(n) is not int or n < 0 for n in value)
        ):
            raise MigrationRefusedError("Workflow recovery physical identity is unknown.")
    documents = {"intent.json": raw}
    owned = None
    if "owned.json" in names:
        owned, raw = _load(entry / "owned.json")
        if (
            set(owned) != {"file_identity"}
            or not isinstance(owned["file_identity"], list)
            or len(owned["file_identity"]) != 2
            or any(type(n) is not int or n < 0 for n in owned["file_identity"])
        ):
            raise MigrationRefusedError("Workflow created-file identity is unknown.")
        documents["owned.json"] = raw
    return document, owned, documents


async def _referenced(session, document: dict) -> bool:
    key = document["key"]
    run_id = uuid.UUID(document["run_id"])
    # A copied/corrupt reference in a different Run must not be erased merely
    # because the original transaction is absent.
    others = (
        await session.execute(
            select(WorkflowRun.id)
            .where(
                WorkflowRun.id != run_id,
                WorkflowRun.node_results_json.contains(key, autoescape=True),
            )
            .limit(1)
        )
    ).first()
    if others is not None:
        raise MigrationRefusedError("Workflow output has another committed reference; retained.")
    record = (
        await session.execute(
            select(WorkflowRun.workflow_id, WorkflowRun.node_results_json).where(
                WorkflowRun.id == run_id
            )
        )
    ).one_or_none()
    if record is not None:
        project = await session.scalar(
            select(Workflow.project_id).where(Workflow.id == record.workflow_id)
        )
        if (
            str(record.workflow_id) != document["workflow_id"]
            or str(project) != document["project_id"]
        ):
            raise MigrationRefusedError("Committed Workflow recovery owner differs.")
        if len(record.node_results_json or "") > 16 * 1024 * 1024:
            raise MigrationRefusedError("Committed Workflow output references exceed their bound.")
        results = json.loads(record.node_results_json or "{}")
        if document["kind"] == "model":
            return await _model_referenced(session, document, results)
        for node in results.values():
            for artifact in (node.get("output") or {}).get("artifacts", []):
                if artifact.get("object_key") == key:
                    if (
                        artifact.get("bytes") != document["bytes"]
                        or artifact.get("sha256") != document["sha256"]
                    ):
                        raise MigrationRefusedError(
                            "Committed Workflow output byte reference differs."
                        )
                    return True
    if document["kind"] == "model":
        return await _model_referenced(session, document, None)
    if document["kind"] == "derived":
        row = (
            await session.execute(
                select(
                    Dataset.project_id, Dataset.object_key, Dataset.file_size, Dataset.sha256
                ).where(Dataset.id == uuid.UUID(document["dataset_id"]))
            )
        ).one_or_none()
        if row is not None:
            prefix, leaf = key.rsplit("/", 1)
            if (
                str(row.project_id) != document["project_id"]
                or row.object_key != prefix + "/input.parquet"
            ):
                raise MigrationRefusedError("Committed derived-data reference differs.")
            if leaf == "input.parquet" and (
                row.file_size != document["bytes"] or row.sha256 != document["sha256"]
            ):
                raise MigrationRefusedError("Committed derived-data bytes differ.")
            return True
    return False


async def _model_referenced(session, document: dict, results: dict | None) -> bool:
    source = document["model_source"]
    row = (
        await session.execute(
            select(
                MLRun.id,
                MLRun.artifact_key,
                MLRun.status,
                MLRun.artifact_meta_json,
                MLExperiment.id.label("experiment_id"),
                MLExperiment.project_id,
                MLExperiment.dataset_id,
                MLExperiment.dataset_sha256,
            )
            .join(MLExperiment, MLRun.experiment_id == MLExperiment.id)
            .where(
                (MLRun.id == uuid.UUID(source["ml_run_id"]))
                | (MLRun.artifact_key == document["key"])
            )
            .limit(2)
        )
    ).all()
    bindings = [
        (node.get("output") or {}).get("source", {})
        for node in (results or {}).values()
        if (node.get("output") or {}).get("source", {}).get("ml_run_id") == source["ml_run_id"]
    ]
    if not row:
        if bindings:
            raise MigrationRefusedError("Committed Workflow model reference has no ML row.")
        return False
    if len(row) != 1:
        raise MigrationRefusedError("Workflow model has ambiguous committed references.")
    run = row[0]
    if (
        str(run.id) != source["ml_run_id"]
        or str(run.experiment_id) != source["experiment_id"]
        or str(run.project_id) != document["project_id"]
        or str(run.dataset_id) != source["dataset_id"]
        or run.dataset_sha256 != source["dataset_sha256"]
    ):
        raise MigrationRefusedError("Committed Workflow model ownership differs; retained.")
    meta = json.loads(run.artifact_meta_json or "{}")
    if run.status == MLRunStatus.FAILED and run.artifact_key is None and not bindings and not meta:
        return False  # Only an identity-verified explicit failed, unpublished run.
    if (
        run.artifact_key != document["key"]
        or run.status != MLRunStatus.COMPLETED
        or meta.get("bytes") != document["bytes"]
        or meta.get("sha256") != document["sha256"]
        or len(bindings) != 1
    ):
        raise MigrationRefusedError("Committed Workflow model ownership differs; retained.")
    binding = bindings[0]
    if (
        binding.get("workflow_id") != document["workflow_id"]
        or binding.get("workflow_run_id") != document["run_id"]
        or binding.get("ml_experiment_id") != source["experiment_id"]
        or binding.get("dataset_id") != source["dataset_id"]
        or binding.get("dataset_sha256") != source["dataset_sha256"]
        or binding.get("artifact", {}).get("bytes") != document["bytes"]
        or binding.get("artifact", {}).get("sha256") != document["sha256"]
    ):
        raise MigrationRefusedError("Committed Workflow model source differs; retained.")
    return True


def _verified_file(path: Path, document: dict, owned: dict | None, *, remove: bool) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    if owned is None:
        raise MigrationRefusedError("Unconfirmed created Workflow file retained.")
    with directories(path.parent) as parent:
        if _identity(path.parent.lstat()) != document["parent_identity"]:
            raise MigrationRefusedError("Workflow output parent identity changed.")
        with opened(path, delete=remove) as stream:
            before = os.fstat(stream.fileno())
            if _identity(before) != owned["file_identity"] or before.st_size != document["bytes"]:
                raise MigrationRefusedError(
                    "Workflow output identity or incomplete bytes retained."
                )
            content = stream.read(document["bytes"] + 1)
            after = os.fstat(stream.fileno())
            if (
                len(content) != document["bytes"]
                or hashlib.sha256(content).hexdigest() != document["sha256"]
                or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            ):
                raise MigrationRefusedError("Changed Workflow output bytes retained.")
            if remove:
                unlink_opened(stream, path, parent)
    return True


def _retire(entry: Path, documents: dict[str, bytes]) -> None:
    # A durable retirement marker makes a second process able to finish a
    # metadata-only retirement interrupted between individual unlink calls.
    marker = entry / "retired.json"
    expected_marker = {
        "version": 1,
        "documents": {name: hashlib.sha256(raw).hexdigest() for name, raw in documents.items()},
    }
    write_document(marker, expected_marker)
    _finish_retirement(entry)


def _finish_retirement(entry: Path) -> None:
    marker, raw_marker = _load(entry / "retired.json")
    if (
        set(marker) != {"version", "documents"}
        or type(marker["version"]) is not int
        or marker["version"] != 1
        or not isinstance(marker["documents"], dict)
        or not {"intent.json"} <= set(marker["documents"]) <= {"intent.json", "owned.json"}
        or any(
            not isinstance(s, str) or not _HEX.fullmatch(s) for s in marker["documents"].values()
        )
    ):
        raise MigrationRefusedError("Workflow retirement receipt is unverifiable.")
    if not {p.name for p in entry.iterdir()} <= set(marker["documents"]) | {"retired.json"}:
        raise MigrationRefusedError("Unknown Workflow retirement files retained.")
    with directories(entry) as parent:
        for name, expected in [
            *marker["documents"].items(),
            ("retired.json", hashlib.sha256(raw_marker).hexdigest()),
        ]:
            try:
                with opened(entry / name, delete=True) as stream:
                    raw = stream.read(MAX_DOCUMENT_BYTES + 1)
                    if (
                        not 0 < len(raw) <= MAX_DOCUMENT_BYTES
                        or hashlib.sha256(raw).hexdigest() != expected
                    ):
                        raise MigrationRefusedError("Changed Workflow journal retained.")
                    unlink_opened(stream, entry / name, parent)
            except FileNotFoundError:
                if name == "retired.json":
                    raise
    os.rmdir(long_path(entry))  # Exact empty journal directory only.


def delete_registered(key: str) -> None:
    """Normal rollback compensation uses the same exact persisted ownership."""
    database, storage, journal = _roots()
    entry = journal / hashlib.sha256(key.encode("utf-8")).hexdigest()
    try:
        entry.lstat()
    except FileNotFoundError:
        return  # Never erase an unregistered, pre-existing collision.
    with directories(journal), directories(entry):
        document, owned, _ = _validated(entry, database, storage)
        path = storage.joinpath(*document["key"].split("/"))
        for candidate in [path, path.with_name(path.name + ".pending")]:
            _verified_file(candidate, document, owned, remove=True)


async def reconcile_startup(session_factory) -> dict[str, int]:
    """Call only before requests/workers, under the existing SQLite lifecycle."""
    result = {"registered": 0, "retained_committed": 0, "compensated": 0}
    if not enabled():
        return result
    database, storage, journal = _roots()
    engine = session_factory.kw.get("bind")
    if (
        not isinstance(engine, AsyncEngine)
        or engine.url.get_backend_name() != "sqlite"
        or not engine.url.database
        or _native(Path(engine.url.database)) != database
    ):
        raise MigrationRefusedError(
            "Workflow recovery session is not the configured SQLite database."
        )
    try:
        journal.lstat()
    except FileNotFoundError:
        return result
    with directories(database.parent), directories(storage), directories(journal):
        entries = list(journal.iterdir())
        if len(entries) > MAX_JOURNAL_ENTRIES:
            raise MigrationRefusedError("Workflow output journal exceeds its startup bound.")
        for entry in sorted(entries):
            if not _HEX.fullmatch(entry.name):
                raise MigrationRefusedError("Unknown Workflow journal entry retained.")
            with directories(entry):
                names = {p.name for p in entry.iterdir()}
            if not names:
                os.rmdir(long_path(entry))  # Empty allocation/retirement only; no files erased.
                continue
            if "retired.json" in names:
                _finish_retirement(entry)
                continue
            with directories(entry):
                document, owned, documents = _validated(entry, database, storage)
                # A new independent read must succeed. A failed/unknown query is
                # never interpreted as an absent committed business reference.
                async with session_factory() as session:
                    committed = await _referenced(session, document)
                if _regular_identity(database) != document["database_identity"]:
                    raise MigrationRefusedError(
                        "Workflow recovery database changed during observation."
                    )
                path = storage.joinpath(*document["key"].split("/"))
                target_present = _verified_file(path, document, owned, remove=False)
                pending = path.with_name(path.name + ".pending")
                pending_present = _verified_file(pending, document, owned, remove=False)
                if target_present and pending_present:
                    raise MigrationRefusedError("Ambiguous Workflow output names retained.")
                if committed:
                    if not target_present or pending_present:
                        raise MigrationRefusedError(
                            "Committed Workflow output is missing; retained."
                        )
                    result["retained_committed"] += 1
                else:
                    for candidate, present in [(path, target_present), (pending, pending_present)]:
                        if present:
                            _verified_file(candidate, document, owned, remove=True)
                    result["compensated"] += int(target_present or pending_present)
                result["registered"] += 1
            _retire(entry, documents)
    return result
