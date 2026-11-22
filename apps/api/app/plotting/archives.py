"""Read-only plotting snapshots and owned copies of their saved artifacts."""

import json
import uuid

from app.schemas.workspace import PlotRun


def roots(records):
    """Walk package ancestry without accepting arbitrary object-store references."""
    current = records
    for _ in range(8):
        if not isinstance(current, dict):
            break
        yield current
        projects = current.get("projects", [])
        snapshot = projects[0].get("snapshot_json") if projects else None
        snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot or {}
        current = snapshot.get("imported_project", {}).get("records")


def bindings(records):
    found = {}
    for tables in roots(records):
        for row in tables.get("workspace_preview_records", []):
            if row.get("kind") != "plot_run":
                continue
            run = PlotRun.model_validate_json(row["payload_json"])
            if run.status != "completed":
                continue
            for extension, artifact in run.artifacts.items():
                previous = found.get(artifact.object_key)
                value = (extension, artifact)
                if previous and previous != value:
                    raise ValueError("Conflicting plot artifact identity")
                found[artifact.object_key] = value
    return found


def references(records):
    result = {}
    for tables in roots(records):
        projects = tables.get("projects", [])
        raw = projects[0].get("snapshot_json") if projects else None
        snapshot = json.loads(raw) if isinstance(raw, str) else raw or {}
        for old, owned in snapshot.get("imported_project", {}).get("object_references", {}).items():
            result.setdefault(old, owned)
    return result


def mapped(key, mapping):
    seen = set()
    while key in mapping and key not in seen:
        seen.add(key)
        key = mapping[key]
    return key


def owned_objects(records, project, user):
    from app.services.project_files import fail

    mapping = references(records)
    result = {}
    prefix = f"users/{user.id}/projects/{project.id}/"
    for key, (_extension, artifact) in bindings(records).items():
        owned = mapped(key, mapping)
        if not owned.startswith(prefix):
            raise fail("保存的图形文件无法定位，请先恢复原项目。")
        result[owned] = artifact.sha256
    return result


def archived(project):
    from app.services.project_research import archives, timestamp

    tables, mapping = archives(project)
    outer = {"projects": [{"snapshot_json": project.snapshot_json}]}
    objects = references(outer)
    result = []
    for row in tables.get("workspace_preview_records", {}).values():
        if row.get("kind") != "plot_run":
            continue
        value = PlotRun.model_validate_json(row["payload_json"])
        # This is a saved historical result, never a new native execution.
        value.draft_id = uuid.UUID(mapped(str(value.draft_id), mapping))
        value.scope.project_id = project.id
        if value.scope.dataset_id:
            value.scope.dataset_id = uuid.UUID(mapped(str(value.scope.dataset_id), mapping))
        for artifact in value.artifacts.values():
            artifact.object_key = mapped(artifact.object_key, objects)
        if value.status in {"queued", "running", "cancel_requested"}:
            value.status = "interrupted"
            value.error_code = "PLOT_ARCHIVED_INTERRUPTED"
            value.error_message = "这次绘图未完成，可以重新运行保存的代码。"
        result.append(
            {
                "id": row["id"],
                "owner_id": str(project.owner_id),
                "project_id": str(project.id),
                "revision": row["revision"],
                "created_at": timestamp(row.get("created_at") or row.get("updated_at")),
                "payload": value.model_dump(mode="json"),
                "archived": True,
            }
        )
    return result
