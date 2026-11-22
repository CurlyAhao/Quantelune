"""Revalidate migration stages and restore a backup into a new isolated clone.

This does not publish a clone, alter a live source, or authorize an upgrade.
"""

import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.research import migration as base

_MAX_RECEIPT = 4 * 1024 * 1024


@dataclass(frozen=True)
class StageInspection:
    phase: str
    directory: Path
    backup: Path
    backup_sha256: str
    source_revision: str | None
    target_revision: str
    candidate: Path | None
    candidate_sha256: str | None
    source_identity: dict


def _receipt(directory: Path, filename: str) -> dict:
    path = base._regular_path(directory / filename)
    if base._native_path(path).stat().st_size > _MAX_RECEIPT:
        raise base.MigrationRefusedError("Migration receipt exceeds the size limit.")
    try:
        result = json.loads(base._native_path(path).read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise base.MigrationRefusedError("Malformed migration receipt.") from exc
    if not isinstance(result, dict):
        raise base.MigrationRefusedError("Invalid migration receipt document.")
    return result


def _markers(directory: Path) -> dict[str, dict]:
    phases = {
        "02-upgrading.json": "UPGRADING",
        "03-verified.json": "CANDIDATE_VERIFIED",
        "04-failed.json": "FAILED",
    }
    markers = {}
    for filename, phase in phases.items():
        try:
            base._native_path(directory / filename).lstat()
        except FileNotFoundError:
            continue
        # lstat sees dangling links; inspect every present marker before phase
        # precedence, including regular/reparse/type, JSON and phase checks.
        marker = _receipt(directory, filename)
        if marker.get("phase") != phase:
            raise base.MigrationRefusedError("Unknown migration phase receipt.")
        markers[filename] = marker
    return markers


def _validated_backup(directory: Path) -> tuple[StageInspection, dict, dict[str, dict]]:
    directory = base._regular_path(directory, directory=True)
    saved = _receipt(directory, "01-backup.json")
    backup = base._regular_path(directory / "backup.sqlite3")
    if (
        saved.get("phase") != "BACKUP_VERIFIED"
        or saved.get("backup_sha256") != base.file_sha256(backup)
        or saved.get("backup_bytes") != base._native_path(backup).stat().st_size
        or not (saved.get("source_revision") is None or isinstance(saved["source_revision"], str))
        or not isinstance(saved.get("target_revision"), str)
    ):
        raise base.MigrationRefusedError("Backup receipt does not match the actual backup.")
    with closing(base._connect(backup)) as connection:
        base._check(connection)
        identity = base._saved_identity(connection, saved)
        if base._inventory(connection) != saved.get("inventory"):
            raise base.MigrationRefusedError("Backup schema or content evidence changed.")
    target = base._target(identity["baseline_revision"], saved["target_revision"])
    return (
        StageInspection(
            "BACKUP_VERIFIED",
            directory,
            backup,
            saved["backup_sha256"],
            saved["source_revision"],
            target,
            None,
            None,
            identity,
        ),
        saved,
        _markers(directory),
    )


def inspect_sqlite_stage(directory: Path) -> StageInspection:
    """Read actual bytes/schema again; marker existence is never sufficient."""
    inspection, saved, markers = _validated_backup(directory)
    directory, backup, target = inspection.directory, inspection.backup, inspection.target_revision
    phase, candidate, candidate_hash = "BACKUP_VERIFIED", None, None
    if "04-failed.json" in markers:
        if "03-verified.json" in markers:
            raise base.MigrationRefusedError("Conflicting verified and failed receipts.")
        phase = "FAILED"
    elif "03-verified.json" in markers:
        if "02-upgrading.json" not in markers:
            raise base.MigrationRefusedError("Verified stage is missing its upgrade receipt.")
        verified = markers["03-verified.json"]
        candidate = base._regular_path(directory / "candidate.sqlite3")
        candidate_hash = base.file_sha256(candidate)
        if (
            verified.get("phase") != "CANDIDATE_VERIFIED"
            or verified.get("backup_sha256") != saved["backup_sha256"]
            or verified.get("candidate_sha256") != candidate_hash
            or verified.get("target_revision") != target
        ):
            raise base.MigrationRefusedError("Candidate receipt does not match actual bytes.")
        with closing(base._connect(candidate)) as connection:
            base._check(connection)
            base._preserved(connection, saved["inventory"])
            if base._revision(connection) != target:
                raise base.MigrationRefusedError("Candidate revision changed.")
        phase = "CANDIDATE_VERIFIED"
    elif "02-upgrading.json" in markers:
        phase = "INTERRUPTED"
    return StageInspection(
        phase,
        directory,
        backup,
        saved["backup_sha256"],
        saved["source_revision"],
        target,
        candidate,
        candidate_hash,
        inspection.source_identity,
    )


def restore_sqlite_backup(directory: Path, destination: Path) -> Path:
    """Create and validate a new old-version clone; never replace a live file."""
    # A corrupt/failed upgraded candidate does not invalidate a separately
    # proven original backup. Restore is allowed even when candidate inspection
    # correctly refuses that candidate or conflicting upgrade markers.
    inspection, receipt, _ = _validated_backup(directory)
    parent = base._regular_path(destination.parent, directory=True)
    destination = parent / destination.name
    if base._native_path(destination).exists() or base._native_path(destination).is_symlink():
        raise base.MigrationRefusedError("Restore directory exists; original files are preserved.")
    required = 2 * base._native_path(inspection.backup).stat().st_size + base._RESERVE_BYTES
    if shutil.disk_usage(base._native_path(parent)).free < required:
        raise base.MigrationRefusedError("Insufficient space for a verified restore clone.")
    base._native_path(destination).mkdir()
    restored = destination / "restored.sqlite3"
    try:
        with closing(base._connect(inspection.backup)) as saved:
            # Recheck at the actual copy boundary, not only at inspection time.
            if base.file_sha256(inspection.backup) != inspection.backup_sha256:
                raise base.MigrationRefusedError("Backup changed before restoration.")
            with closing(sqlite3.connect(base._native_path(restored))) as target:
                saved.backup(target, pages=256, sleep=0.01)
        base._sync_file(restored)
        with closing(base._connect(restored)) as connection:
            base._check(connection)
            base._saved_identity(connection, receipt)
            if base._inventory(connection) != receipt["inventory"]:
                raise base.MigrationRefusedError("Restored content or constraints do not match.")
        if base.file_sha256(inspection.backup) != inspection.backup_sha256:
            raise base.MigrationRefusedError("Backup changed during restoration.")
        base._record(
            destination,
            "01-restored.json",
            {
                "phase": "RESTORE_CLONE_VERIFIED",
                "source_revision": inspection.source_revision,
                "source_identity": inspection.source_identity,
                "backup_sha256": inspection.backup_sha256,
                "restored_sha256": base.file_sha256(restored),
                "live_source_modified": False,
            },
        )
        return restored
    except BaseException as exc:
        try:
            base._record(
                destination,
                "02-failed.json",
                {
                    "phase": "RESTORE_FAILED",
                    "error_type": type(exc).__name__,
                },
            )
        except OSError:
            pass
        raise
