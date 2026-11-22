import base64
import hashlib
import json
import uuid
import zipfile

import pytest

from app.exceptions import StatForgeError
from app.routers import workspace_imports
from app.services import project_files


def test_workspace_zip_stages_compatible_json_and_limits_expansion(tmp_path, monkeypatch):
    archive = tmp_path / "input.zip"
    output = tmp_path / "output.json"
    content = b"sample material" * 100
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("folder/a.txt", content)
    workspace_imports.read_zip(str(archive), str(output))
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "files": [{"path": "folder/a.txt", "content": base64.b64encode(content).decode()}]
    }
    monkeypatch.setattr(workspace_imports, "expanded_limit", lambda: len(content) - 1)
    with pytest.raises(StatForgeError):
        workspace_imports.read_zip(str(archive), str(output))


def test_project_package_stages_and_checks_actual_bytes(tmp_path, monkeypatch):
    content = b"portable material" * 100
    sha = hashlib.sha256(content).hexdigest()
    project_id = str(uuid.uuid4())
    manifest = {
        "format": project_files.LEGACY_SCHEMA,
        "project_id": project_id,
        "records": {"projects": [{"id": project_id}]},
        "objects": [{"sha256": sha, "bytes": len(content), "object_key": "one"}],
    }
    payload = json.dumps(manifest).encode()
    package = tmp_path / "project.statforge"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("project.json", payload)
        bundle.writestr("project.sha256", hashlib.sha256(payload).hexdigest())
        bundle.writestr("blobs/" + sha, content)
    staging = tmp_path / "staging"
    staging.mkdir()
    restored, blobs, package_sha = project_files.unpack(package, staging)
    assert restored == manifest
    assert blobs["one"].read_bytes() == content
    assert package_sha == hashlib.sha256(package.read_bytes()).hexdigest()
    monkeypatch.setattr(
        project_files, "expanded_limit", lambda: len(payload) + 64 + len(content) - 1
    )
    with pytest.raises(StatForgeError):
        project_files.unpack(package, staging)


def test_material_secret_scan_handles_utf8_and_chunk_boundaries(tmp_path):
    from app.services.material_service import reject_file_secret

    path = tmp_path / "material"
    path.write_bytes(b"a" * (1024 * 1024 - 1) + "密钥".encode() + b"\xff")
    with pytest.raises(StatForgeError):
        reject_file_secret(path, "密钥")
    reject_file_secret(path, "不存在")
    with pytest.raises(StatForgeError):
        reject_file_secret(path, "钥�")


def test_staging_refuses_low_disk(tmp_path, monkeypatch):
    from app.services import archive_staging

    monkeypatch.setattr(
        archive_staging.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 1})()
    )
    with pytest.raises(ValueError):
        archive_staging.require_disk_space(tmp_path, 1)


async def test_project_package_material_roundtrip(client):
    from tests.conftest import create_project, register_and_login

    await register_and_login(client)
    project = await create_project(client)
    content = "项目材料，保留中文。".encode()
    created = await client.post(
        f"/api/v1/projects/{project['id']}/materials",
        files={"file": ("notes.txt", content)},
    )
    assert created.status_code == 201, created.text
    package = await client.get(f"/api/v1/projects/{project['id']}/package")
    assert package.status_code == 200, package.text
    opened = await client.post(
        "/api/v1/projects/open-file",
        files={"file": ("project.statforge", package.content)},
        data={"as_copy": "true"},
    )
    assert opened.status_code == 200, opened.text
    materials = await client.get(f"/api/v1/projects/{opened.json()['id']}/materials")
    assert materials.status_code == 200, materials.text
    restored = materials.json()[0]
    assert restored["sha256"] == hashlib.sha256(content).hexdigest()
    downloaded = await client.get(f"/api/v1/materials/{restored['id']}/content")
    assert downloaded.content == content
