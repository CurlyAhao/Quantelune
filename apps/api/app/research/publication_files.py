"""Server-selected immutable target bytes; preserve partial/uncertain ownership."""

import hashlib
import os
import uuid
from contextlib import ExitStack
from pathlib import Path

from app.config import settings
from app.ml.bounded_writer import MAX_ARTIFACT_BYTES
from app.research.contracts import canonical_bytes
from app.research.migration import MigrationRefusedError
from app.research.output_handles import directories, lexical, long_path, opened, rename_opened
from app.research.produced import read_bytes


def targets(document: dict, response: dict, manifest: dict) -> list[dict]:
    """Compute keys from verified preallocated identities, never caller paths."""
    source, kind = document["source"], document["domain_kind"]
    project = uuid.UUID(source["project_id"])
    identity = uuid.UUID(document["domain_id"])
    files = {item["name"]: item for item in manifest["artifacts"]}
    selected = []

    def add(name, key, output_kind, media_type):
        item = files[name]
        selected.append({**item, "object_key": key, "kind": output_kind, "media_type": media_type})

    if kind == "ml" and response["result"].get("status") == "completed":
        origin = document["configuration_source"]
        experiment = uuid.UUID(origin["id"]) if origin else uuid.uuid5(identity, "ml_experiment")
        add(
            "model.joblib",
            f"ml-artifacts/{project}/{experiment}/{identity}.joblib",
            "model",
            "application/octet-stream",
        )
    elif kind == "workflow":
        result = response["result"]
        for record in result["dataset_records"]:
            add(
                record["staged_name"],
                record["object_key"],
                "derived_data",
                "application/vnd.apache.parquet",
            )
            add(record["metadata_name"], record["metadata_key"], "provenance", "application/json")
        for record in result["model_records"]:
            add(record["staged_name"], record["artifact_key"], "model", "application/octet-stream")
        for node in result["node_results"].values():
            for reference in (node.get("output") or {}).get("artifacts", []):
                add(
                    reference["staged_name"],
                    reference["object_key"],
                    "output",
                    reference["media_type"],
                )
    if len({item["object_key"] for item in selected}) != len(selected):
        raise MigrationRefusedError("Publication target ownership is ambiguous.")
    return selected


def _path(key: str) -> Path:
    if settings.storage_backend != "local" or not isinstance(key, str):
        raise MigrationRefusedError("Publication storage is unavailable.")
    parts = key.split("/")
    if (
        not parts
        or any(not part or part in {".", ".."} for part in parts)
        or any("\\" in part or ":" in part or "\0" in part for part in parts)
        or parts[0]
        not in {"research-artifacts", "workflow-derived-data", "workflow-artifacts", "ml-artifacts"}
    ):
        raise MigrationRefusedError("Publication target is outside its fixed namespace.")
    return Path(long_path(lexical(Path(settings.local_storage_path)).joinpath(*parts)))


def write_once(key: str, content: bytes) -> None:
    if not 0 < len(content) <= MAX_ARTIFACT_BYTES:
        raise MigrationRefusedError("Publication object byte bound failed.")
    path = _path(key)
    base = Path(long_path(lexical(Path(settings.local_storage_path))))
    with ExitStack() as stack:
        parent_fd = stack.enter_context(directories(base))
        parent = base
        for name in path.parent.relative_to(base).parts:
            child = parent / name
            try:
                if os.name == "posix":
                    os.mkdir(name, mode=0o700, dir_fd=parent_fd)
                else:
                    os.mkdir(long_path(child))
            except FileExistsError:
                pass
            parent = child
            parent_fd = stack.enter_context(directories(parent))
        # A retained complete target can only be reused after actual exclusive
        # byte/identity verification. A pending write is unknown, never erased.
        try:
            observed = read_bytes(path, MAX_ARTIFACT_BYTES)
        except MigrationRefusedError:
            if Path(long_path(path)).exists():
                raise
        else:
            if observed != content:
                raise MigrationRefusedError("Publication target changed; no overwrite.")
            return
        pending = path.with_name(path.name + ".pending")
        with opened(pending, write=True, create=True) as stream:
            if stream.write(content) != len(content):
                raise OSError("Incomplete publication write.")
            stream.flush()
            os.fsync(stream.fileno())
            rename_opened(stream, path, parent_fd)
        if os.name == "posix":
            os.fsync(parent_fd)


def copy_registered(stage: Path, items: list[dict]) -> None:
    for item in items:
        content = read_bytes(stage / item["name"], MAX_ARTIFACT_BYTES)
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise MigrationRefusedError("Registered production bytes changed before publication.")
        write_once(item["object_key"], content)


def write_root(plan: dict, document: dict) -> tuple[str, bytes, str]:
    data = canonical_bytes(document)
    digest = hashlib.sha256(data).hexdigest()
    owner, root = uuid.UUID(plan["owner_id"]), uuid.UUID(plan["root_id"])
    key = f"research-artifacts/{owner}/{root}/{digest}.json"
    write_once(key, data)
    return key, data, digest
