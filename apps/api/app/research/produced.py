"""Data-only durable worker receipts in a trusted, exclusively owned stage."""

import hashlib
import json
import os
import re
from pathlib import Path

from app.ml.bounded_writer import MAX_ARTIFACT_BYTES, MAX_RESULT_BYTES
from app.research.contracts import canonical_bytes, logical_hash
from app.research.credential_liveness import process_identity
from app.research.migration import MigrationRefusedError
from app.research.output_handles import directories, lexical, opened, rename_opened


def read_bytes(path: Path, limit: int) -> bytes:
    try:
        with opened(path) as stream:
            content = stream.read(limit + 1)
    except FileNotFoundError:
        raise MigrationRefusedError("Required produced evidence file is missing.") from None
    if not 0 < len(content) <= limit:
        raise MigrationRefusedError("Produced evidence byte bound failed.")
    return content


def sync_file(path: Path) -> None:
    with opened(path, write=True) as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_document(path: Path, document: dict) -> None:
    encoded = canonical_bytes(document)
    temporary = path.with_name(path.name + ".pending")
    if path.exists() or temporary.exists():
        raise MigrationRefusedError("Produced document already exists; no overwrite.")
    with directories(path.parent) as parent:
        with opened(temporary, write=True, create=True) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            rename_opened(stream, path, parent)
        if os.name == "posix":
            os.fsync(parent)


def seal(directory: Path, request: dict) -> None:
    directory = lexical(directory)
    result_path = directory / "result.json"
    result = read_bytes(result_path, MAX_RESULT_BYTES)
    parsed = json.loads(result)
    if parsed.get("status") != "ok":
        return  # A stable error channel is not a produced business result.
    artifact_path = directory / "model.joblib"
    artifacts = []
    required_artifact = (
        request["operation"] == "execute" and parsed.get("result", {}).get("status") == "completed"
    )
    try:
        artifact_path.lstat()
        artifact_present = True
    except FileNotFoundError:
        artifact_present = False
    if required_artifact and not artifact_present:
        raise MigrationRefusedError("Completed ML result has no verified fitted artifact.")
    if artifact_present:
        content = read_bytes(artifact_path, MAX_ARTIFACT_BYTES)
        sync_file(artifact_path)
        artifacts.append(
            {
                "name": "model.joblib",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        if (
            parsed.get("artifact_meta", {}).get("bytes") != len(content)
            or parsed.get("artifact_meta", {}).get("sha256") != artifacts[0]["sha256"]
        ):
            raise MigrationRefusedError("Fitted output differs from the actual artifact metadata.")
    if request["operation"] == "research_workflow":
        if artifacts:
            raise MigrationRefusedError("Workflow cannot publish a top-level ML artifact.")
        artifacts = workflow_artifacts(directory, parsed, request["config"], sync=True)
    status, birth = process_identity(os.getpid())
    if status != "alive" or birth is None:
        raise MigrationRefusedError("Cannot verify the produced worker identity.")
    sync_file(result_path)
    launch_bytes = read_bytes(directory / "launch.json", 65536)
    producer = {"pid": os.getpid(), "birth": birth}
    if load_json(launch_bytes) != {
        "version": 1,
        "intent_hash": request["intent_hash"],
        "producer": producer,
    }:
        raise MigrationRefusedError("Produced worker differs from native launch record.")
    write_document(
        directory / "produced.json",
        {
            "version": 2,
            "intent_hash": request["intent_hash"],
            "operation": request["operation"],
            "dataset_sha256": request["dataset_sha256"],
            "config_hash": logical_hash(request["config"]),
            "result": {
                "name": "result.json",
                "bytes": len(result),
                "sha256": hashlib.sha256(result).hexdigest(),
            },
            "artifacts": artifacts,
            "producer": producer,
            "launch_sha256": hashlib.sha256(launch_bytes).hexdigest(),
        },
    )


def verified(
    directory: Path,
    intent_hash: str,
    operation: str,
    source_sha256: str,
    config: dict,
    *,
    require_launch: bool = True,
) -> tuple[dict, dict, str]:
    encoded = read_bytes(directory / "produced.json", 65536)
    manifest = load_json(encoded)
    keys = {
        "version",
        "intent_hash",
        "operation",
        "dataset_sha256",
        "config_hash",
        "result",
        "artifacts",
        "producer",
    }
    if require_launch:
        keys.add("launch_sha256")
    if (
        set(manifest) != keys
        or type(manifest["version"]) is not int
        or manifest["version"] != (2 if require_launch else 1)
        or manifest["intent_hash"] != intent_hash
        or manifest["operation"] != operation
        or manifest["dataset_sha256"] != source_sha256
        or manifest["config_hash"] != logical_hash(config)
    ):
        raise MigrationRefusedError("Produced result does not belong to the frozen intent.")
    producer = manifest["producer"]
    if (
        not isinstance(producer, dict)
        or set(producer) != {"pid", "birth"}
        or type(producer["pid"]) is not int
        or not 0 < producer["pid"] < 2**31
        or not isinstance(producer["birth"], str)
        or not re.fullmatch(r"win:[1-9][0-9]*|linux:[0-9a-f-]{36}:[0-9]+", producer["birth"])
    ):
        raise MigrationRefusedError("Produced worker identity is unknown.")
    if require_launch:
        launch_bytes = read_bytes(directory / "launch.json", 65536)
        if hashlib.sha256(launch_bytes).hexdigest() != manifest["launch_sha256"] or load_json(
            launch_bytes
        ) != {"version": 1, "intent_hash": intent_hash, "producer": producer}:
            raise MigrationRefusedError("Produced launch binding changed.")
    result = read_bytes(directory / "result.json", MAX_RESULT_BYTES)
    actual = {
        "name": "result.json",
        "bytes": len(result),
        "sha256": hashlib.sha256(result).hexdigest(),
    }
    if manifest["result"] != actual:
        raise MigrationRefusedError("Produced result byte identity changed.")
    response = load_json(result)
    if response.get("version") != 1 or response.get("status") != "ok":
        raise MigrationRefusedError("Produced result channel is not complete.")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > (
        300 if operation == "research_workflow" else 1
    ):
        raise MigrationRefusedError("Produced artifact channel is unknown.")
    if (
        operation == "execute"
        and response.get("result", {}).get("status") == "completed"
        and len(artifacts) != 1
    ):
        raise MigrationRefusedError("Completed ML result has no verified fitted artifact.")
    if operation == "research_workflow":
        if artifacts != workflow_artifacts(directory, response, config):
            raise MigrationRefusedError("Produced Workflow artifact identity changed.")
        # Fixed Workflow objects do not share the single-ML model channel.
        try:
            (directory / "model.joblib").lstat()
        except FileNotFoundError:
            pass
        else:
            raise MigrationRefusedError("Unregistered produced artifact retained.")
    elif artifacts:
        content = read_bytes(directory / "model.joblib", MAX_ARTIFACT_BYTES)
        if artifacts != [
            {
                "name": "model.joblib",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ]:
            raise MigrationRefusedError("Produced artifact identity changed.")
        if response.get("artifact_meta", {}).get("sha256") != artifacts[0][
            "sha256"
        ] or response.get("artifact_meta", {}).get("bytes") != len(content):
            raise MigrationRefusedError("Produced fitted artifact does not match result metadata.")
    else:
        try:
            (directory / "model.joblib").lstat()
        except FileNotFoundError:
            pass
        else:
            raise MigrationRefusedError("Unregistered produced artifact retained.")
    return manifest, response, hashlib.sha256(encoded).hexdigest()


def workflow_artifacts(
    directory: Path, response: dict, config: dict, *, sync: bool = False
) -> list[dict]:
    result = response.get("result")
    if not isinstance(result, dict) or result.get("status") not in {"completed", "failed"}:
        raise MigrationRefusedError("Produced Workflow result channel is unknown.")
    objects = result.get("workflow_objects")
    if not isinstance(objects, list) or len(objects) > 300:
        raise MigrationRefusedError("Produced Workflow object bound failed.")
    actual, seen, total = [], set(), 0
    for item in objects:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "bytes", "sha256"}
            or not isinstance(item["name"], str)
            or not re.fullmatch(
                r"wf-[0-9a-f]{32}-(input\.parquet|provenance\.json|model\.joblib|output\.bin)",
                item["name"],
            )
            or item["name"] in seen
        ):
            raise MigrationRefusedError("Produced Workflow object registration failed.")
        content = read_bytes(directory / item["name"], MAX_ARTIFACT_BYTES)
        total += len(content)
        observed = {
            "name": item["name"],
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if item != observed or total > 300 * MAX_ARTIFACT_BYTES:
            raise MigrationRefusedError("Produced Workflow object byte identity changed.")
        if sync:
            sync_file(directory / item["name"])
        actual.append(observed)
        seen.add(item["name"])
    # Every result reference must name a registered actual byte object. Missing
    # files are failures even if a rewritten result/manifest says completed.
    names = {item["name"]: item for item in actual}
    for record in result.get("dataset_records", []):
        for key in ("staged_name", "metadata_name"):
            if record.get(key) not in names:
                raise MigrationRefusedError("Workflow derived data proof is missing.")
        item = names[record["staged_name"]]
        if item["bytes"] != record.get("bytes") or item["sha256"] != record.get("sha256"):
            raise MigrationRefusedError("Workflow derived data proof changed.")
    for record in result.get("model_records", []):
        if record.get("staged_name") not in names:
            raise MigrationRefusedError("Workflow fitted model proof is missing.")
        item = names[record["staged_name"]]
        if any(
            item[key] != record.get("artifact_meta", {}).get(key) for key in ("bytes", "sha256")
        ):
            raise MigrationRefusedError("Workflow fitted model proof changed.")
    for node in result.get("node_results", {}).values():
        for reference in (node.get("output") or {}).get("artifacts", []):
            if reference.get("staged_name") not in names:
                raise MigrationRefusedError("Workflow output proof is missing.")
            item = names[reference["staged_name"]]
            if any(item[key] != reference.get(key) for key in ("bytes", "sha256")):
                raise MigrationRefusedError("Workflow output proof changed.")
    from app.research.workflow_proof import verify_links

    verify_links(directory, result, config, actual)
    return actual


def verify(
    directory: Path,
    intent_hash: str,
    operation: str,
    source_sha256: str,
    config: dict,
    *,
    require_launch: bool = True,
) -> tuple[dict, dict]:
    manifest, response, _digest = verified(
        directory, intent_hash, operation, source_sha256, config, require_launch=require_launch
    )
    return manifest, response


def load_json(encoded: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MigrationRefusedError("Duplicate produced JSON field.")
            result[key] = value
        return result

    def invalid(_value):
        raise MigrationRefusedError("Nonfinite produced JSON value.")

    result = json.loads(encoded, object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(result, dict):
        raise MigrationRefusedError("Produced JSON must be an object.")
    return result


def launch(directory: Path, request: dict) -> None:
    status, birth = process_identity(os.getpid())
    if status != "alive" or birth is None:
        raise MigrationRefusedError("Cannot verify native worker launch identity.")
    write_document(
        directory / "launch.json",
        {
            "version": 1,
            "intent_hash": request["intent_hash"],
            "producer": {"pid": os.getpid(), "birth": birth},
        },
    )
